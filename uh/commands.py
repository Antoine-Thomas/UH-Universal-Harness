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

from . import sandbox

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
    from . import providers_admin

    parts = args.split()
    action = parts[0].lower() if parts else ""
    name = parts[1].lower() if len(parts) > 1 else ""

    if action in {"add", "test"}:
        if not name:
            return CommandResult(err=f"usage: /provider {action} <name>")
        if action == "test":
            try:
                result = providers_admin.test_connection(name)
            except providers_admin.AdminError as exc:
                return CommandResult(err=f"provider: {exc}")
            if result["ok"]:
                return CommandResult(out=f"{name}: ok (HTTP {result['status']})")
            suffix = f" (HTTP {result['status']})" if result.get("status") else ""
            return CommandResult(err=f"{name}: failed: {result['error_kind']}{suffix}")

        # add: the key never comes from the command line
        try:
            if name in providers_admin.catalog():
                key = providers_admin.read_secret("API key (not echoed): ")
                if not key:
                    return CommandResult(err="provider: nothing entered")
                count = providers_admin.add_key_to(name, key)
                return CommandResult(out=f"{name}: {count} key(s) stored")
            try:
                base_url = input("base URL (https, e.g. https://api.exemple.fr/v1): ").strip()
                model = input("model id: ").strip()
            except EOFError:
                return CommandResult(err="provider: cancelled")
            key = providers_admin.read_secret("API key (optional, not echoed): ")
            created = providers_admin.create_custom(name, base_url, model, key)
        except providers_admin.AdminError as exc:
            return CommandResult(err=f"provider: {exc}")
        return CommandResult(
            out=f"added {created['name']} -> {created['base_url']}"
            f" (model {created['model'] or 'none'})"
        )

    if not name:
        from .providers import all_provider_kinds

        return CommandResult(
            out=f"provider: {session.provider_kind}\n"
            f"known:    {', '.join(all_provider_kinds())}\n"
            "usage:    /provider <name> | add <name> | test <name>"
        )
    try:
        return CommandResult(err=f"uh chat: {session.set_provider(name)}")
    except Exception as exc:  # noqa: BLE001
        return CommandResult(err=f"uh chat: {exc}")


def _cmd_key(_session: "ChatSession", args: str) -> CommandResult:
    """Keys are write-only everywhere, including here: only counts are shown."""
    from . import providers_admin

    parts = args.split()
    action = parts[0].lower() if parts else "list"

    if action == "list":
        lines = ["keys (values are never displayed):"]
        for row in providers_admin.provider_rows():
            state = f"{row['keys']} key(s)" if row["keys"] else "absent"
            lines.append(f"  {row['name']:<12}{state}")
        return CommandResult(out="\n".join(lines))

    if action not in {"add", "remove"}:
        return CommandResult(err="usage: /key list | /key add <provider> | /key remove <provider> <index>")
    if len(parts) < 2:
        return CommandResult(err="key: a provider name is required")

    name = parts[1]
    if action == "add":
        try:
            key = providers_admin.read_secret("API key (not echoed): ")
            if not key:
                return CommandResult(err="key: nothing entered")
            count = providers_admin.add_key_to(name, key)
        except providers_admin.AdminError as exc:
            return CommandResult(err=f"key: {exc}")
        return CommandResult(out=f"{name.lower()}: {count} key(s) stored")

    if len(parts) < 3:
        return CommandResult(err="usage: /key remove <provider> <index>")
    try:
        index = int(parts[2])
    except ValueError:
        return CommandResult(err="key: index must be a number")
    try:
        count = providers_admin.remove_key_index(name, index)
    except providers_admin.AdminError as exc:
        return CommandResult(err=f"key: {exc}")
    return CommandResult(out=f"{name.lower()}: {count} key(s) left")


def _cmd_router(session: "ChatSession", args: str) -> CommandResult:
    from . import providers_admin

    if args.strip().lower() == "reset":
        for _kind, provider in session.chain:
            pool = getattr(provider, "pool", None)
            if pool is not None:
                pool.reset()
                return CommandResult(out="router: cooldowns and circuit breakers reset")
        return CommandResult(out="no router in this session: nothing to reset")

    state = providers_admin.settings()
    rows = [row for row in providers_admin.provider_rows() if row["in_chain"]]
    lines = [
        f"allow_remote: {str(state['allow_remote']).lower()}"
        f"  consent: {'recorded' if state['consent'] else 'none'}"
        f"  strategy: {state['strategy']}",
        "chain:",
    ]
    if not rows:
        lines.append("  (empty: no key stored for a catalogued provider)")
    for row in rows:
        lines.append(
            f"  {row['name']:<12}{row['keys']} key(s)  {row['verified']}  {row['model']}"
        )
    lines.append("usage: /router reset  clears cooldowns in this session")
    return CommandResult(out="\n".join(lines))


def _cmd_local_only(_session: "ChatSession", args: str) -> CommandResult:
    """``on`` = nothing leaves the machine; ``off`` needs the same consent as the web UI."""
    import sys

    from . import providers_admin

    want = args.strip().lower()
    state = providers_admin.settings()
    if want not in {"on", "off"}:
        return CommandResult(
            out=f"allow_remote: {str(state['allow_remote']).lower()}"
            "  (usage: /local-only on|off)"
        )

    if want == "on":
        providers_admin.update_settings(allow_remote=False, consent=False)
        return CommandResult(out="local only: no remote provider will be called")

    if state["consent"]:
        providers_admin.update_settings(allow_remote=True, consent=False)
        return CommandResult(
            out=f"remote enabled (consent recorded {state['consent_date']})"
        )

    if not (sys.stdin is not None and sys.stdin.isatty()):
        return CommandResult(
            err="refusing to enable remote providers without an interactive confirmation"
        )
    print(state["warning"])
    try:
        answer = input("Type 'yes' to confirm: ").strip().lower()
    except EOFError:  # Ctrl-D or a closed pipe: no consent was given
        return CommandResult(out="cancelled: still local only")
    if answer != "yes":
        return CommandResult(out="cancelled: still local only")
    updated = providers_admin.update_settings(allow_remote=True, consent=True)
    return CommandResult(out=f"remote enabled, consent recorded {updated['consent_date']}")


def _session_markdown(session: "ChatSession", label: str) -> str:
    lines = [f"# session {label}", "", f"{len(session.history) - 1} messages", ""]
    for message in session.history[1:]:
        role = str(message.get("role") or "?")
        if role == "system":
            continue
        content = str(message.get("content") or "").strip()
        if role == "tool":
            name = str(message.get("name") or "")
            lines.extend([f"### tool `{name}`", "```text", content[:8000], "```", ""])
            continue
        lines.extend([f"### {role}", content, ""])
        for call in message.get("tool_calls") or []:
            function = (call.get("function") or {}).get("name") or ""
            lines.extend([f"> tool call: `{function}`", ""])
    return "\n".join(lines)


def _session_label(session: "ChatSession") -> str:
    from .store import display_id

    return display_id(session.session_id) if session.session_id else "unsaved"


def _cmd_save(session: "ChatSession", args: str) -> CommandResult:
    from pathlib import Path

    label = _session_label(session)
    raw = args.strip()
    target = Path(raw).expanduser() if raw else Path(session.workspace) / f"session-{label}.md"
    if not target.is_absolute():
        # relative paths land in the workspace: the one directory UH guarantees,
        # and the one the file tools are confined to
        target = Path(session.workspace) / target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_session_markdown(session, label), encoding="utf-8")
    except OSError as exc:
        return CommandResult(err=f"save: {exc}")
    return CommandResult(
        out=f"saved {target}\n"
        "note: tool output may contain sensitive data; UH never writes a key"
    )


def _cmd_export(session: "ChatSession", args: str) -> CommandResult:
    import json
    from pathlib import Path

    fmt = args.strip().lower() or "md"
    if fmt in {"markdown"}:
        fmt = "md"
    if fmt not in {"md", "json"}:
        return CommandResult(err="usage: /export [md|json]")

    label = _session_label(session)
    target = Path(session.workspace) / f"session-{label}.{fmt}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            payload = {
                "session": label,
                "messages": [
                    message for message in session.history[1:] if message.get("role") != "system"
                ],
            }
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            target.write_text(_session_markdown(session, label), encoding="utf-8")
    except OSError as exc:
        return CommandResult(err=f"export: {exc}")
    return CommandResult(
        out=f"exported {target}\n"
        "note: tool output may contain sensitive data; UH never writes a key"
    )


def _cmd_tools(session: "ChatSession", _args: str) -> CommandResult:
    from .toolsets import describe_toolsets

    lines = ["active toolsets:"]
    lines.extend(f"  {line}" for line in describe_toolsets(session.toolsets))
    return CommandResult(out="\n".join(lines))


def _cmd_web(session: "ChatSession", args: str) -> CommandResult:
    """Run the web_search toolset directly - no model call, nothing stored."""
    query = args.strip()
    if not query:
        return CommandResult(err="usage: /web <query>")
    toolset = (session.toolsets or {}).get("web_search")
    if toolset is None:
        from .toolsets.web_search import WebSearchToolset

        toolset = WebSearchToolset()
    result = toolset.call("web_search", {"query": query})
    if result.get("error"):
        return CommandResult(err=f"web: {result['error']}")
    results = result.get("results") or []
    if not results:
        return CommandResult(out=f"web: {result.get('note') or 'no results'}")
    lines = [f"web search: {result.get('query', query)}"]
    for position, item in enumerate(results, 1):
        lines.append(f"  {position}. {item.get('title', '')}")
        lines.append(f"     {item.get('url', '')}")
        snippet = " ".join(str(item.get("snippet") or "").split())
        if snippet:
            lines.append(f"     {snippet[:160]}")
    return CommandResult(out="\n".join(lines))


def _cmd_clear(session: "ChatSession", _args: str) -> CommandResult:
    return CommandResult(err=f"uh chat: {session.clear_history()}")


def _cmd_workspace(session: "ChatSession", args: str) -> CommandResult:
    """Show the workspace and the writable paths, or move both to ``args``."""
    text = args.strip()
    if not text:
        roots = sandbox.allowed_paths(session.workspace)
        return CommandResult(
            out=(
                f"workspace: {session.workspace}\n"
                f"allowed:   {sandbox.display_paths(roots)}\n"
                "usage: /workspace <path>"
            )
        )
    try:
        return CommandResult(out=session.set_workspace(text))
    except ValueError as exc:
        return CommandResult(err=str(exc))


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
    SlashCommand("provider", "Show, switch, add or test a provider", _cmd_provider,
                 args_hint="[name|add <name>|test <name>]", group="Model"),
    SlashCommand("key", "Keys: list, add (masked), remove by index", _cmd_key,
                 args_hint="[list|add <p>|remove <p> <n>]", group="Model"),
    SlashCommand("router", "Router state; reset clears cooldowns", _cmd_router,
                 args_hint="[reset]", group="Model"),
    SlashCommand("local-only", "Nothing leaves the machine; off needs consent", _cmd_local_only,
                 args_hint="[on|off]", group="Model"),
    SlashCommand("tools", "List active toolsets and their tools", _cmd_tools, group="Info"),
    SlashCommand("web", "Search the web directly (no model call)", _cmd_web,
                 args_hint="<query>", group="Info"),
    SlashCommand("clear", "Clear the conversation, keep the session", _cmd_clear, group="Session"),
    SlashCommand(
        "workspace",
        "Show the workspace and the writable paths, or switch workspace",
        _cmd_workspace,
        args_hint="<path>",
        group="Session",
    ),
    SlashCommand("new", "Start a new session", _cmd_new, aliases=("reset",), group="Session"),
    SlashCommand("sessions", "List stored sessions", _cmd_sessions, group="Session"),
    SlashCommand("resume", "Reload a stored session", _cmd_resume,
                 args_hint="<id>", group="Session"),
    SlashCommand("history", "Show the conversation so far", _cmd_history,
                 args_hint="[N]", group="Session"),
    SlashCommand("retry", "Resend the last prompt", _cmd_retry, group="Session"),
    SlashCommand("title", "Set the title of the stored session", _cmd_title,
                 args_hint="<text>", group="Session"),
    SlashCommand("save", "Save this session as Markdown", _cmd_save,
                 args_hint="[file]", group="Session"),
    SlashCommand("export", "Export this session (md or json)", _cmd_export,
                 args_hint="[md|json]", group="Session"),
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
