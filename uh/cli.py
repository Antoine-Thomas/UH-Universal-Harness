"""UH command line interface.

Jalon 1: ``uh chat`` (real agent loop) and ``uh model list`` (real Ollama
discovery). ``uh sessions``, ``uh config`` and ``uh model pull`` remain
placeholders that exit 2 and name their milestone.

The CLI is provider-agnostic on purpose: no vendor assumption, no API key
required on first run. Local models are discovered through Ollama
(``http://127.0.0.1:11434``, override with ``UH_OLLAMA_URL``).
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

from . import __version__
from .agent import BudgetExhausted, last_assistant_message, run_turn
from .providers import Provider, ProviderError
from .providers.ollama import OllamaProvider, list_models
from .toolsets import build_toolsets, describe_toolsets
from .toolsets.memory import uh_home

MIN_PYTHON = (3, 11)
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MAX_ITERATIONS = 8

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NOT_IMPLEMENTED = 2

# Where the local free-router config may live (OmniRouter / Nemotron style).
OMNIROUTER_CONFIG_NAMES = ("config.json", "config.yaml", "config.yml", "config.toml")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _ollama_url() -> str:
    return os.environ.get("UH_OLLAMA_URL", OLLAMA_DEFAULT_URL).rstrip("/")


def _omnirouter_candidates() -> list[Path]:
    """Likely locations of a local free-router config file (read-only probing)."""
    candidates: list[Path] = []

    for env_var in ("UH_OMNIROUTER_CONFIG", "OMNIROUTER_CONFIG", "OMNI_ROUTER_CONFIG"):
        raw = os.environ.get(env_var)
        if raw:
            candidates.append(Path(raw).expanduser())

    home = Path.home()
    candidates.append(home / ".omnirouter")
    candidates.append(home / ".config" / "omnirouter")
    candidates.append(home / ".omnirouter.json")

    if os.name == "nt":
        for env_var in ("APPDATA", "LOCALAPPDATA"):
            base = os.environ.get(env_var)
            if base:
                candidates.append(Path(base) / "omnirouter")
    else:
        candidates.append(Path("/etc/omnirouter"))

    return candidates


def _find_omnirouter_config() -> Path | None:
    for candidate in _omnirouter_candidates():
        if candidate.is_file():
            return candidate
        if candidate.is_dir():
            for name in OMNIROUTER_CONFIG_NAMES:
                nested = candidate / name
                if nested.is_file():
                    return nested
    return None


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

    # 5. Optional local free-router config (info only)
    router_config = _find_omnirouter_config()
    if router_config:
        rows.append(("OK", "router", f"local config found: {router_config}"))
    else:
        rows.append(("INFO", "router", "no local OmniRouter config found (optional)"))

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
    """Build the provider named by ``--provider``. Seam used by the tests."""
    if kind == "ollama":
        return OllamaProvider(model)
    raise ProviderError(f"unknown provider '{kind}'; only 'ollama' is available in Jalon 1")


def _default_model() -> str | None:
    try:
        models = list_models()
    except ProviderError as exc:
        print(f"uh chat: {exc}", file=sys.stderr)
        return None
    if not models:
        print(
            "uh chat: no local model installed; run `ollama pull llama3.2:3b` first",
            file=sys.stderr,
        )
        return None
    return str(models[0]["name"])


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
) -> int:
    """Run one user turn, print the answer, update ``history`` in place.

    Tool activity goes to stderr so that stdout stays scriptable.
    """
    before = len(history)
    history.append({"role": "user", "content": text})
    try:
        updated = run_turn(history, provider, toolsets, max_iterations=max_iterations)
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
    print(content if content else "(empty response)")
    return EXIT_OK


def cmd_chat(args: argparse.Namespace) -> int:
    if args.provider != "ollama":
        print(
            f"uh chat: unknown provider '{args.provider}'; only 'ollama' is available in Jalon 1",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    workspace, _origin = _workspace_path(args.workspace)
    if not workspace.is_dir():
        print(f"uh chat: workspace not found: {workspace}", file=sys.stderr)
        return EXIT_FAILURE
    os.environ["UH_WORKSPACE"] = str(workspace)

    model = args.model or _default_model()
    if not model:
        return EXIT_FAILURE

    try:
        provider = _make_provider(args.provider, model)
    except ProviderError as exc:
        print(f"uh chat: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    toolsets = build_toolsets(workspace)
    max_iterations = max(1, int(args.max_iterations or DEFAULT_MAX_ITERATIONS))
    history: list[dict] = [_system_message(workspace)]

    interactive = not args.once and sys.stdin is not None and sys.stdin.isatty()
    if not interactive:
        line = sys.stdin.readline() if sys.stdin is not None else ""
        text = (line or "").strip()
        if not text:
            return EXIT_OK
        return _turn(history, text, provider, toolsets, max_iterations)

    print(
        f"uh chat - model {getattr(provider, 'model', model)}, workspace {workspace}",
        file=sys.stderr,
    )
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
                print(
                    f"uh chat: current model {getattr(provider, 'model', model)}",
                    file=sys.stderr,
                )
                continue
            try:
                provider = _make_provider(args.provider, parts[1].strip())
            except ProviderError as exc:
                print(f"uh chat: {exc}", file=sys.stderr)
                continue
            model = parts[1].strip()
            print(f"uh chat: model set to {model}", file=sys.stderr)
            continue
        _turn(history, text, provider, toolsets, max_iterations)


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


def cmd_model_pull(_args: argparse.Namespace) -> int:
    return _not_implemented("uh model pull", "Jalon 2 (local model layer)")


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
            "(Ollama, free local routers). No API key needed to start."
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
    chat.add_argument("--provider", default="ollama", help="provider name (default: ollama)")
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

    model = sub.add_parser("model", help="manage local models")
    model_sub = model.add_subparsers(dest="model_command", metavar="<action>")
    model_sub.add_parser("list", help="list models served by Ollama")
    pull = model_sub.add_parser("pull", help="download a model (Jalon 2)")
    pull.add_argument("name", help="model name, e.g. llama3.2:3b")

    sub.add_parser("sessions", help="manage sessions (Jalon 3)")
    sub.add_parser("config", help="read or write UH configuration (Jalon 3)")

    # keep direct handles so a missing action can print the right help
    parser._subparsers_by_name = sub._name_parser_map  # type: ignore[attr-defined]
    return parser


def _model_help(parser: argparse.ArgumentParser) -> int:
    parser._subparsers_by_name["model"].print_help()  # type: ignore[attr-defined]
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
            return _model_help(parser)
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
