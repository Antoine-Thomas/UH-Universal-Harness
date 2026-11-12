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
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from . import config as uh_config
from .agent import (
    BudgetExhausted,
    last_assistant_message,
    provider_supports_streaming,
    run_turn,
)
from .providers import Provider, ProviderError
from .store import Store, StoreError, display_id

TOOL_LINE_PREFIX = "· tool "


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
        line = self.status_line(session)
        if self.rich:
            try:
                from rich.console import Console as RichConsole

                RichConsole(file=self.err or sys.stderr, highlight=False).print(
                    f"[dim]{line}[/dim]"
                )
                return
            except Exception:  # pragma: no cover - rich misbehaving: plain fallback
                pass
        self.errout(line)

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
def system_message(workspace: Path) -> dict:
    return {
        "role": "system",
        "content": (
            "You are UH, a local agent harness. Use the provided tools to inspect and modify "
            f"files inside the workspace {workspace}, to run shell commands, and to keep notes "
            "in long-term memory. Prefer the file tools over the shell for file edits. "
            "Answer without calling a tool once the task is done."
        ),
    }


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
    ) -> None:
        self.provider_factory = provider_factory
        self.provider_kind = provider_kind
        self.chain = chain
        self.provider: Provider = chain[0][1] if len(chain) == 1 else _ChainProvider(chain)
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
        # Web front-end hooks: no printing, events instead (see uh/web).
        self.events: Optional[Callable[[dict], None]] = None
        self._stop = threading.Event()
        self.history: list[dict] = [system_message(workspace)]
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
        before = len(self.history)
        self.history.append({"role": "user", "content": text})
        chunks: list[str] = []

        def on_token(delta: str) -> None:
            if self._stop.is_set():
                raise TurnCancelled()
            chunks.append(delta)
            self.console.stream_write(delta)
            self._emit("token", text=delta)

        try:
            code = self._run_into(chunks, on_token)
        except TurnCancelled:
            self.console.errout("uh chat: stopped")
            self._emit("error", message="stopped")
            del self.history[before:]
            return 1
        if code != 0:
            del self.history[before:]
            return code

        content = (last_assistant_message(self.history) or {}).get("content") or ""
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
            code = self._run_into(chunks, on_token, extra=[nudge])
            if code != 0:
                return code
            if len(self.history) > position:
                del self.history[position]
            content = (last_assistant_message(self.history) or {}).get("content") or ""

        self._print_answer(content, chunks)
        self._emit("answer", content=content)
        try:
            self._persist(self.history[before:])
        except StoreError as exc:
            self.console.errout(f"uh chat: cannot record session: {exc}")
        return 0

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
            )
        except BudgetExhausted as exc:
            self.console.errout(f"uh chat: {exc}")
            return 1
        except ProviderError as exc:
            self.console.errout(f"uh chat: provider error: {exc}")
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
        self.history[:] = [system_message(self.workspace)]
        return "history cleared (session kept)"

    def new_session(self) -> str:
        self.history[:] = [system_message(self.workspace)]
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
        self.history[:] = [system_message(self.workspace)] + self._stored_messages(session["id"])
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
        self.chain = chain

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
            try:
                if on_token is not None and provider_supports_streaming(provider):
                    return provider.chat(messages, tools, on_token=on_token)
                return provider.chat(messages, tools)
            except ProviderError as exc:
                first = first or exc
                errors.append(f"{kind}: {exc}")
                if position + 1 < len(self.chain):
                    print(
                        f"fallback: {kind} -> {self.chain[position + 1][0]} (reason: {exc})",
                        file=sys.stderr,
                    )
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
