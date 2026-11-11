"""UH command line interface.

Jalon 0 exposes the full command surface; anything not implemented yet
exits with EXIT_NOT_IMPLEMENTED (2) and says which milestone delivers it.

The CLI is provider-agnostic on purpose: no Claude/GPT assumption, no API
key required on first run. Local models are discovered through Ollama
(http://127.0.0.1:11434) and, later, through a free local router.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__

MIN_PYTHON = (3, 11)
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"

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
# placeholders (Jalon 0 contract)
# --------------------------------------------------------------------------- #
def _not_implemented(command: str, milestone: str) -> int:
    print(
        f"{command}: Not implemented in Jalon 0 - planned in {milestone}.",
        file=sys.stderr,
    )
    return EXIT_NOT_IMPLEMENTED


def cmd_model_list(_args: argparse.Namespace) -> int:
    return _not_implemented("uh model list", "Jalon 2 (local model layer)")


def cmd_model_pull(_args: argparse.Namespace) -> int:
    return _not_implemented("uh model pull", "Jalon 2 (local model layer)")


def cmd_chat(_args: argparse.Namespace) -> int:
    return _not_implemented("uh chat", "Jalon 1 (agent core)")


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
    sub.add_parser("chat", help="start a conversation loop (Jalon 1)")

    model = sub.add_parser("model", help="manage local models")
    model_sub = model.add_subparsers(dest="model_command", metavar="<action>")
    model_sub.add_parser("list", help="list locally detected models (Jalon 2)")
    pull = model_sub.add_parser("pull", help="download a model (Jalon 2)")
    pull.add_argument("name", help="model name, e.g. llama3.2:3b")

    sub.add_parser("sessions", help="manage sessions (Jalon 3)")
    sub.add_parser("config", help="read or write UH configuration (Jalon 3)")

    # keep direct handles so a missing action can print the right help
    parser._subparsers_by_name = sub._name_parser_map  # type: ignore[attr-defined]
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return EXIT_OK

    if args.command == "doctor":
        return cmd_doctor(args)

    if args.command == "chat":
        return cmd_chat(args)

    if args.command == "model":
        if args.model_command == "list":
            return cmd_model_list(args)
        if args.model_command == "pull":
            return cmd_model_pull(args)
        # `uh model` without an action: print model help instead of crashing.
        parser._subparsers_by_name["model"].print_help()  # type: ignore[attr-defined]
        return EXIT_NOT_IMPLEMENTED

    if args.command == "sessions":
        return cmd_sessions(args)

    if args.command == "config":
        return cmd_config(args)

    parser.print_help()
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":
    raise SystemExit(main())
