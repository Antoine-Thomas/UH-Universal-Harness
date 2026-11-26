"""Console experience for UH: banner, status bar, REPL state (Jalon 4).

Design constraints:

* **rich is optional** (`[ui]` extra). Without it, or off a TTY, everything
  falls back to plain text with exactly the Jalon 3 output: the answer on
  stdout, tool activity and notices on stderr. Nothing breaks when rich is
  absent.
* ``NO_COLOR`` is honoured: no ANSI escape is emitted when it is set, and the
  plain path never emits any.
* The session state lives here (provider, model, history, store), so the slash
  command handlers in :mod:`uh.commands` stay thin and testable.
* ``INTERRUPT_NOTE`` is deliberately French (``[interrompu]``), unlike the rest
  of the CLI: it is a short token the operator recognises at a glance, and it
  was explicitly requested. Do not "normalise" this one string to English.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import config as uh_config
from . import decision as uh_decision
from . import intent as uh_intent
from . import mode_gate as uh_mode_gate
from . import sandbox
from . import skill_prefs as uh_skill_prefs
from . import skills as uh_skills
from .agent import (
    NUDGE_MARK,
    BudgetExhausted,
    _coerce_arguments,
    last_assistant_message,
    provider_supports_streaming,
    run_turn,
)
from .context import ContextManager, default_budget, summarise_with
from .providers import Provider, ProviderError
from .store import Store, StoreError, display_id

TOOL_LINE_PREFIX = "· tool "
# French on purpose: the one user-facing string that stays so (see module docstring).
INTERRUPT_NOTE = "[interrompu] tour annulé (Ctrl+C)"
INTERRUPT_EXIT = 130
#: A turn never ends without something to read (point 1). These sentences are
#: the floor: the iteration limit, a turn that stopped with nothing written
#: (refusal limit, empty answer), and a provider that never answered. Each is
#: emitted as a `notice` event *and* stored as a system message, so a reload shows
#: the same sentence as the live transcript.
BUDGET_NOTICE = (
    "le tour s'est arrêté après {calls} appels au modèle sans réponse finale "
    "(limite max_iterations) ; reformule la demande ou augmente la limite — "
    "le message et les résultats d'outils restent enregistrés"
)
NO_ANSWER_NOTICE = (
    "le tour s'est terminé sans réponse écrite du modèle ; le message, les appels "
    "d'outils et leurs résultats restent dans la conversation"
)
PROVIDER_FAILED_NOTICE = (
    "le modèle n'a pas répondu ({error}) ; le message reste dans la conversation, "
    "relance quand le fournisseur répond"
)


#: Appended to an answer the provider cut mid-stream. In the text rather than in a
#: column on purpose: no schema migration, the reload shows it, and the next turn
#: replays it - so the model knows its previous answer was interrupted.
PARTIAL_MARK = "_[réponse interrompue]_"


class TurnCancelled(Exception):
    """Raised inside the token callback when ``request_stop`` was called."""


# --------------------------------------------------------------------------- #
# rendering helpers
# --------------------------------------------------------------------------- #
def use_color(stream=None) -> bool:
    """True when ANSI colour is acceptable on ``stream``."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    target = stream if stream is not None else sys.stdout
    try:
        return bool(target.isatty())
    except (AttributeError, ValueError):  # pragma: no cover - exotic streams
        return False


def rich_available() -> bool:
    try:
        import rich  # noqa: F401
    except ImportError:
        return False
    return True


# --------------------------------------------------------------------------- #
# UHB header line
# --------------------------------------------------------------------------- #
UHB_UNREACHABLE = "UHB: unreachable"


def uhb_header_line(toolset: object | None) -> Optional[str]:
    """One line about UHB, or ``None`` when the toolset was never built.

    The toolset exists only when a UHB answering ``GET /modes`` was found at
    startup, so ``None`` is a normal state: no line, not a warning. A toolset
    that is present but cannot answer - UHB died between the probe and the call,
    or answered garbage - says so on one line instead of raising: the header
    never breaks the console.
    """
    if toolset is None:
        return None
    status = getattr(toolset, "status", None)
    if not callable(status):
        return None
    try:
        summary = status()
    except Exception:  # noqa: BLE001 - a header line must not crash the session
        return UHB_UNREACHABLE
    if not isinstance(summary, dict) or "error" in summary:
        return UHB_UNREACHABLE
    return _uhb_summary_line(summary)


def _uhb_summary_line(summary: dict) -> str:
    """``UHB: connected (vX.Y.Z) · mode: <mode> · <n> providers``, fields optional.

    Version and mode come from UHB's own ``/status`` answer, never from a
    constant here: the console describes the control plane that is running.
    """
    version = str(summary.get("version") or "").strip()
    mode = str(summary.get("mode") or "").strip()
    providers = summary.get("providers")
    count = len(providers) if isinstance(providers, list) else 0

    head = "UHB: connected"
    if version:
        head += f" ({version if version[:1] in {'v', 'V'} else 'v' + version})"
    parts = [head]
    if mode:
        parts.append(f"mode: {mode}")
    parts.append(f"{count} provider" + ("" if count == 1 else "s"))
    return " · ".join(parts)


class Console:
    """Thin output layer: rich when it helps, plain text otherwise."""

    def __init__(self, stream=None, err=None) -> None:
        self.stream = stream
        self.err = err
        self.colour = use_color(stream)
        self.rich = rich_available() and self.colour

    # -- primitives -------------------------------------------------------- #
    def out(self, text: str) -> None:
        print(text, file=self.stream or sys.stdout)

    def errout(self, text: str) -> None:
        print(text, file=self.err or sys.stderr)

    def stream_write(self, text: str) -> None:
        handle = self.stream or sys.stdout
        handle.write(text)
        handle.flush()

    def end_partial_line(self) -> None:
        """Close a streamed line left open by an interruption (Ctrl+C)."""
        self.stream_write("\n")

    # -- status bar -------------------------------------------------------- #
    @staticmethod
    def status_line(session: "ChatSession") -> str:
        parts = [
            session.provider_kind or "?",
            session.model or "?",
            display_id(session.session_id) if session.session_id else "unsaved",
            f"{len(session.history)} msg",
            f"ctx ~{session.context_estimate()} tok",
        ]
        return " · ".join(parts)

    def show_status(self, session: "ChatSession") -> None:
        self.detail(self.status_line(session))

    def styled(self, text: str, style: str) -> None:
        """Write ``text`` to stderr with a rich style, plain text otherwise."""
        if self.rich:
            try:
                from rich.console import Console as RichConsole
                from rich.markup import escape

                RichConsole(file=self.err or sys.stderr, highlight=False).print(
                    f"[{style}]{escape(text)}[/{style}]"
                )
                return
            except Exception:  # pragma: no cover - rich misbehaving: plain fallback
                pass
        self.errout(text)

    def detail(self, text: str) -> None:
        """Secondary information: header, status bar, notices (grey)."""
        self.styled(text, "dim")

    def error(self, text: str) -> None:
        """Something the user must notice: provider failures, interrupts (red)."""
        self.styled(text, "bold red")

    def show_banner(self, short: bool = True) -> None:
        from . import banner as banner_mod

        text = banner_mod.BANNER_SHORT if short else banner_mod.render()
        handle = (self.err or sys.stderr) if short else (self.stream or sys.stdout)
        handle.write(text if text.endswith("\n") else text + "\n")
        handle.flush()

    # -- tables ------------------------------------------------------------ #
    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        if self.rich and rows:
            try:
                from rich.table import Table

                table = Table(show_header=True, header_style="bold", box=None)
                for header in headers:
                    table.add_column(header, overflow="fold")
                for row in rows:
                    table.add_row(*[str(cell) for cell in row])
                from rich.console import Console as RichConsole

                RichConsole(file=self.stream or sys.stdout).print(table)
                return
            except Exception:  # pragma: no cover - rich misbehaving
                pass
        widths = [len(header) for header in headers]
        for row in rows:
            for index, cell in enumerate(row):
                widths[index] = max(widths[index], len(str(cell)))
        header_line = "  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))
        self.out(header_line)
        for row in rows:
            self.out("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))


# --------------------------------------------------------------------------- #
# session state
# --------------------------------------------------------------------------- #


def intent_routing_enabled() -> bool:
    """``UH_INTENT_ROUTING=1`` turns on automatic capability selection (off by default).

    Off, a turn is offered the whole catalogue exactly as before: the flag exists so
    the two mechanisms can be compared on the same machine and reverted in a
    keystroke, not so that behaviour changes under the operator's fingers.
    """
    # Exactly "1". `true`, `yes`, `on` or any inherited value must NOT switch it on:
    # a flag that turns itself on from a stray environment is not a flag.
    return (os.environ.get("UH_INTENT_ROUTING") or "").strip() == "1"


def _skill_listing() -> list:
    """The **enabled** skills, or an empty list: a broken folder never fails a turn.

    A disabled skill is invisible to the router — it is not proposed, not injected,
    and contributes nothing to how the turn's toolsets are chosen. Turning a skill
    off is a preference about context, never a permission: it grants nothing and
    takes nothing away from UHB.
    """
    try:
        return uh_skill_prefs.enabled_only(uh_skills.list_skills().get("skills") or [])
    except Exception:  # noqa: BLE001 - automatic selection is a convenience
        return []


def system_message(workspace: Path, extra_system: str = "", role: Optional[dict] = None) -> dict:
    """The system prompt, plus whatever the operator attached to this turn.

    ``extra_system`` is a skill's instructions, labelled as such by
    :func:`uh.skills.prompt_block`. It rides on the system message instead of
    being stored as a user message: it is context for this turn, not something
    the user said, and the transcript must stay honest about who said what.

    ``role`` is the session's character & role (P3-14): a paragraph about posture,
    expertise and tone, placed before the skill so the turn's task stays closest to
    the user message. It grants no tool and disables no skill.
    """
    content = (
        "You are UH, a local agent harness. Use the provided tools to inspect and modify "
        f"files inside the workspace {workspace}, to run shell commands, and to keep notes "
        "in long-term memory. Prefer the file tools over the shell for file edits. "
        "Answer without calling a tool once the task is done. "
        "When the user asks for a chart, a plot, a diagram or a map, draw it: call "
        "execute_code and have it save a .png (matplotlib, Agg backend - there is no "
        "display), then say one line about what it shows. Save it in the CURRENT "
        "directory as a bare file name (for example sheep.png): no subfolder (the "
        "sandbox refuses it) and no absolute path (UH collects images from the run "
        "directory only, so a Desktop path is written but never shown, and the user "
        "sees a path instead of a picture). Never answer such a request "
        "with a tutorial about which library to install: the image is displayed in the "
        "conversation, next to your answer. What that drawing IS: an illustration "
        "produced by code, not a photograph - call it « illustration vectorielle, pas "
        "une photo » and never present it as a photo or a generated image. "
        "When the user asks to SEE a real photograph or picture of something - « la "
        "photo d'un chat », « une image de la tour Eiffel » - call `web_image_search` "
        "with the subject only: it returns real images with their licence and author. "
        "Do not draw it with `execute_code`, and do not answer with a text search: a "
        "list of links is not the photo that was asked for. "
        "You can also reach external tools through the configured MCP servers - including "
        "cloud storage such as AWS S3 - and you know where this harness keeps its own "
        "files: the map below is authoritative, never guess a path. "
        "When the user asks how to do something in this interface, give the exact path "
        "(tab, then button) and a link the user can click: [Ouvrir Connexions](/?panel=conn), "
        "or ?panel=keys, local, providers, models, uhb, memory, decision, role, telemetry, data. "
        "Never answer a UI question with 'run this command' - a visible capability is used "
        "from the interface. "
        "If the user asks WHERE a button, a tab or a setting is, NEVER tell them to edit a "
        "file: config.toml and keys.toml are for advanced users only, and editing them is "
        "not an answer to a UI question. Name the tab and the button, and give the deep "
        "link - MCP servers, cloud storage and IDEs are in Connexions (?panel=conn), model "
        "keys in ?panel=keys, a local model in ?panel=local. "
        "When an MCP tool returns an error, read it before acting. If it says that no "
        "session, no console or no kernel is running (for example "
        "mcp__positron__execute_code), tell the user to open the CONSOLE in the IDE, run one "
        "cell, then ask again. Never invent a workaround command, and never edit a "
        "configuration file to compensate. "
        "YOU ARE AN AGENT. When a tool exists for what the user asks - "
        "mcp__positron__execute_code for their data, write_file for a file, terminal for a "
        "command - CALL THE TOOL. Never answer a task with a code block to copy-paste when a "
        "tool can do it: you run the code, the user does not. A code block is the answer "
        "only when the user asks to see code. "
        "One door per subject, and it does not move: Connexions (?panel=conn) is the ONLY "
        "place to connect an MCP server, an IDE or a cloud storage. Data (?panel=data) holds "
        "the local maintenance - backup, skills, installed tools, repair UH - and NEVER a "
        "connection. If the user asks where to connect something, the answer is Connexions "
        "and its button '+ Connecter un MCP'; never send them to Data for a connection, and "
        "never to a file. "
        "If a tool refuses for sandbox reasons, NEVER try to bypass it with `terminal`. "
        "Tell the user: 'Le sandbox refuse : <raison>. Utilise `/workspace <path>` pour "
        "changer de dossier, ou demande-moi d'écrire dans `~/Desktop` / `~/Documents` / "
        "`~/uh-workspace` qui sont autorisés.'"
    )
    # The agent's own map: asked "where are my dreams?", a model that was never
    # told invents a path. Cheap (names and paths, no scan) and generated from the
    # code that owns them, so it cannot lie the way a hand-written doc does.
    # Before `extra_system` on purpose: the skill's instructions are this turn's
    # task, and the closest thing to the user message wins the model's attention.
    try:
        from .system_map import brief as system_map_brief

        block = system_map_brief(workspace=workspace)
    except Exception:  # noqa: BLE001 - a map is context, never a reason to fail a turn
        block = ""
    if block:
        content = f"{content}\n\n{block}"
    from . import roles as uh_roles

    role_block = uh_roles.prompt_block(role)
    if role_block:
        content = f"{content}{role_block}"
    if extra_system.strip():
        content = f"{content}\n\n{extra_system.strip()}"
    return {"role": "system", "content": content}


def _stopped_on_refusal(messages: list[dict]) -> bool:
    """True when `run_turn` stopped because the same call was refused too often.

    The loop only returns a refusal as its last message when the per-call counter
    reached zero - an ordinary tool error leaves the loop running. `run_text` uses
    this to skip its "no text after a tool call, ask once more" retry: re-asking a
    model that just repeated the same forbidden call three times is the budget
    burning on a dead end that `run_turn` already decided to stop.
    """
    if not messages:
        return False
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "tool":
        return False
    try:
        payload = json.loads(last.get("content") or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("type") == "forbidden"


def _without_nudges(messages: list[dict]) -> tuple[list[dict], int]:
    """The turn without its retry instructions, and how many there were.

    The nudge of point 3 is protocol, not conversation: the provider needed it, the
    operator's transcript does not. Dropping it here keeps the thread readable and
    the count feeds the turn log, so "the model was re-asked" stays measurable
    after the fact instead of living only in a test.
    """
    kept: list[dict] = []
    dropped = 0
    for message in messages:
        if (
            isinstance(message, dict)
            and message.get("role") == "system"
            and NUDGE_MARK in str(message.get("content") or "")
        ):
            dropped += 1
            continue
        kept.append(message)
    return kept, dropped


def _model_can_use_tools(provider: str, model: str) -> bool:
    """Whether to send a tool list at all - ``True`` unless it was measured as false.

    An unmeasured model keeps its tools: removing them on a guess would silently
    disarm a chat that worked. Only a measured ``tools: False`` disarms it, which is
    the case this exists for (a local vision model that Ollama refuses to give tools).
    """
    try:
        from . import capabilities as uh_caps

        info = uh_caps.describe(provider, model)
    except Exception:  # noqa: BLE001 - a broken table must not break a turn
        return True
    if info.get("verified") and info.get("tools") is False:
        return False
    return True


def _model_sees_images(provider: str, model: str) -> bool:
    """Whether this model may be sent image bytes - measured, and unknown means no.

    Sending them anyway is not harmless: Ollama answers 400 "Multimodal data
    provided, but model does not support multimodal requests" and the whole turn
    dies. The capability table already knows; this is where it is obeyed.
    """
    try:
        from . import capabilities as uh_caps

        info = uh_caps.describe(provider, model)
    except Exception:  # noqa: BLE001
        return False
    return "image" in (info.get("modalities") or [])


class ChatSession:
    """Everything one console conversation needs, with or without a store."""

    def __init__(
        self,
        *,
        provider_factory: Callable[[str, str], Provider],
        provider_kind: str,
        chain: list[tuple[str, Provider]],
        model: str,
        toolsets: dict,
        workspace: Path,
        cfg: uh_config.Config,
        stream: bool,
        store: Optional[Store],
        max_iterations: int,
        session_id: Optional[str] = None,
        announce: bool = True,
        console: Optional["Console"] = None,
        extra_system: str = "",
        params: Optional[dict] = None,
        role: Optional[dict] = None,
        mode: str = "",
    ) -> None:
        self.provider_factory = provider_factory
        self.provider_kind = provider_kind
        self.chain = chain
        self.provider: Provider = chain[0][1] if len(chain) == 1 else _ChainProvider(chain)
        if isinstance(self.provider, _ChainProvider):
            # The fallback sentence belongs in the transcript: the browser has no
            # stderr, and an answer that changed voice without a word is a lie by
            # omission.
            self.provider.on_notice = self._notice
        self.model = model
        self.toolsets = toolsets
        self.workspace = workspace
        self.cfg = cfg
        self.stream = stream
        self.store = store
        self.max_iterations = max_iterations
        # Last automatic selection, for the decision journal (None when the flag is off).
        self.last_intent: Optional[dict] = None
        #: The UHB mode this turn runs under, when the caller knows it ("chat",
        #: "dev", …). Empty means "unknown": the mode gate then stays open, because
        #: guessing a read-only profile would withhold tools for no reason.
        self.mode: str = str(mode or "")
        #: Tool names the mode withheld from this turn's offer (point 2).
        self.last_withheld: list[str] = []
        #: Tool names this turn was actually offered, for the turn log: the
        #: measurement that says the gate is real, not a sentence in a prompt.
        self.last_offered: list[str] = []
        #: How many times this turn re-asked the model to call a tool instead of
        #: pasting code (point 3). Logged with the turn, never stored as a message.
        self.last_nudges: int = 0
        self.session_id = session_id
        self.announce = announce
        self.console = console or Console()
        # Token accounting: provider counters are cumulative, so a turn takes a
        # diff - a turn that calls the model several times sums all its calls.
        self.last_usage: Optional[dict] = None
        self.total_usage: dict = {"prompt": 0, "completion": 0, "total": 0}
        self.last_real_model: Optional[str] = None
        # Web front-end hooks: no printing, events instead (see uh/web).
        self.events: Optional[Callable[[dict], None]] = None
        self._stop = threading.Event()
        # Pruning happens per provider call, not per turn: a tool loop is exactly
        # when the payload grows past the window. The store keeps everything.
        self.context = ContextManager(
            budget_tokens=default_budget(self.model, cfg),
            summarizer=lambda messages: summarise_with(self.provider, messages),
        )
        self._context_noted = False
        #: The sentence this turn ends with when it produced no text; `run_text`
        #: stores it with the turn so silence is never persisted (point 1).
        self._end_notice: str = ""
        #: Set by `_run_into` when the turn stopped on the refusal limit, so the
        #: "ask once more for text" retry does not re-ask a model that is stuck.
        self._ended_on_refusal: bool = False
        # Kept so a workspace move can rebuild the system prompt without dropping
        # the skill the operator attached to this turn.
        self.extra_system = extra_system
        # Character & role (P3-14): who answers and in what voice. It rides in the
        # system prompt only — no skill, toolset or MCP server is touched.
        self.role = role
        self.history: list[dict] = [system_message(workspace, extra_system, role)]
        # Chat parameters (temperature, top_p, …) for every call this session makes.
        # A third-party provider without set_params keeps working: it simply uses
        # its own defaults.
        self.params: dict = dict(params or {})
        setter = getattr(self.provider, "set_params", None)
        if callable(setter):
            setter(self.params)
        if session_id and store is not None:
            self.history.extend(self._stored_messages(session_id))

    # -- front-end hooks --------------------------------------------------- #
    def request_stop(self) -> None:
        """Ask the running turn to abort at the next streamed token."""
        self._stop.set()

    def clear_stop(self) -> None:
        self._stop.clear()

    def _emit(self, kind: str, **payload) -> None:
        if self.events is not None:
            try:
                self.events({"type": kind, **payload})
            except Exception:  # noqa: BLE001 - a broken sink must not kill the turn
                pass

    def _notice(self, text: str) -> None:
        """One line about how a turn ended. stderr for the console, an event for
        the web transcript: the same sentence in both places, never a silent
        downgrade."""
        self.console.errout(f"! {text}")
        self._emit("notice", message=text)

    def _end_without_answer(self, text: str, *, emit: bool = True) -> None:
        """Give a silent turn a sentence, once, and remember it for the store.

        Point 1: the three exits of `run_turn` - a final answer, the refusal
        limit, ``BudgetExhausted`` - all leave something to read, and the user's
        message is never stored alone. The sentence leaves as the `notice` event
        the browser already draws (and as the console's `!` line), and it is kept
        in ``_end_notice`` so `run_text` stores it as a system message: a reload
        shows exactly what the live transcript showed.

        ``emit=False`` is for a failure the caller has already announced (a
        provider error has its own bubble); the sentence is then only persisted,
        so the transcript still explains the silence after a reload. A turn the
        operator stopped (Ctrl+C, Stop) is the one case that writes nothing: a
        turn cut in the middle is not a turn, and it is rolled back on purpose.
        """
        if self._end_notice:
            return
        self._end_notice = text
        if emit:
            self._notice(text)

    def _intent_skill(self, base: list[dict]) -> list[dict]:
        """Attach the router's skill to *this* call only — never to the stored history.

        ``base`` is already a copy (``_run_into`` builds it with ``list(self.history)``),
        so replacing element 0 adds the skill's instructions to the turn without the
        transcript claiming the model was given them earlier. An operator-attached
        skill always wins: the router fills a gap, it does not overrule a choice.
        """
        skill = str((self.last_intent or {}).get("skill") or "")
        if not skill or not intent_routing_enabled() or self.extra_system.strip():
            return base
        try:
            block = uh_skills.prompt_block(skill) or ""
        except Exception as exc:  # noqa: BLE001 - a skill never breaks a turn
            self.console.detail(f"intent: skill {skill} ignored: {type(exc).__name__}: {exc}")
            return base
        if not block:
            return base
        overlay = list(base)
        if overlay and isinstance(overlay[0], dict) and overlay[0].get("role") == "system":
            overlay[0] = system_message(self.workspace, block, self.role)
        else:
            overlay.insert(0, system_message(self.workspace, block, self.role))
        self.last_intent = {**(self.last_intent or {}), "skill_injected": True}
        return overlay

    def _intent_toolsets(self, base: list[dict]) -> dict:
        """The toolsets this turn is offered — router's choice, then the mode gate.

        Two filters, in this order and never merged:

        1. the intent router says what is *relevant* (`_selected_toolsets`);
        2. the current mode says what is *allowed* at all (`_mode_toolsets`).

        Both only decide what is offered. The model still decides whether to call,
        and UHB still decides whether it may run: offering is not granting.
        """
        return self._mode_toolsets(self._selected_toolsets(base))

    def _mode_toolsets(self, toolsets: dict) -> dict:
        """In a read-only mode, offer only what that mode can allow (point 2).

        `chat` cannot write, so offering `write_file`, `patch`, `terminal` or a
        remote MCP tool asks the model to knock on a door that is already locked:
        one paid provider call, one refusal, and an operator who learns nothing.
        The withheld names are kept for the decision journal, not for the model.
        """
        # `getattr`: a session built by hand (tests, a future caller that skips
        # __init__) has no `mode`, and an unknown mode must never break a turn.
        mode = str(getattr(self, "mode", "") or "")
        if not uh_mode_gate.is_read_only(mode):
            self.last_offered = uh_mode_gate.offered_names(toolsets)
            return toolsets
        kept, withheld = uh_mode_gate.read_only_view(toolsets, mode)
        self.last_withheld = withheld
        self.last_offered = uh_mode_gate.offered_names(kept)
        return kept

    def _selected_toolsets(self, base: list[dict]) -> dict:
        """The toolsets this turn is offered — the router's choice, or all of them.

        Three responsibilities stay separate: the router says what is *relevant*,
        the model decides whether to call, UHB decides whether it may run. A router
        that raises falls back to the unchanged catalogue (never to a wider one), and
        nothing here executes anything.
        """
        self.last_intent = None
        if not intent_routing_enabled():
            return self.toolsets
        text = ""
        for message in reversed(base):
            if isinstance(message, dict) and message.get("role") == "user":
                text = str(message.get("content") or "")
                break
        try:
            selection = uh_intent.select(
                text,
                self.toolsets,
                skills=_skill_listing(),
                history=[m for m in base[-6:] if isinstance(m, dict)],
            )
        except Exception as exc:  # noqa: BLE001 - selection must never break a turn
            self.console.detail(f"intent: {type(exc).__name__}: {exc}")
            return self.toolsets
        chosen = uh_intent.turn_toolsets(self.toolsets, selection, enabled=True)
        self.last_intent = selection.payload()
        line = uh_intent.describe(selection)
        if line and chosen != self.toolsets:
            self._notice(line)
        return chosen

    # -- persistence helpers ----------------------------------------------- #
    def _stored_messages(self, session_id: str) -> list[dict]:
        assert self.store is not None
        skip = {"id", "session_id", "created_at"}
        return [
            {key: value for key, value in message.items() if key not in skip}
            for message in self.store.get_messages(session_id)
        ]

    def _persist(self, new_messages: list[dict]) -> None:
        if self.store is None or not new_messages:
            return
        if self.session_id is None:
            first_user = next(
                (m.get("content") or "" for m in new_messages if m.get("role") == "user"), ""
            )
            self.session_id = self.store.create_session(
                title=_title_from(first_user),
                model=str(self.model or ""),
                provider=self.provider_kind or "",
            )
            if self.announce:
                self.console.errout(f"session {display_id(self.session_id)}")
        for message in new_messages:
            self.store.append_message(self.session_id, message)

    # -- one turn ---------------------------------------------------------- #
    def run_text(self, text: str, images: Optional[List[str]] = None) -> int:
        if images:
            # P1-14: bytes only go to a model measured as able to read them. The
            # warning already told the operator; sending anyway would turn that
            # warning into a 400 from the provider and lose the turn.
            real_model = str(getattr(self.provider, "model", "") or self.model)
            if not _model_sees_images(self.provider_kind, real_model):
                images = None
        started = time.perf_counter()
        route_before = self._route_snapshot()
        before = len(self.history)
        # One sentence per turn, and only if the turn ends without text: cleared
        # here so a previous turn's notice can never be stored twice.
        self._end_notice = ""
        self._ended_on_refusal = False
        self.last_nudges = 0
        # A turn owns its images: the fingerprints reset here, so a retry inside
        # this turn cannot redraw what it already drew, and the next turn starts
        # clean (Codex review on PR #25).
        self._artifact_keys: set[str] = set()
        self.history.append({"role": "user", "content": text})
        chunks: list[str] = []
        usage_before = self._usage_snapshot()

        def on_token(delta: str) -> None:
            if self._stop.is_set():
                raise TurnCancelled()
            chunks.append(delta)
            self.console.stream_write(delta)
            self._emit("token", text=delta)

        produced: list[dict] = []
        try:
            try:
                code = self._run_into(chunks, on_token, images=images)
            except TurnCancelled:
                self.console.errout("uh chat: stopped")
                self._emit("error", message="stopped")
                del self.history[before:]
                return 1
            except KeyboardInterrupt:
                # Ctrl+C lands inside httpx/httpcore while a provider streams: the
                # traceback belongs nowhere on screen, and half an answer must not
                # be recorded. Drop the turn, keep the session alive.
                return self._interrupted(before, chunks)
            if code != 0:
                # The provider failed (HTTP, timeout, budget): the turn leaves the
                # live history, but what was said is still recorded below - a
                # failed turn must not erase the conversation.
                self._keep_partial(chunks)
                produced = list(self.history[before:])
                del self.history[before:]
                return code

            content = self._normalise_answer_images(before)
            used_tools = any(message.get("role") == "tool" for message in self.history[before:])

            if not content and used_tools and not self._ended_on_refusal:
                # A small model can call a tool and then send an empty message. Ask
                # once more; the synthetic nudge is dropped so it never reaches the
                # stored session. Not after a refusal stop: that turn already
                # ended, and asking again is the dead end `run_turn` refused to
                # pay for.
                self.console.errout("uh chat: no text after a tool call, asking once more")
                nudge = {
                    "role": "user",
                    "content": "Answer the original question now, in plain text, without calling a tool.",
                }
                position = len(self.history)
                try:
                    code = self._run_into(chunks, on_token, extra=[nudge])
                except KeyboardInterrupt:
                    return self._interrupted(before, chunks)
                if code != 0:
                    self._keep_partial(chunks)
                    produced = list(self.history[before:])
                    del self.history[before:]
                    return code
                if len(self.history) > position:
                    del self.history[position]
                content = self._normalise_answer_images(before)

            if not str(content or "").strip():
                # Two of the three exits land here: the refusal limit (run_turn
                # returns with tool cards and no text) and a plain empty answer.
                # Neither may leave the operator with a user message and silence.
                self._end_without_answer(NO_ANSWER_NOTICE)
            produced = list(self.history[before:])
            self._print_answer(content, chunks)
            self._record_usage(usage_before)
            self.console.detail(self.usage_line())
            self._note_context()
            self._emit("answer", content=content)
            self._record_decision(started, route_before, produced)
            return 0
        except Exception as exc:  # noqa: BLE001 - an unexpected failure is still a turn
            # A toolset bug, a provider answering something unserialisable, a
            # KeyError in a renderer: none of these is a ProviderError, and before
            # this clause they escaped with `produced` still empty - so the
            # `finally` below persisted nothing and the user's own message
            # disappeared from the store. Report it, keep what was said, exit 1.
            produced = list(self.history[before:])
            self.console.error(f"uh chat: unexpected failure: {type(exc).__name__}: {exc}")
            self._emit("error", message=f"{type(exc).__name__}: {exc}")
            return 1
        finally:
            # Recorded even when the turn failed: the user's message and whatever
            # the model managed to say survive an HTTP error, a timeout or a crash.
            # The closing sentence joins them, so the store never holds a user
            # message followed by silence (point 1).
            if produced:
                if self._end_notice:
                    produced = list(produced) + [{"role": "system", "content": self._end_notice}]
                try:
                    self._persist(produced)
                except StoreError as exc:
                    self.console.error(f"uh chat: cannot record session: {exc}")

    def _normalise_answer_images(self, since: int | None = None) -> str:
        """Images the provider returned: bytes to the interface, text to the model.

        Runs once per finished turn. A remote URL is fetched HERE, server-side,
        with the guards in ``uh.images`` (http(s), no literal local address, size
        cap, MIME from the magic bytes); the browser is never handed a remote URL,
        and a refusal becomes one readable transcript line.

        ``since`` is the history index the turn started at: the images a *tool*
        produced during the turn (``execute_code`` inlining what its snippet wrote)
        join the provider's own blocks, so a chart the model drew is displayed in
        the conversation like any other image.
        """
        message = last_assistant_message(self.history)
        if not message:
            return ""
        original = message.get("content")
        from . import images as uh_images

        try:
            normalised = uh_images.normalise(original)
        except Exception as exc:  # noqa: BLE001 - a broken image never breaks a turn
            self.console.detail(f"images: {type(exc).__name__}: {exc}")
            return str(original or "")
        artifacts: List[Dict[str, Any]] = []
        # One image, one drawing: the empty-answer retry below runs this
        # normalisation a second time over the same window, and re-emitting would
        # draw the same chart twice (Codex review on PR #25).
        seen = self.__dict__.setdefault("_artifact_keys", set())
        for payload in normalised.payloads() + self._tool_images(since):
            key = self._artifact_key(payload)
            if key in seen:
                continue
            seen.add(key)
            artifacts.append(payload)
        if artifacts:
            message["artifacts"] = artifacts
            self._emit("artifacts", images=artifacts)
        note = uh_images.note_for(normalised.refusals)
        if note:
            self._notice(note)
        if isinstance(original, list) or normalised.artifacts or normalised.refusals:
            message["content"] = normalised.text
        return normalised.text

    def _artifact_key(self, payload: Dict[str, Any]) -> str:
        """Stable fingerprint of one image payload: identical bytes draw once."""
        return hashlib.sha1(str(payload.get("data_url", "")).encode("utf-8")).hexdigest()

    def _tool_images(self, since: int | None) -> List[Dict[str, Any]]:
        """Images a tool produced during this turn — the second source of artifacts.

        ``execute_code`` answers with ``images: [{mime, bytes, data_url, …}]`` once
        the snippet wrote files (UHB inlines them; the catalogue carries the switch).
        Only an **inlined data URL** is used: the browser is never handed a
        filesystem path, and nothing is fetched from the network. The MIME comes
        from the bytes, never from the field the tool declared — the same rule the
        provider images follow.
        """
        if since is None:
            return []
        from . import images as uh_images

        payloads: List[Dict[str, Any]] = []
        for message in self.history[since:]:
            if message.get("role") != "tool" or message.get("name") != "execute_code":
                continue
            try:
                result = json.loads(message.get("content") or "{}")
            except (TypeError, ValueError):
                continue
            images = result.get("images") if isinstance(result, dict) else None
            harvested = 0
            for image in images or []:
                if not isinstance(image, dict):
                    continue
                decoded = uh_images.decode_data_url(str(image.get("data_url") or ""))
                if not decoded:
                    continue
                data, _declared = decoded
                mime = uh_images.sniff_mime(data)
                if not mime:
                    continue
                # Un rendu qui sort d'`execute_code` est une ILLUSTRATION : le code a
                # dessine, il n'a pas photographie. L'etiquette voyage avec l'image
                # (sous le cadre, pour l'operateur) et avec le message d'outil (pour
                # le modele), sinon le tour suivant la reappelle « photo ».
                payload = uh_images.Artifact(
                    mime=mime, data=data, note=uh_images.ILLUSTRATION_NOTE
                ).as_payload()
                # Provenance, server-side only: "Poubelle" must delete a real file,
                # and the page must never receive a filesystem path to fetch. It is
                # not sent to the model either - the tool message itself is what the
                # model re-reads, and this key lives in the message's artifacts.
                source = {
                    key: image.get(key)
                    for key in ("path", "relative")
                    if isinstance(image.get(key), str) and image.get(key)
                }
                if isinstance(result.get("run_dir"), str) and result["run_dir"]:
                    source["run_dir"] = result["run_dir"]
                if source:
                    payload["source"] = source
                payloads.append(payload)
                harvested += 1
            if harvested and isinstance(result, dict):
                # The base64 leaves the tool message: it is replayed to the model on
                # the next turn and printed in the tool card, and a 14 KB chart is
                # thousands of useless input tokens. The bytes live in the message
                # artifacts, nowhere else (Codex review on PR #25).
                for image in images or []:
                    if isinstance(image, dict) and image.get("data_url"):
                        image.pop("data_url", None)
                        image["attached"] = "image jointe a la reponse (base64 retire du contexte)"
                        # Ce que le modele LIRA au prochain tour : une illustration,
                        # jamais une photo. Sans ce champ il ne voit qu'un fichier.
                        image["rendering"] = uh_images.ILLUSTRATION_NOTE
                message["content"] = json.dumps(result, ensure_ascii=False)
        return payloads

    def _keep_partial(self, chunks: list[str]) -> None:
        """Record what the model said before the stream died, and mark it.

        Without this the words on screen existed nowhere: a reload lost them, and
        the next turn had no idea an answer had been started.
        """
        partial = "".join(chunks).strip()
        if not partial:
            return
        self.history.append({"role": "assistant", "content": f"{partial}\n\n{PARTIAL_MARK}"})

    def _route_snapshot(self) -> Optional[dict]:
        """The UHB plan on the toolset before a turn (identity, not equality).

        A toolset keeps ``last_route`` across turns, so only a *new* object means
        the model asked UHB during this turn. Comparing contents would attribute
        last turn's plan to this one when the prompt happens to repeat.
        """
        toolset = self.toolsets.get("uhb")
        return getattr(toolset, "last_route", None)

    def _record_decision(
        self, started: float, route_before: Optional[dict], produced: list
    ) -> None:
        """Store what this turn decided, for the Decision view. Never fatal.

        The panel is a reading of past turns: a store that refuses the write must
        not turn an answered question into an error.
        """
        if self.store is None or not self.session_id:
            return
        route = self._route_snapshot()
        if route is route_before:
            route = None
        context_state = self.context.snapshot()
        tool_calls = sum(1 for message in produced if message.get("role") == "tool")
        entry = uh_decision.build_entry(
            provider=self.provider_kind,
            model=self.model,
            served=self.last_real_model or getattr(self.provider, "last_real_model", None),
            usage=self.last_usage,
            duration_ms=int((time.perf_counter() - started) * 1000),
            budget=int(context_state.get("budget") or 0),
            summary=str(context_state.get("summary") or ""),
            summarised_exchanges=int(context_state.get("summarised_exchanges") or 0),
            kept_exchanges=int(context_state.get("kept_exchanges") or 0),
            context_mode=str(context_state.get("mode") or "none"),
            payload_tokens=int(context_state.get("payload_tokens") or 0),
            route=route,
            tool_calls=tool_calls,
            intent=getattr(self, "last_intent", None),
        )
        try:
            uh_decision.record(self.store, self.session_id, entry)
        except Exception:  # noqa: BLE001 - a view must not break a turn
            pass

    def _note_context(self) -> None:
        """Say it once per session when the payload had to be pruned.

        Silence would be wrong here: the model answered from a summary, and that
        is a fact about the answer, not an implementation detail.
        """
        if self._context_noted or not self.context.last_summary:
            return
        self._context_noted = True
        self.console.errout(
            f"uh chat: context pruned to ~{self.context.budget} tokens "
            "(older turns summarised; the session keeps every message)"
        )

    def _interrupted(self, before: int, chunks: list[str]) -> int:
        """Acknowledge a Ctrl+C and drop the half-finished turn.

        The user message and any partial tool traffic are rolled back: a turn
        cut in the middle is not a turn, and replaying it from a truncated
        history is what produces 400s on the next request.
        """
        if chunks:
            self.console.end_partial_line()
        self.console.error(INTERRUPT_NOTE)
        self._emit("error", message="interrupted")
        del self.history[before:]
        return INTERRUPT_EXIT

    # -- token accounting --------------------------------------------------- #
    def _usage_snapshot(self) -> dict:
        """Cumulative counters of the current provider (empty when unsupported)."""
        snapshot = getattr(self.provider, "usage_snapshot", None)
        data = snapshot() if callable(snapshot) else {}
        return data if isinstance(data, dict) else {}

    def _record_usage(self, before: dict) -> None:
        """Fold this turn's counters into the session.

        Interrupted and failed turns never reach here: their tokens are spent but
        the turn is dropped, and billing a turn that produced nothing would be a
        lie. ``last_usage`` is ``None`` when the provider reported no counters,
        which is what makes the console say "tokens non rapportés".
        """
        after = self._usage_snapshot()
        turn = {
            key: max(0, int(after.get(key) or 0) - int(before.get(key) or 0))
            for key in ("prompt", "completion", "total")
        }
        reported = any(turn.values())
        self.last_usage = turn if reported else None
        if reported:
            for key in self.total_usage:
                self.total_usage[key] += turn[key]
        self.last_real_model = (
            getattr(self.provider, "last_real_model", None) or self.last_real_model
        )

    def usage_summary(self) -> str:
        """``1234 tok (in 312 / out 922)``, or an explicit "not reported"."""
        if not self.last_usage:
            return "tokens non rapportés"
        usage = self.last_usage
        return f"{usage['total']} tok (in {usage['prompt']} / out {usage['completion']})"

    def usage_line(self) -> str:
        """The dim line printed after every turn: who answered, at what cost."""
        model = self.last_real_model or self.model or "?"
        line = f"{self.provider_kind}  {model}  {self.usage_summary()}"
        if self.last_usage:
            line += f"  total session {self.total_usage['total']} tok"
        return line

    def _notice_unavailable_capability(self, messages: list[dict]) -> None:
        """Dire qu'une capacite a ete reconnue mais qu'aucun outil ne peut la servir.

        Trois conditions, et aucune n'est << le contenu est vide >> : le routeur a
        reconnu une famille d'action, le catalogue ne portait pas le toolset
        correspondant, et le tour s'est termine sans appel d'outil ni texte. Une
        reponse vide ordinaire ne declenche donc rien.
        """
        missing = list((self.last_intent or {}).get("unavailable") or [])
        if not missing:
            return
        last = None
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "assistant":
                last = message
        if not isinstance(last, dict):
            return
        if last.get("tool_calls") or str(last.get("content") or "").strip():
            return
        # The specific sentence becomes the turn's closing notice, so it is the one
        # stored - a generic "no answer" behind it would say less, twice.
        self._end_without_answer(
            "cette demande necessite l'execution de code, qui n'est pas activee "
            "dans ce mode : aucun graphique ne peut etre produit."
        )

    def _run_into(
        self,
        chunks: list[str],
        on_token,
        extra: list[dict] | None = None,
        images: Optional[List[str]] = None,
    ) -> int:
        """One ``run_turn`` pass over the current history (+ optional nudge)."""
        before = len(self.history)
        base = list(self.history) + list(extra or [])
        toolsets = self._intent_toolsets(base)
        base = self._intent_skill(base)
        try:
            updated = run_turn(
                base,
                self.provider,
                toolsets,
                max_iterations=self.max_iterations,
                on_token=on_token if self.stream else None,
                context=self.context,
                on_notice=self._notice,
                images=images,
                offer_tools=_model_can_use_tools(
                    self.provider_kind, str(getattr(self.provider, "model", "") or self.model)
                ),
            )
            self._notice_unavailable_capability(updated)
            self._ended_on_refusal = _stopped_on_refusal(updated)
            # The retry instructions of point 3 leave the transcript here: the
            # provider needed them, the operator does not. `last_nudges` keeps the
            # fact measurable (turn log) without a system bubble in the thread.
            updated, self.last_nudges = _without_nudges(updated)
        except BudgetExhausted as exc:
            self.console.error(f"uh chat: {exc}")
            # The third exit of `run_turn`: no final answer, so the turn owes the
            # operator a sentence - live (notice event) and in the store (the
            # system message `run_text` appends). Silence here is what the rule
            # of point 1 forbids.
            self._end_without_answer(BUDGET_NOTICE.format(calls=self.max_iterations))
            return 1
        except ProviderError as exc:
            self.console.error(f"uh chat: provider error: {exc}")
            # A provider that dies mid-stream used to produce NO frame at all: the
            # browser kept the words it had already received and then rendered
            # them as a finished answer. Say it died, and say whether the words on
            # screen are real.
            self._emit(
                "error",
                message=str(exc),
                source="provider",
                partial=bool("".join(chunks).strip()),
            )
            # The error bubble is live only; the stored turn needs its own
            # sentence, otherwise a reload shows a user message and a silence.
            self._end_without_answer(
                PROVIDER_FAILED_NOTICE.format(error=type(exc).__name__), emit=False
            )
            return 1

        for message in updated[before:]:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "assistant" and message.get("tool_calls"):
                for call in message["tool_calls"]:
                    function = call.get("function") or {}
                    self._emit(
                        "tool_call",
                        name=function.get("name", ""),
                        args=_coerce_arguments(function.get("arguments")),
                    )
            if message.get("role") == "tool":
                self.console.errout(_tool_activity(message))
                self._emit(
                    "tool_result",
                    name=message.get("name") or "",
                    content=str(message.get("content") or "")[:2000],
                )
        self.history[:] = updated
        return 0

    def _print_answer(self, content: str, chunks: list[str]) -> None:
        streamed = "".join(chunks)
        if not self.stream:
            self.console.out(content if content else "(empty response)")
            return
        if streamed:
            self.console.out("")
            remainder = content[len(streamed) :] if content.startswith(streamed) else content
            if remainder:
                self.console.out(remainder)
            return
        self.console.out(content if content else "(empty response)")

    # -- state shown in the status bar ------------------------------------- #
    def context_estimate(self) -> int:
        """Rough token estimate (~4 characters per token), labelled as such."""
        characters = sum(len(str(message.get("content") or "")) for message in self.history)
        return max(1, characters // 4)

    # -- mutations used by slash commands ---------------------------------- #
    def set_model(self, name: str) -> str:
        self.provider = self.provider_factory(self.provider_kind, name)
        self.model = name
        return f"model set to {name} ({self.provider_kind})"

    def set_provider(self, kind: str) -> str:
        provider = self.provider_factory(kind, self.model)
        self.provider = provider
        self.provider_kind = kind
        self.chain = [(kind, provider)]
        self.model = str(getattr(provider, "model", self.model) or self.model)
        return f"provider set to {kind} (model {self.model or 'default'})"

    def clear_history(self) -> str:
        self.history[:] = [system_message(self.workspace, role=self.role)]
        return "history cleared (session kept)"

    def set_workspace(self, raw: str) -> str:
        """Move the workspace and every filesystem toolset. Raises ``ValueError``.

        ``memory`` is deliberately not moved: ``MEMORY.md`` is UH's own state,
        not project data, and following the workspace would fork the user's
        notes once per project.
        """
        text = (raw or "").strip()
        if not text:
            raise ValueError("usage: /workspace <path>")
        target = Path(text).expanduser()
        if not target.exists():
            raise ValueError(f"no such directory: {target}")
        if not target.is_dir():
            raise ValueError(f"not a directory: {target}")
        resolved = target.resolve()
        if sandbox.is_system_directory(resolved):
            raise ValueError(f"refused: {resolved} is a system directory; pick a project directory")

        self.workspace = resolved
        moved: list[str] = []
        for name, toolset in self.toolsets.items():
            setter = getattr(toolset, "set_workspace", None)
            if callable(setter):
                setter(resolved)
                moved.append(name)
        # The system prompt names the workspace: refresh it, keeping the rest of
        # the conversation so the user does not lose context by moving.
        if self.history and self.history[0].get("role") == "system":
            self.history[0] = system_message(resolved, self.extra_system, self.role)
        roots = sandbox.allowed_paths(resolved)
        lines = [
            f"workspace set to {resolved}",
            f"allowed:   {sandbox.display_paths(roots)}",
        ]
        if moved:
            lines.append(f"moved:     {', '.join(sorted(moved))}")
        return "\n".join(lines)

    def new_session(self) -> str:
        self.history[:] = [system_message(self.workspace, role=self.role)]
        if self.store is None:
            self.session_id = None
            return "history cleared (not saved: --no-save)"
        self.session_id = self.store.create_session(
            title="", model=str(self.model or ""), provider=self.provider_kind or ""
        )
        return f"new session {display_id(self.session_id)}"

    def resume(self, token: str) -> str:
        if self.store is None:
            return "sessions are disabled (--no-save)"
        matches = [
            session
            for session in self.store.list_sessions(limit=10_000)
            if session["id"] == token
            or session["id"].startswith(token)
            or display_id(session["id"]) == token
        ]
        if not matches:
            return f"no session matching '{token}'"
        if len(matches) > 1:
            return f"'{token}' is ambiguous ({len(matches)} sessions); use a longer id"
        session = matches[0]
        self.session_id = session["id"]
        self.history[:] = [system_message(self.workspace, role=self.role)] + self._stored_messages(
            session["id"]
        )
        return f"resumed {display_id(self.session_id)} ({len(self.history) - 1} messages)"

    def sessions_text(self) -> str:
        if self.store is None:
            return "sessions are disabled (--no-save)"
        sessions = self.store.list_sessions()
        if not sessions:
            return "no sessions"
        lines = [f"  {'ID':<11}{'UPDATED':<21}{'PROVIDER':<14}{'MSGS':>5}  TITLE"]
        for session in sessions:
            lines.append(
                f"  {display_id(session['id']):<11}{str(session.get('updated_at'))[:19]:<21}"
                f"{(session.get('provider') or '-')[:13]:<14}"
                f"{session.get('message_count', 0):>5}  {(session.get('title') or '')[:38]}"
            )
        return "\n".join(lines)

    def set_title(self, text: str) -> str:
        if self.store is None or self.session_id is None:
            return "no stored session to title yet (send a message first)"
        title = text.strip()[:120]
        self.store.conn.execute(
            "UPDATE sessions SET title = ? WHERE id = ?", (title, self.session_id)
        )
        self.store.conn.commit()
        return f"title set to '{title}'"

    def retry(self) -> str:
        last_user = None
        for index in range(len(self.history) - 1, -1, -1):
            if self.history[index].get("role") == "user":
                last_user = (index, self.history[index].get("content") or "")
                break
        if last_user is None:
            return "nothing to retry"
        index, text = last_user
        del self.history[index:]
        self.console.errout("uh chat: retrying last prompt")
        self.run_text(text)
        return ""

    def status(self) -> str:
        lines = [
            f"provider: {self.provider_kind or '?'}",
            f"model:    {self.model or '?'}",
            f"served:   {self.last_real_model or '(nothing answered yet)'}",
            f"tokens:   last {self.usage_summary()}, session {self.total_usage['total']} tok",
            f"session:  {display_id(self.session_id) if self.session_id else 'unsaved'}",
            f"messages: {len(self.history)} (including the system prompt)",
            f"context:  ~{self.context_estimate()} tokens (estimate: characters / 4)",
            f"workspace: {self.workspace}",
            f"streaming: {'on' if self.stream else 'off'}",
        ]
        return "\n".join(lines)

    def history_text(self, limit: int = 20) -> str:
        messages = [m for m in self.history if m.get("role") != "system"]
        if not messages:
            return "(no messages)"
        tail = messages[-limit:]
        lines = []
        for message in tail:
            role = message.get("role") or "?"
            name = f"/{message['name']}" if message.get("name") else ""
            body = (message.get("content") or "").replace("\n", " ")
            suffix = " [tool_calls]" if message.get("tool_calls") else ""
            lines.append(f"  {role}{name}: {body[:200]}{suffix}")
        if len(messages) > len(tail):
            lines.insert(0, f"  ... {len(messages) - len(tail)} earlier message(s)")
        return "\n".join(lines)

    def close(self) -> None:
        if self.store is not None:
            self.store.close()


class _ChainProvider(Provider):
    """Minimal in-session chain: first provider that answers wins, traced."""

    name = "chain"

    def __init__(self, chain: list[tuple[str, Provider]]) -> None:
        super().__init__()
        self.chain = chain
        #: Set by the session. A fallback that only reaches stderr is invisible in
        #: the browser: the turn silently answers from another model, and the user
        #: has no way to know why the answer changed voice.
        self.on_notice: Optional[Callable[[str], None]] = None

    def _trace(self, line: str, notice: str = "") -> None:
        print(line, file=sys.stderr)
        if notice and self.on_notice is not None:
            try:
                self.on_notice(notice)
            except Exception:  # noqa: BLE001 - a notice never breaks a turn
                pass

    def set_params(self, params: Optional[dict]) -> None:
        """Apply the session's parameters to every provider in the chain."""
        self.request_params = dict(params or {})
        for _kind, provider in self.chain:
            setter = getattr(provider, "set_params", None)
            if callable(setter):
                setter(self.request_params)

    def usage_snapshot(self) -> dict:
        """Cumulative counters of every provider in the chain (whoever answered)."""
        total = {"prompt": 0, "completion": 0, "total": 0}
        for _kind, provider in self.chain:
            snapshot = getattr(provider, "usage_snapshot", None)
            data = snapshot() if callable(snapshot) else {}
            if not isinstance(data, dict):
                continue
            for key in total:
                total[key] += int(data.get(key) or 0)
        return total

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> dict:
        errors: list[str] = []
        first: Optional[ProviderError] = None
        for position, (kind, provider) in enumerate(self.chain):
            emitted = False

            def counted(delta: str, _sink=on_token) -> None:
                nonlocal emitted
                emitted = True
                if _sink is not None:
                    _sink(delta)

            try:
                if on_token is not None and provider_supports_streaming(provider):
                    result = provider.chat(messages, tools, on_token=counted)
                else:
                    result = provider.chat(messages, tools)
            except ProviderError as exc:
                if emitted:
                    # Half an answer is already on screen: replaying it on the
                    # next provider would duplicate the text. Report, never replay.
                    raise
                if exc.auth_error:
                    # A refused credential (401/402/403) is not a network incident.
                    # Falling back here would hide a bad key and answer - and bill -
                    # from another account without telling anyone. The other 4xx
                    # (400 "model does not support tools", 404 unknown model) do
                    # describe that provider, so the chain continues.
                    raise
                first = first or exc
                errors.append(f"{kind}: {exc}")
                if position + 1 < len(self.chain):
                    following = self.chain[position + 1][0]
                    self._trace(
                        f"fallback: {kind} -> {following} (reason: {exc.reason})",
                        f"Bascule : {kind} n'a pas répondu ({exc.reason}), "
                        f"nouvelle tentative avec {following}.",
                    )
                continue
            # The turn's metrics come from whoever actually answered.
            self.last_real_model = getattr(provider, "last_real_model", None) or getattr(
                provider, "model", None
            )
            self.last_usage = getattr(provider, "last_usage", None)
            return result
        if len(self.chain) == 1 and first is not None:
            raise first
        raise ProviderError("all providers failed: " + "; ".join(errors))


def _tool_activity(message: dict) -> str:
    name = message.get("name") or "?"
    raw = message.get("content") or ""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        payload = {"raw": str(raw)}
    if isinstance(payload, dict) and payload.get("error"):
        return f"{TOOL_LINE_PREFIX}{name}: error: {str(payload['error'])[:200]}"
    return f"{TOOL_LINE_PREFIX}{name}: ok"


def _title_from(text: str, limit: int = 60) -> str:
    return " ".join((text or "").split())[:limit]
