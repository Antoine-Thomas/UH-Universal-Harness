"""UH command line interface.

Jalon 2: streamed ``uh chat``, real ``uh model pull``, a provider registry
(``ollama`` / ``omnirouter``) with a traced fallback chain, and
``uh providers list``. ``uh sessions`` and ``uh config`` remain placeholders.

The CLI is provider-agnostic: no vendor assumption, no API key required on
first run. Local models come from Ollama (``UH_OLLAMA_URL``); a local OmniRoute
gateway is used as a backup when configured (``UH_OMNIROUTER_URL`` or
``~/.omniroute/.env``).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .agent import BudgetExhausted, last_assistant_message, provider_supports_streaming, run_turn
from .providers import PROVIDERS, Provider, ProviderError, available_providers, build_provider
from .providers.ollama import list_models, pull_model
from .providers.omnirouter import (
    DEFAULT_MODEL as DEFAULT_OMNIROUTER_MODEL,
    config_path as omnirouter_config_path,
    is_configured as omnirouter_is_configured,
    list_remote_models,
    resolve_base_url as omnirouter_base_url,
)
from .toolsets import build_toolsets, describe_toolsets
from .toolsets.memory import uh_home

MIN_PYTHON = (3, 11)
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MAX_ITERATIONS = 8

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NOT_IMPLEMENTED = 2


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _ollama_url() -> str:
    return os.environ.get("UH_OLLAMA_URL", OLLAMA_DEFAULT_URL).rstrip("/")


def _run_version(cmd: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    out = (completed.stdout or completed.stderr or "").strip().splitlines()
    return out[0].strip() if out else ""


def _probe_ollama(timeout: float = 1.5) -> tuple[bool, str]:
    """Return (reachable, detail). Never raises: Ollama absence is not an error."""
    url = _ollama_url()
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a hard dependency
        return False, "httpx not importable, cannot probe"

    try:
        response = httpx.get(f"{url}/api/tags", timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - any network/transport failure is "absent"
        return False, f"not reachable at {url} ({type(exc).__name__})"

    if response.status_code != 200:
        return False, f"{url} answered HTTP {response.status_code}"

    names: list[str] = []
    try:
        payload = response.json()
        names = [m.get("name", "?") for m in payload.get("models", []) if isinstance(m, dict)]
    except Exception:  # noqa: BLE001 - a 200 with odd body is still "reachable"
        return True, f"reachable at {url}"
    return True, f"reachable at {url} ({len(names)} model(s))"


def _router_detail() -> tuple[str, str]:
    """Free-router line for ``uh doctor`` (measured: OmniRoute uses a dotenv file)."""
    url = omnirouter_base_url()
    path = omnirouter_config_path()
    if url and path:
        return "OK", f"OmniRouter at {url} (config: {path})"
    if url:
        return "OK", f"OmniRouter at {url} (from environment)"
    if path:
        return "INFO", f"OmniRoute config at {path} but no base URL could be derived"
    return "INFO", "no local OmniRouter config found (optional)"


def _workspace_path(raw: str | None = None) -> tuple[Path, str]:
    """Resolve the workspace: explicit argument, then UH_WORKSPACE, then cwd."""
    value = raw or os.environ.get("UH_WORKSPACE")
    origin = "argument" if raw else ("UH_WORKSPACE" if value else "cwd")
    return (Path(value).expanduser() if value else Path.cwd()).resolve(), origin


def _check_workspace() -> tuple[str, str]:
    path, origin = _workspace_path()
    if not path.is_dir():
        return "FAIL", f"{path} ({origin}) is not a directory"
    try:
        fd, probe = tempfile.mkstemp(prefix=".uh-doctor-", dir=str(path))
        os.close(fd)
        os.unlink(probe)
    except OSError as exc:
        return "FAIL", f"{path} ({origin}) is not writable: {exc}"
    return "OK", f"{path} ({origin}, writable)"


def _check_uh_home() -> tuple[str, str]:
    path = uh_home()
    if path.is_dir():
        return "OK", f"{path} exists"

    missing: list[Path] = []
    probe = path
    while not probe.exists() and probe.parent != probe:
        missing.append(probe)
        probe = probe.parent
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        for created in missing:  # deepest first: roll back what may have been created
            try:
                created.rmdir()
            except OSError:
                break
        return "WARN", f"{path} cannot be created: {exc}"
    return "OK", f"{path} created"


def _human_size(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"  # pragma: no cover


# --------------------------------------------------------------------------- #
# uh doctor
# --------------------------------------------------------------------------- #
def cmd_doctor(_args: argparse.Namespace) -> int:
    rows: list[tuple[str, str, str]] = []  # (status, label, detail)
    failures = 0
    warnings = 0

    # 1. Python
    current = sys.version_info
    py_str = f"{current.major}.{current.minor}.{current.micro}"
    if (current.major, current.minor) >= MIN_PYTHON:
        rows.append(("OK", "python", f"{py_str} (>= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})"))
    else:
        rows.append(
            ("FAIL", "python", f"{py_str} is too old, need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}")
        )
        failures += 1

    # 2. httpx
    try:
        import httpx  # noqa: F401

        rows.append(("OK", "httpx", getattr(httpx, "__version__", "installed")))
    except ImportError:
        rows.append(("FAIL", "httpx", "missing - reinstall with: pip install uh-harness"))
        failures += 1

    # 3. git
    git_path = shutil.which("git")
    if git_path:
        git_version = _run_version(["git", "--version"]) or "installed"
        rows.append(("OK", "git", f"{git_version} ({git_path})"))
    else:
        rows.append(("FAIL", "git", "not found on PATH"))
        failures += 1

    # 4. Ollama (warning only - the harness must stay usable without it)
    reachable, detail = _probe_ollama()
    if reachable:
        rows.append(("OK", "ollama", detail))
    else:
        rows.append(("WARN", "ollama", f"{detail} - optional, start it to use local models"))
        warnings += 1

    # 5. Free router (info only, never blocking)
    router_status, router_detail = _router_detail()
    rows.append((router_status, "router", router_detail))

    # 6. Workspace: the file/terminal tools refuse anything outside it
    workspace_status, workspace_detail = _check_workspace()
    rows.append((workspace_status, "workspace", workspace_detail))
    if workspace_status == "FAIL":
        failures += 1

    # 7. UH home for the flat memory file (created on first write)
    home_status, home_detail = _check_uh_home()
    rows.append((home_status, "uh_home", home_detail))
    if home_status == "WARN":
        warnings += 1

    width = max(len(label) for _, label, _ in rows)
    print(f"UH doctor - uh {__version__}")
    print(f"  host: {platform.system()} {platform.release()} / {platform.machine()}")
    print(f"  python: {sys.executable}")
    print("")
    for status, label, detail in rows:
        print(f"  [{status:<4}] {label:<{width}}  {detail}")
    print("")

    if failures:
        print(f"doctor: {failures} blocking issue(s), {warnings} warning(s)")
        return EXIT_FAILURE
    if warnings:
        print(f"doctor: healthy, {warnings} warning(s)")
    else:
        print("doctor: healthy, no warnings")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# providers
# --------------------------------------------------------------------------- #
def _make_provider(kind: str, model: str) -> Provider:
    """Build the provider named by ``kind``. Seam used by the tests."""
    return build_provider(kind, model)


class _FallbackProvider(Provider):
    """Try providers in order; every switch is traced on stderr.

    Note: if a provider fails *after* emitting some tokens, the retry restarts
    from the same messages, so the caller may see the beginning of the answer
    twice. Switches are rare and the trace makes it visible.
    """

    name = "chain"

    def __init__(self, chain: list[tuple[str, Provider]]) -> None:
        if not chain:
            raise ProviderError("empty provider chain")
        self.chain = chain
        self.index = 0

    @property
    def model(self) -> str:
        return str(getattr(self.chain[self.index][1], "model", "") or "")

    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> dict:
        errors: list[str] = []
        first_error: ProviderError | None = None
        for position in range(self.index, len(self.chain)):
            kind, provider = self.chain[position]
            try:
                if on_token is not None and provider_supports_streaming(provider):
                    message = provider.chat(messages, tools, on_token=on_token)
                else:
                    message = provider.chat(messages, tools)
            except ProviderError as exc:
                first_error = first_error or exc
                errors.append(f"{kind}: {exc}")
                if position + 1 < len(self.chain):
                    print(
                        f"fallback: {kind} -> {self.chain[position + 1][0]} (reason: {exc})",
                        file=sys.stderr,
                    )
                continue
            self.index = position
            return message

        if len(self.chain) == 1 and first_error is not None:
            raise first_error
        raise ProviderError("all providers failed: " + "; ".join(errors))


def _build_chain(requested: str | None, model: str | None) -> tuple[list[tuple[str, Provider]], str]:
    """Return ``[(kind, provider), ...]`` and the model actually selected.

    Explicit ``--provider`` means one provider, no fallback. Otherwise the chain
    is ``ollama`` (with the requested or first local model) then ``omnirouter``
    when it is configured - the switch is always announced on stderr.
    """
    if requested:
        provider = _make_provider(requested, model or "")
        return [(requested, provider)], (model or getattr(provider, "model", "") or "")

    chain: list[tuple[str, Provider]] = []
    reasons: list[str] = []

    ollama_model = model
    if not ollama_model:
        try:
            local = list_models()
        except ProviderError as exc:
            reasons.append(f"ollama: {exc}")
            local = []
        ollama_model = str(local[0]["name"]) if local else None
        if ollama_model is None and not reasons:
            reasons.append("ollama: no local model installed (run: ollama pull <name>)")

    if ollama_model:
        try:
            chain.append(("ollama", _make_provider("ollama", ollama_model)))
        except ProviderError as exc:
            reasons.append(f"ollama: {exc}")

    # Model namespaces differ between providers: an Ollama tag such as
    # "qwen3:8b" is not a valid OmniRoute id, so the backup uses its own model
    # (UH_OMNIROUTER_MODEL or a free auto-combo) unless --provider names it.
    omni_model = os.environ.get("UH_OMNIROUTER_MODEL") or DEFAULT_OMNIROUTER_MODEL
    try:
        chain.append(("omnirouter", _make_provider("omnirouter", omni_model)))
    except ProviderError as exc:
        reasons.append(f"omnirouter: {exc}")

    if not chain:
        raise ProviderError("no usable provider - " + "; ".join(reasons))

    if chain[0][0] == "omnirouter":
        reason = next(
            (item[len("ollama: "):] for item in reasons if item.startswith("ollama: ")),
            "ollama provider unavailable",
        )
        print(f"fallback: ollama -> omnirouter (reason: {reason})", file=sys.stderr)

    return chain, (ollama_model or omni_model)


# --------------------------------------------------------------------------- #
# uh providers
# --------------------------------------------------------------------------- #
def cmd_providers_list(_args: argparse.Namespace) -> int:
    print("providers (runtime: httpx only)")

    url = _ollama_url()
    try:
        local = list_models()
    except ProviderError:
        print(f"  {'ollama':<11} not reachable at {url}")
        print(f"  {'':<11} start it with `ollama serve`")
    else:
        print(f"  {'ollama':<11} reachable at {url} ({len(local)} model(s))")
        default = str(local[0]["name"]) if local else "(none installed)"
        print(f"  {'':<11} default model: {default}")

    base = omnirouter_base_url()
    if not base:
        print(f"  {'omnirouter':<11} not configured (set UH_OMNIROUTER_URL)")
    else:
        try:
            ids = list_remote_models(base)
        except ProviderError as exc:
            print(f"  {'omnirouter':<11} configured at {base}, unreachable: {exc}")
        else:
            print(f"  {'omnirouter':<11} configured at {base} ({len(ids)} model(s))")
            suffix = "" if ids else " (check UH_OMNIROUTER_API_KEY)"
            print(f"  {'':<11} default model: {DEFAULT_OMNIROUTER_MODEL}{suffix}")

    print("")
    print(f"registered: {', '.join(available_providers())}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# uh model pull
# --------------------------------------------------------------------------- #
class _PullProgress:
    """Render ``/api/pull`` events: in-place on a TTY, throttled lines otherwise."""

    def __init__(self, quiet: bool = False, stream=None) -> None:
        self.quiet = quiet
        self.stream = stream or sys.stderr
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.last_status: str | None = None
        self.last_percent = -1
        self.width = 0

    def __call__(self, event: dict) -> None:
        if self.quiet:
            return
        status = str(event.get("status") or "").strip()
        completed = event.get("completed")
        total = event.get("total")

        if total:
            percent = int((completed or 0) * 100 / total) if total else 0
            line = f"{status} {percent:3d}% ({_human_size(completed or 0)}/{_human_size(total)})"
            if self.tty:
                self.stream.write("\r" + line[:200].ljust(self.width))
                self.stream.flush()
                self.width = len(line[:200])
            elif status != self.last_status or percent - self.last_percent >= 10 or percent >= 100:
                print(line, file=self.stream)
                self.last_status = status
                self.last_percent = percent
            return

        if status and status != self.last_status:
            print(status, file=self.stream)
            self.last_status = status

    def finish(self) -> None:
        if not self.quiet and self.tty and self.width:
            self.stream.write("\n")
            self.stream.flush()
            self.width = 0


def cmd_model_pull(args: argparse.Namespace) -> int:
    name = (args.name or "").strip()
    if not name:
        print("uh model pull: a model name is required", file=sys.stderr)
        return EXIT_FAILURE

    if args.interactive and not args.yes:
        print(
            "uh model pull: the size of a remote model is unknown before download "
            "(Ollama answers 404 on /api/show for a non-local model); this may take a while.",
            file=sys.stderr,
        )
        if not sys.stdin or not sys.stdin.isatty():
            print("uh model pull: --interactive needs a TTY; aborted", file=sys.stderr)
            return EXIT_FAILURE
        answer = input(f"pull {name}? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("uh model pull: cancelled", file=sys.stderr)
            return EXIT_FAILURE
    elif not args.quiet:
        print(f"uh model pull: {name} (size unknown before download)", file=sys.stderr)

    progress = _PullProgress(quiet=args.quiet)
    try:
        pull_model(name, progress)
    except ProviderError as exc:
        progress.finish()
        print(f"uh model pull: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    progress.finish()
    print(f"pulled {name}", file=sys.stderr)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# uh chat
# --------------------------------------------------------------------------- #
def _system_message(workspace: Path) -> dict:
    return {
        "role": "system",
        "content": (
            "You are UH, a local agent harness. Use the provided tools to inspect and modify "
            f"files inside the workspace {workspace}, to run shell commands, and to keep notes "
            "in long-term memory. Prefer the file tools over the shell for file edits. "
            "Answer without calling a tool once the task is done."
        ),
    }


def _tool_activity(message: dict) -> str:
    name = message.get("name") or "?"
    raw = message.get("content") or ""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        payload = {"raw": str(raw)}
    if isinstance(payload, dict) and payload.get("error"):
        return f"· tool {name}: error: {str(payload['error'])[:200]}"
    return f"· tool {name}: ok"


def _turn(
    history: list[dict],
    text: str,
    provider: Provider,
    toolsets: dict,
    max_iterations: int,
    stream: bool = True,
) -> int:
    """Run one user turn, print the answer, update ``history`` in place.

    Streaming prints deltas to stdout as they arrive; tool activity goes to
    stderr so stdout stays scriptable.
    """
    before = len(history)
    history.append({"role": "user", "content": text})

    chunks: list[str] = []

    def on_token(delta: str) -> None:
        chunks.append(delta)
        print(delta, end="", flush=True)

    try:
        updated = run_turn(
            history,
            provider,
            toolsets,
            max_iterations=max_iterations,
            on_token=on_token if stream else None,
        )
    except BudgetExhausted as exc:
        print(f"uh chat: {exc}", file=sys.stderr)
        del history[before:]
        return EXIT_FAILURE
    except ProviderError as exc:
        print(f"uh chat: provider error: {exc}", file=sys.stderr)
        del history[before:]
        return EXIT_FAILURE

    for message in updated[before:]:
        if isinstance(message, dict) and message.get("role") == "tool":
            print(_tool_activity(message), file=sys.stderr)

    history[:] = updated
    final = last_assistant_message(updated)
    content = (final or {}).get("content") or ""
    streamed = "".join(chunks)

    if stream:
        if streamed:
            print()
            if content and content != streamed:
                print(content)
        elif content:
            print(content)
        else:
            print("(empty response)")
    else:
        print(content if content else "(empty response)")
    return EXIT_OK


def cmd_chat(args: argparse.Namespace) -> int:
    workspace, _origin = _workspace_path(args.workspace)
    if not workspace.is_dir():
        print(f"uh chat: workspace not found: {workspace}", file=sys.stderr)
        return EXIT_FAILURE
    os.environ["UH_WORKSPACE"] = str(workspace)

    try:
        chain, model = _build_chain(args.provider, args.model)
    except ProviderError as exc:
        print(f"uh chat: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    provider: Provider = _FallbackProvider(chain) if len(chain) > 1 else chain[0][1]
    stream = not args.no_stream
    toolsets = build_toolsets(workspace)
    max_iterations = max(1, int(args.max_iterations or DEFAULT_MAX_ITERATIONS))
    history: list[dict] = [_system_message(workspace)]

    interactive = not args.once and sys.stdin is not None and sys.stdin.isatty()
    if not interactive:
        line = sys.stdin.readline() if sys.stdin is not None else ""
        text = (line or "").strip()
        if not text:
            return EXIT_OK
        return _turn(history, text, provider, toolsets, max_iterations, stream)

    label = " + ".join(kind for kind, _ in chain)
    mode = "stream" if stream else "blocking"
    print(f"uh chat - model {model}, provider {label}, {mode}, workspace {workspace}", file=sys.stderr)
    print("commands: /exit, /model <name>, /tools, /clear", file=sys.stderr)
    while True:
        try:
            line = input("you> ")
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            return EXIT_OK
        text = line.strip()
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            return EXIT_OK
        if text == "/clear":
            history[:] = [_system_message(workspace)]
            print("uh chat: history cleared", file=sys.stderr)
            continue
        if text == "/tools":
            print("active toolsets:", file=sys.stderr)
            for toolset_line in describe_toolsets(toolsets):
                print(f"  {toolset_line}", file=sys.stderr)
            continue
        if text.startswith("/model"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                print(f"uh chat: current model {model}", file=sys.stderr)
                continue
            kind = args.provider or chain[0][0]
            try:
                provider = _make_provider(kind, parts[1].strip())
            except ProviderError as exc:
                print(f"uh chat: {exc}", file=sys.stderr)
                continue
            model = parts[1].strip()
            print(f"uh chat: model set to {model} ({kind})", file=sys.stderr)
            continue
        _turn(history, text, provider, toolsets, max_iterations, stream)


# --------------------------------------------------------------------------- #
# uh model
# --------------------------------------------------------------------------- #
def cmd_model_list(_args: argparse.Namespace) -> int:
    try:
        models = list_models()
    except ProviderError as exc:
        print(f"uh model list: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"Ollama models at {_ollama_url()}")
    print("")
    if not models:
        print("no model installed - run: ollama pull llama3.2:3b")
        return EXIT_OK

    name_width = max(24, max(len(model["name"]) for model in models) + 2)
    print(f"  {'NAME':<{name_width}}{'SIZE':>10}   MODIFIED")
    for model in models:
        modified = str(model["modified_at"])[:19].replace("T", " ")
        print(f"  {model['name']:<{name_width}}{_human_size(model['size']):>10}   {modified}")
    print("")
    print(f"{len(models)} model(s)")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# placeholders
# --------------------------------------------------------------------------- #
def _not_implemented(command: str, milestone: str) -> int:
    print(
        f"{command}: Not implemented in Jalon 0 - planned in {milestone}.",
        file=sys.stderr,
    )
    return EXIT_NOT_IMPLEMENTED


def cmd_sessions(_args: argparse.Namespace) -> int:
    return _not_implemented("uh sessions", "Jalon 3 (persistence and config)")


def cmd_config(_args: argparse.Namespace) -> int:
    return _not_implemented("uh config", "Jalon 3 (persistence and config)")


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uh",
        description="Universal Harness - provider-agnostic agent harness CLI.",
        epilog=(
            "Universal CLI: works with any local or free provider "
            "(Ollama, OmniRouter). No API key needed to start."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"uh {__version__}",
        help="print the UH version and exit",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="print tracebacks instead of short errors",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("doctor", help="check the local environment (python, httpx, git, ollama)")

    chat = sub.add_parser("chat", help="conversation loop with file/terminal/memory tools")
    chat.add_argument("--model", help="model name (default: first model found in Ollama)")
    chat.add_argument(
        "--provider",
        metavar="<kind>",
        help=(
            f"force one provider ({', '.join(available_providers())}); "
            "default: ollama, then omnirouter when configured"
        ),
    )
    chat.add_argument("--workspace", help="workspace directory for the file/terminal tools")
    chat.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=f"max provider calls per turn (default: {DEFAULT_MAX_ITERATIONS})",
    )
    chat.add_argument(
        "--once",
        action="store_true",
        help="read one line from stdin, answer, exit (implicit when stdin is not a TTY)",
    )
    chat.add_argument(
        "--no-stream",
        action="store_true",
        help="wait for the complete answer instead of streaming tokens",
    )

    model = sub.add_parser("model", help="manage local models")
    model_sub = model.add_subparsers(dest="model_command", metavar="<action>")
    model_sub.add_parser("list", help="list models served by Ollama")
    pull = model_sub.add_parser("pull", help="download a model from the Ollama registry")
    pull.add_argument("name", help="model name, e.g. llama3.2:3b")
    pull.add_argument("--quiet", action="store_true", help="no progress, only a final summary")
    pull.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    pull.add_argument(
        "--interactive",
        action="store_true",
        help="ask for confirmation before downloading (size is unknown beforehand)",
    )

    providers = sub.add_parser("providers", help="list registered providers and their state")
    providers_sub = providers.add_subparsers(dest="providers_command", metavar="<action>")
    providers_sub.add_parser("list", help="show providers and their status")

    sub.add_parser("sessions", help="manage sessions (Jalon 3)")
    sub.add_parser("config", help="read or write UH configuration (Jalon 3)")

    # keep direct handles so a missing action can print the right help
    parser._subparsers_by_name = sub._name_parser_map  # type: ignore[attr-defined]
    return parser


def _sub_help(parser: argparse.ArgumentParser, name: str) -> int:
    parser._subparsers_by_name[name].print_help()  # type: ignore[attr-defined]
    return EXIT_NOT_IMPLEMENTED


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return EXIT_OK

    try:
        if args.command == "doctor":
            return cmd_doctor(args)
        if args.command == "chat":
            return cmd_chat(args)
        if args.command == "model":
            if args.model_command == "list":
                return cmd_model_list(args)
            if args.model_command == "pull":
                return cmd_model_pull(args)
            return _sub_help(parser, "model")
        if args.command == "providers":
            if args.providers_command == "list":
                return cmd_providers_list(args)
            return _sub_help(parser, "providers")
        if args.command == "sessions":
            return cmd_sessions(args)
        if args.command == "config":
            return cmd_config(args)
    except Exception as exc:  # noqa: BLE001 - last-resort guard: no traceback by default
        if getattr(args, "debug", False):
            raise
        print(f"uh: unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    parser.print_help()
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":
    raise SystemExit(main())
