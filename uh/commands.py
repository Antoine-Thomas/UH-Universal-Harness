"""Slash-command registry (Jalon 4).

One table drives the console: name, aliases, help, argument hint and handler.
`uh/cli.py` no longer pattern-matches ``text.startswith("/model")``; it calls
:func:`dispatch`, which resolves aliases, runs the handler and returns a
:class:`CommandResult` the REPL can print.

The selection of commands (and why the rest of the Hermes registry was left
out) is documented in ``docs/SLASH_COMMANDS.md``.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .console import ChatSession


@dataclass
class CommandResult:
    out: str = ""       # printed on stdout
    err: str = ""       # printed on stderr
    exit: bool = False  # leave the console

    @property
    def empty(self) -> bool:
        return not (self.out or self.err or self.exit)


Handler = Callable[["ChatSession", str], CommandResult]


@dataclass(frozen=True)
class SlashCommand:
    name: str
    help: str
    handler: Handler
    aliases: tuple[str, ...] = ()
    args_hint: str = ""
    group: str = "Session"
    completion: tuple[str, ...] = ()

    @property
    def signature(self) -> str:
        return f"/{self.name} {self.args_hint}".strip()


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #
def _cmd_help(session: "ChatSession", args: str) -> CommandResult:
    target = args.strip().lstrip("/").lower()
    if target:
        command = resolve(target)
        if command is None:
            closest = suggest(target)
            hint = f" - did you mean /{closest} ?" if closest else ""
            return CommandResult(err=f"unknown command /{target}{hint}")
        return CommandResult(out=f"/{command.name}   {command.help}")
    return CommandResult(out=render_help())


def _cmd_model(session: "ChatSession", args: str) -> CommandResult:
    name = args.strip()
    if not name:
        return CommandResult(out=_available_models(session))
    try:
        return CommandResult(err=f"uh chat: {session.set_model(name)}")
    except Exception as exc:  # noqa: BLE001 - provider construction is the only failure
        return CommandResult(err=f"uh chat: {exc}")


def _cmd_provider(session: "ChatSession", args: str) -> CommandResult:
    name = args.strip().lower()
    if not name:
        from .providers import all_provider_kinds

        return CommandResult(
            out=f"provider: {session.provider_kind}\n"
            f"known:    {', '.join(all_provider_kinds())}\n"
            "usage:    /provider <name>"
        )
    try:
        return CommandResult(err=f"uh chat: {session.set_provider(name)}")
    except Exception as exc:  # noqa: BLE001
        return CommandResult(err=f"uh chat: {exc}")


def _cmd_tools(session: "ChatSession", _args: str) -> CommandResult:
    from .toolsets import describe_toolsets

    lines = ["active toolsets:"]
    lines.extend(f"  {line}" for line in describe_toolsets(session.toolsets))
    return CommandResult(out="\n".join(lines))


def _cmd_clear(session: "ChatSession", _args: str) -> CommandResult:
    return CommandResult(err=f"uh chat: {session.clear_history()}")


def _cmd_new(session: "ChatSession", _args: str) -> CommandResult:
    return CommandResult(err=f"uh chat: {session.new_session()}")


def _cmd_sessions(session: "ChatSession", _args: str) -> CommandResult:
    return CommandResult(out=session.sessions_text())


def _cmd_resume(session: "ChatSession", args: str) -> CommandResult:
    token = args.strip()
    if not token:
        return CommandResult(err="usage: /resume <id>  (see /sessions for ids)")
    message = session.resume(token)
    ok = message.startswith("resumed")
    return CommandResult(err=f"uh chat: {message}") if ok else CommandResult(err=message)


def _cmd_config(session: "ChatSession", _args: str) -> CommandResult:
    from . import config as uh_config

    cfg = uh_config.load()
    lines = []
    for key in uh_config.DEFAULTS:
        value = cfg.get(key)
        rendered = "true" if value is True else "false" if value is False else str(value)
        lines.append(f"  {key:<18}{rendered:<26}{cfg.source(key)}")
    return CommandResult(out="\n".join(["configuration:", *lines]))


def _cmd_doctor(_session: "ChatSession", _args: str) -> CommandResult:
    from . import cli

    return CommandResult(out="") if cli.main(["doctor"]) == 0 else CommandResult(err="doctor reported problems")


def _cmd_exit(_session: "ChatSession", _args: str) -> CommandResult:
    return CommandResult(exit=True)


def _cmd_history(session: "ChatSession", args: str) -> CommandResult:
    limit = 20
    if args.strip().isdigit():
        limit = max(1, int(args.strip()))
    return CommandResult(out=session.history_text(limit=limit))


def _cmd_status(session: "ChatSession", _args: str) -> CommandResult:
    return CommandResult(out=session.status())


def _cmd_version(_session: "ChatSession", _args: str) -> CommandResult:
    from . import __version__

    return CommandResult(out=f"uh {__version__}")


def _cmd_title(session: "ChatSession", args: str) -> CommandResult:
    return CommandResult(err=f"uh chat: {session.set_title(args)}")


def _cmd_retry(session: "ChatSession", _args: str) -> CommandResult:
    return CommandResult(err=session.retry())


def _cmd_memory(_session: "ChatSession", _args: str) -> CommandResult:
    from .toolsets.memory import MemoryToolset

    result = MemoryToolset().call("memory_read", {})
    if "error" in result:
        return CommandResult(err=str(result["error"]))
    if not result.get("exists"):
        return CommandResult(err=f"no memory file at {result.get('path')}")
    lines = [f"memory: {result['path']}"]
    for name, body in (result.get("sections") or {}).items():
        lines.append("")
        lines.append(f"## {name}")
        lines.append(body or "(empty)")
    return CommandResult(out="\n".join(lines))


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
REGISTRY: tuple[SlashCommand, ...] = (
    SlashCommand("help", "List commands, or explain one (/help model)", _cmd_help,
                 aliases=("h", "?"), args_hint="[command]", group="Info"),
    SlashCommand("model", "List models, or switch model in this session", _cmd_model,
                 args_hint="[name]", group="Model"),
    SlashCommand("provider", "Show or switch the provider chain", _cmd_provider,
                 args_hint="[name]", group="Model"),
    SlashCommand("tools", "List active toolsets and their tools", _cmd_tools, group="Info"),
    SlashCommand("clear", "Clear the conversation, keep the session", _cmd_clear, group="Session"),
    SlashCommand("new", "Start a new session", _cmd_new, aliases=("reset",), group="Session"),
    SlashCommand("sessions", "List stored sessions", _cmd_sessions, group="Session"),
    SlashCommand("resume", "Reload a stored session", _cmd_resume,
                 args_hint="<id>", group="Session"),
    SlashCommand("history", "Show the conversation so far", _cmd_history,
                 args_hint="[N]", group="Session"),
    SlashCommand("retry", "Resend the last prompt", _cmd_retry, group="Session"),
    SlashCommand("title", "Set the title of the stored session", _cmd_title,
                 args_hint="<text>", group="Session"),
    SlashCommand("status", "Provider, model, session, context size", _cmd_status, group="Info"),
    SlashCommand("config", "Effective configuration and its sources", _cmd_config, group="Info"),
    SlashCommand("doctor", "Environment report", _cmd_doctor, group="Info"),
    SlashCommand("memory", "Show the flat memory file", _cmd_memory, group="Info"),
    SlashCommand("version", "Print the UH version", _cmd_version, aliases=("v",), group="Info"),
    SlashCommand("exit", "Leave the console", _cmd_exit, aliases=("quit", "q"), group="Exit"),
)

REGISTRY_BY_NAME: dict[str, SlashCommand] = {command.name: command for command in REGISTRY}
ALIASES: dict[str, str] = {
    alias: command.name for command in REGISTRY for alias in command.aliases
}
ALIASES.update({command.name: command.name for command in REGISTRY})

GROUP_ORDER = ("Session", "Model", "Info", "Exit")


# --------------------------------------------------------------------------- #
# public helpers
# --------------------------------------------------------------------------- #
def is_command(text: str) -> bool:
    """A leading slash means command intent - never a prompt."""
    stripped = (text or "").lstrip()
    return stripped.startswith("/") and len(stripped) > 1 and not stripped.startswith("//")


def resolve(word: str) -> Optional[SlashCommand]:
    key = (word or "").strip().lstrip("/").lower()
    canonical = ALIASES.get(key)
    return REGISTRY_BY_NAME.get(canonical) if canonical else None


def suggest(word: str) -> Optional[str]:
    key = (word or "").strip().lstrip("/").lower()
    if not key:
        return None
    matches = difflib.get_close_matches(key, list(ALIASES), n=1, cutoff=0.6)
    return ALIASES[matches[0]] if matches else None


def dispatch(session: "ChatSession", line: str) -> CommandResult:
    """Run one slash command line. Unknown commands never become a prompt."""
    stripped = (line or "").strip()
    if not stripped.startswith("/"):
        return CommandResult(err=f"not a command: {stripped!r}")
    head, _, rest = stripped[1:].partition(" ")
    command = resolve(head)
    if command is None:
        closest = suggest(head)
        hint = f" - did you mean /{closest} ?" if closest else " (/help lists commands)"
        return CommandResult(err=f"unknown command /{head}{hint}")
    return command.handler(session, rest.strip())


def render_help(width: int = 0) -> str:
    """Grouped command list; plain text, aligned, no colour."""
    lines = ["commands:"]
    for group in GROUP_ORDER:
        entries = [command for command in REGISTRY if command.group == group]
        if not entries:
            continue
        lines.append("")
        lines.append(f"  {group}")
        for command in entries:
            signature = command.signature
            alias = f" ({', '.join('/' + a for a in command.aliases)})" if command.aliases else ""
            lines.append(f"    {signature:<20}{command.help}{alias}")
    lines.append("")
    lines.append("  anything else is sent to the model")
    return "\n".join(lines)


def completion_candidates(prefix: str) -> list[str]:
    """Names matching ``prefix`` (without the slash), for tab completion."""
    token = (prefix or "").lstrip("/").lower()
    return sorted(name for name in ALIASES if name.startswith(token))


def _available_models(session: "ChatSession") -> str:
    kind = session.provider_kind or "ollama"
    lines = [f"provider: {kind} (current model: {session.model or '?'})"]
    try:
        if kind == "ollama":
            from .providers.ollama import list_models

            models = [entry["name"] for entry in list_models()]
            lines.append("local models:")
            lines.extend(f"  {name}" for name in models) if models else lines.append("  (none)")
        elif kind == "omnirouter":
            from .providers.omnirouter import list_remote_models

            remote = list_remote_models()
            lines.append(f"gateway models: {len(remote)} available")
            lines.extend(f"  {name}" for name in remote[:20])
        else:
            lines.append("configured model: see `uh providers list`")
    except Exception as exc:  # noqa: BLE001 - listing is best effort
        lines.append(f"cannot list models: {exc}")
    lines.append("")
    lines.append("usage: /model <name>")
    return "\n".join(lines)
