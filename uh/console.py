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
from . import sandbox
from .agent import (
    BudgetExhausted,
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
        "display), then say one line about what it shows. Never answer such a request "
        "with a tutorial about which library to install: the image is displayed in the "
        "conversation, next to your answer. "
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
    def run_text(self, text: str) -> int:
        started = time.perf_counter()
        route_before = self._route_snapshot()
        before = len(self.history)
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
                code = self._run_into(chunks, on_token)
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

            if not content and used_tools:
                # A small model can call a tool and then send an empty message. Ask
                # once more; the synthetic nudge is dropped so it never reaches the
                # stored session.
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
            if produced:
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
                payloads.append(uh_images.Artifact(mime=mime, data=data).as_payload())
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
        self.history.append(
            {"role": "assistant", "content": f"{partial}\n\n{PARTIAL_MARK}"}
        )

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
        self.last_real_model = getattr(self.provider, "last_real_model", None) or self.last_real_model

    def usage_summary(self) -> str:
        """``1234 tok (in 312 / out 922)``, or an explicit "not reported"."""
        if not self.last_usage:
            return "tokens non rapportés"
        usage = self.last_usage
        return (
            f"{usage['total']} tok (in {usage['prompt']} / out {usage['completion']})"
        )

    def usage_line(self) -> str:
        """The dim line printed after every turn: who answered, at what cost."""
        model = self.last_real_model or self.model or "?"
        line = f"{self.provider_kind}  {model}  {self.usage_summary()}"
        if self.last_usage:
            line += f"  total session {self.total_usage['total']} tok"
        return line

    def _run_into(
        self, chunks: list[str], on_token, extra: list[dict] | None = None
    ) -> int:
        """One ``run_turn`` pass over the current history (+ optional nudge)."""
        before = len(self.history)
        base = list(self.history) + list(extra or [])
        try:
            updated = run_turn(
                base,
                self.provider,
                self.toolsets,
                max_iterations=self.max_iterations,
                on_token=on_token if self.stream else None,
                context=self.context,
                on_notice=self._notice,
            )
        except BudgetExhausted as exc:
            self.console.error(f"uh chat: {exc}")
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
            return 1

        for message in updated[before:]:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "assistant" and message.get("tool_calls"):
                for call in message["tool_calls"]:
                    function = call.get("function") or {}
                    self._emit("tool_call", name=function.get("name", ""), args=function.get("arguments"))
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
            remainder = content[len(streamed):] if content.startswith(streamed) else content
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
            raise ValueError(
                f"refused: {resolved} is a system directory; pick a project directory"
            )

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
        self.history[:] = [system_message(self.workspace, role=self.role)] + self._stored_messages(session["id"])
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
        self.store.conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, self.session_id))
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
