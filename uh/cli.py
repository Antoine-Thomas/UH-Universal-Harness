"""UH command line interface.

Jalon 3: persistent sessions (SQLite + FTS5), layered configuration (TOML),
``uh memory migrate|show``, and a ``uh chat`` that saves by default.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from . import __version__
from . import banner
from . import config as uh_config
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
from .store import Store, StoreError, default_store_path, display_id
from .toolsets import build_toolsets, describe_toolsets
from .toolsets.memory import SECTIONS as MEMORY_SECTIONS
from .toolsets.memory import memory_path, parse as parse_memory, uh_home

MIN_PYTHON = (3, 11)
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MAX_ITERATIONS = 8
MIN_FREE_DISK_BYTES = 500 * 1024 * 1024

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


def _config_warnings(cfg: uh_config.Config) -> None:
    for warning in cfg.warnings:
        print(f"uh: config: {warning}", file=sys.stderr)


def _load_config(args: argparse.Namespace | None = None) -> uh_config.Config:
    overrides: dict[str, Any] = {}
    if args is not None:
        overrides = {
            "default_provider": getattr(args, "provider", None),
            "default_model": getattr(args, "model", None),
            "max_iterations": getattr(args, "max_iterations", None),
            "workspace": getattr(args, "workspace", None),
        }
    cfg = uh_config.load(overrides)
    _config_warnings(cfg)
    return cfg


# --------------------------------------------------------------------------- #
# store helpers
# --------------------------------------------------------------------------- #
def _open_store(path: str | os.PathLike[str] | None = None) -> Store:
    return Store(path)


def _resolve_session(store: Store, token: str) -> tuple[dict | None, int]:
    """Accept a full id, a unique prefix, or the short form shown by ``sessions list``."""
    token = (token or "").strip()
    if not token:
        return None, 0
    exact = store.get_session(token)
    if exact is not None:
        return exact, 1
    matches = [
        session
        for session in store.list_sessions(limit=10_000)
        if session["id"].startswith(token) or display_id(session["id"]) == token
    ]
    if len(matches) == 1:
        return matches[0], 1
    return None, len(matches)


def _session_or_error(store: Store, token: str) -> tuple[dict | None, int]:
    session, matches = _resolve_session(store, token)
    if session is None:
        if matches > 1:
            print(f"uh: '{token}' is ambiguous ({matches} sessions); use a longer id", file=sys.stderr)
        else:
            print(f"uh: no session matching '{token}'", file=sys.stderr)
        return None, EXIT_FAILURE
    return session, EXIT_OK


# --------------------------------------------------------------------------- #
# uh doctor
# --------------------------------------------------------------------------- #
def _check_store(cfg: uh_config.Config) -> tuple[str, str]:
    path = cfg.resolved_store_path()
    if not path.exists():
        return "WARN", f"{path} not created yet (created on first `uh chat`)"
    try:
        with Store(path) as store:
            size = _human_size(path.stat().st_size)
            return "OK", f"{path} schema v{store.schema_version}, {size}, fts5 available"
    except StoreError as exc:
        return "WARN", f"{path} unusable: {exc}"


def _check_disk(cfg: uh_config.Config) -> tuple[str, str]:
    path = cfg.resolved_store_path()
    target = path.parent if path.parent.exists() else Path.cwd()
    try:
        free = shutil.disk_usage(str(target)).free
    except OSError as exc:
        return "WARN", f"cannot read free space for {target}: {exc}"
    if free < MIN_FREE_DISK_BYTES:
        return "WARN", f"{_human_size(free)} free on {target} (< {_human_size(MIN_FREE_DISK_BYTES)})"
    return "OK", f"{_human_size(free)} free on {target}"


def _config_detail() -> tuple[str, str]:
    found = []
    for layer, path in (
        ("user", uh_config.user_config_path()),
        ("project", uh_config.project_config_path()),
    ):
        if path.is_file():
            found.append(f"{layer}: {path}")
    if found:
        return "INFO", "; ".join(found)
    return "INFO", "no config file (defaults + environment only)"


def cmd_doctor(_args: argparse.Namespace) -> int:
    rows: list[tuple[str, str, str]] = []  # (status, label, detail)
    failures = 0
    warnings = 0

    cfg = uh_config.load()
    for warning in cfg.warnings:
        print(f"uh: config: {warning}", file=sys.stderr)

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

    # 8. Store (Jalon 3)
    store_status, store_detail = _check_store(cfg)
    rows.append((store_status, "store", store_detail))
    if store_status == "WARN":
        warnings += 1

    # 9. Disk space for the store volume
    disk_status, disk_detail = _check_disk(cfg)
    rows.append((disk_status, "disk", disk_detail))
    if disk_status == "WARN":
        warnings += 1

    # 10. Configuration layers
    config_status, config_detail = _config_detail()
    rows.append((config_status, "config", config_detail))

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


def _build_chain(
    requested: str | None,
    model: str | None,
    omnirouter_model: str = DEFAULT_OMNIROUTER_MODEL,
    preferred: str | None = None,
) -> tuple[list[tuple[str, Provider]], str]:
    """Return ``[(kind, provider), ...]`` and the model actually selected.

    ``requested`` (CLI ``--provider``) means one provider, no fallback.
    ``preferred`` is the configured default provider: it only ORDERS the chain,
    it never disables the fallback - otherwise a default of "ollama" in
    config.toml would silently kill the OmniRouter backup.
    """
    if requested:
        provider = _make_provider(requested, model or "")
        return [(requested, provider)], (model or getattr(provider, "model", "") or "")

    reasons: list[str] = []
    ollama_entry: tuple[str, Provider] | None = None
    ollama_model = model

    if not ollama_model:
        try:
            local = list_models()
            ollama_model = str(local[0]["name"]) if local else None
        except ProviderError as exc:
            reasons.append(f"ollama: {exc}")
            ollama_model = None
        if ollama_model is None and not any(item.startswith("ollama:") for item in reasons):
            reasons.append("ollama: no local model installed (run: ollama pull <name>)")

    if ollama_model:
        try:
            ollama_entry = ("ollama", _make_provider("ollama", ollama_model))
        except ProviderError as exc:
            reasons.append(f"ollama: {exc}")

    omni_entry: tuple[str, Provider] | None = None
    try:
        omni_entry = (
            "omnirouter",
            _make_provider("omnirouter", omnirouter_model or DEFAULT_OMNIROUTER_MODEL),
        )
    except ProviderError as exc:
        reasons.append(f"omnirouter: {exc}")

    omnirouter_first = (preferred or "").strip().lower() == "omnirouter"
    first, second = (omni_entry, ollama_entry) if omnirouter_first else (ollama_entry, omni_entry)
    chain = [entry for entry in (first, second) if entry is not None]

    if not chain:
        raise ProviderError("no usable provider - " + "; ".join(reasons))

    if chain[0][0] == "omnirouter" and not omnirouter_first:
        reason = next(
            (item[len("ollama: "):] for item in reasons if item.startswith("ollama: ")),
            "ollama provider unavailable",
        )
        print(f"fallback: ollama -> omnirouter (reason: {reason})", file=sys.stderr)

    return chain, (ollama_model or omnirouter_model or DEFAULT_OMNIROUTER_MODEL)


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


def _title_from(text: str, limit: int = 60) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit]


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
    cfg = _load_config(args)

    workspace_raw = args.workspace or str(cfg.get("workspace") or "")
    workspace, _origin = _workspace_path(workspace_raw or None)
    if not workspace.is_dir():
        print(f"uh chat: workspace not found: {workspace}", file=sys.stderr)
        return EXIT_FAILURE
    os.environ["UH_WORKSPACE"] = str(workspace)

    model = args.model or str(cfg.get("default_model") or "") or None
    provider_kind = args.provider or str(cfg.get("default_provider") or "") or None

    try:
        chain, model = _build_chain(
            args.provider,
            model,
            str(cfg.get("omnirouter_model") or ""),
            preferred=provider_kind,
        )
    except ProviderError as exc:
        print(f"uh chat: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    provider: Provider = _FallbackProvider(chain) if len(chain) > 1 else chain[0][1]
    stream = False if args.no_stream else bool(cfg.get("stream"))
    max_iterations = max(1, int(cfg.get("max_iterations") or DEFAULT_MAX_ITERATIONS))
    toolsets = build_toolsets(workspace)

    save = not args.no_save
    store: Store | None = None
    session_id: str | None = getattr(args, "session", None)
    if save:
        try:
            store = _open_store(cfg.resolved_store_path())
        except StoreError as exc:
            print(f"uh chat: {exc}", file=sys.stderr)
            return EXIT_FAILURE

    history: list[dict] = [_system_message(workspace)]
    if session_id:
        if store is None:
            print("uh chat: --session needs persistence (drop --no-save)", file=sys.stderr)
            return EXIT_FAILURE
        session, code = _session_or_error(store, session_id)
        if session is None:
            return code
        session_id = session["id"]
        if getattr(args, "new", False):
            fork = store.create_session(
                title=session.get("title") or "fork",
                model=model,
                provider="+".join(kind for kind, _ in chain),
                metadata={"forked_from": session_id},
            )
            for message in store.get_messages(session_id):
                store.append_message(fork, message)
            session_id = fork
            print(f"session {display_id(session_id)} (forked)", file=sys.stderr)
        history.extend(
            {key: value for key, value in message.items() if key not in {"id", "session_id", "created_at"}}
            for message in store.get_messages(session_id)
        )

    interactive = not args.once and sys.stdin is not None and sys.stdin.isatty()
    label = " + ".join(kind for kind, _ in chain)
    mode = "stream" if stream else "blocking"

    if interactive:
        if bool(cfg.get("banner")) and banner.should_show():
            print(banner.BANNER_SHORT, file=sys.stderr, end="")
        print(
            f"uh chat - model {model}, provider {label}, {mode}, workspace {workspace}",
            file=sys.stderr,
        )
        if session_id:
            print(f"session {display_id(session_id)}", file=sys.stderr)
        print("commands: /exit, /model <name>, /tools, /clear", file=sys.stderr)

    def persist(new_messages: list[dict]) -> None:
        nonlocal session_id
        if store is None or not new_messages:
            return
        if session_id is None:
            first_user = next(
                (m.get("content") or "" for m in new_messages if m.get("role") == "user"), ""
            )
            session_id = store.create_session(
                title=_title_from(first_user),
                model=str(model or ""),
                provider="+".join(kind for kind, _ in chain),
            )
            print(f"session {display_id(session_id)}", file=sys.stderr)
        for message in new_messages:
            store.append_message(session_id, message)

    def run_turn_text(text: str) -> int:
        before = len(history)
        code = _turn(history, text, provider, toolsets, max_iterations, stream)
        if code == EXIT_OK:
            try:
                persist(history[before:])
            except StoreError as exc:
                print(f"uh chat: cannot record session: {exc}", file=sys.stderr)
        return code

    try:
        if not interactive:
            prompt = getattr(args, "prompt", None)
            text = (prompt or "").strip()
            if not text:
                line = sys.stdin.readline() if sys.stdin is not None else ""
                text = (line or "").strip()
            if not text:
                return EXIT_OK
            return run_turn_text(text)

        while True:
            try:
                line = input("you> ")
            except (EOFError, KeyboardInterrupt):
                print("", file=sys.stderr)
                break
            text = line.strip()
            if not text:
                continue
            if text in {"/exit", "/quit"}:
                break
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
                kind = provider_kind or chain[0][0]
                try:
                    provider = _make_provider(kind, parts[1].strip())
                except ProviderError as exc:
                    print(f"uh chat: {exc}", file=sys.stderr)
                    continue
                model = parts[1].strip()
                print(f"uh chat: model set to {model} ({kind})", file=sys.stderr)
                continue
            run_turn_text(text)
    finally:
        if store is not None:
            store.close()

    if session_id:
        print(f"resume with: uh chat --session {display_id(session_id)}", file=sys.stderr)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# uh sessions
# --------------------------------------------------------------------------- #
def _sessions_store(args: argparse.Namespace) -> tuple[Store | None, int]:
    cfg = _load_config(args)
    try:
        return Store(cfg.resolved_store_path()), EXIT_OK
    except StoreError as exc:
        print(f"uh sessions: {exc}", file=sys.stderr)
        return None, EXIT_FAILURE


def cmd_sessions_list(args: argparse.Namespace) -> int:
    store, code = _sessions_store(args)
    if store is None:
        return code
    with store:
        sessions = store.list_sessions(limit=getattr(args, "limit", 50))
    if not sessions:
        print("no sessions", file=sys.stderr)
        return EXIT_OK

    print(f"  {'ID':<11}{'UPDATED':<21}{'PROVIDER':<12}{'MSGS':>5}  TITLE")
    for session in sessions:
        title = (session.get("title") or "").replace("\n", " ")[:38]
        provider = (session.get("provider") or "-")[:11]
        print(
            f"  {display_id(session['id']):<11}{str(session.get('updated_at'))[:19]:<21}"
            f"{provider:<12}{session.get('message_count', 0):>5}  {title}"
        )
    print("")
    print(f"{len(sessions)} session(s)")
    return EXIT_OK


def cmd_sessions_show(args: argparse.Namespace) -> int:
    store, code = _sessions_store(args)
    if store is None:
        return code
    with store:
        session, code = _session_or_error(store, args.session_id)
        if session is None:
            return code
        limit = None if getattr(args, "all", False) else 20
        messages = store.get_messages(session["id"], limit=limit)

    print(f"session {session['id']}")
    print(f"  title:    {session.get('title') or '-'}")
    print(f"  model:    {session.get('model') or '-'}")
    print(f"  provider: {session.get('provider') or '-'}")
    print(f"  created:  {session.get('created_at')}")
    print(f"  updated:  {session.get('updated_at')}")
    if session.get("metadata"):
        print(f"  metadata: {json.dumps(session['metadata'], ensure_ascii=False)}")
    print("")
    if not messages:
        print("(no messages)")
        return EXIT_OK
    if limit is not None:
        print(f"last {len(messages)} message(s)  (--all for everything)")
    for message in messages:
        role = message.get("role") or "?"
        name = f"/{message['name']}" if message.get("name") else ""
        body = (message.get("content") or "").replace("\n", " ")
        suffix = " [tool_calls]" if message.get("tool_calls") else ""
        print(f"  {role}{name}: {body[:160]}{suffix}")
    return EXIT_OK


def cmd_sessions_search(args: argparse.Namespace) -> int:
    store, code = _sessions_store(args)
    if store is None:
        return code
    with store:
        hits = store.search(args.query, limit=getattr(args, "limit", 20))
    if not hits:
        print(f"no match for '{args.query}'", file=sys.stderr)
        return EXIT_OK
    for hit in hits:
        print(f"  {display_id(hit['session_id'])}  {str(hit.get('updated_at'))[:19]}  {hit.get('title') or '-'}")
        print(f"      {hit.get('snippet')}")
    print("")
    print(f"{len(hits)} match(es)")
    return EXIT_OK


def cmd_sessions_delete(args: argparse.Namespace) -> int:
    store, code = _sessions_store(args)
    if store is None:
        return code
    with store:
        session, code = _session_or_error(store, args.session_id)
        if session is None:
            return code
        if not args.yes:
            if not (sys.stdin and sys.stdin.isatty()):
                print("uh sessions delete: confirmation needs a TTY (use --yes)", file=sys.stderr)
                return EXIT_FAILURE
            answer = input(f"delete session {display_id(session['id'])}? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("cancelled", file=sys.stderr)
                return EXIT_FAILURE
        deleted = store.delete_session(session["id"])
    print(f"deleted {display_id(session['id'])}" if deleted else "nothing deleted", file=sys.stderr)
    return EXIT_OK if deleted else EXIT_FAILURE


def cmd_sessions_resume(args: argparse.Namespace) -> int:
    """Reuse the chat command with a preloaded session."""
    namespace = argparse.Namespace(
        model=getattr(args, "model", None),
        provider=getattr(args, "provider", None),
        workspace=getattr(args, "workspace", None),
        max_iterations=getattr(args, "max_iterations", None),
        once=False,
        no_stream=getattr(args, "no_stream", False),
        session=args.session_id,
        no_save=getattr(args, "no_save", False),
        new=getattr(args, "fork", False),
        prompt=None,
    )
    return cmd_chat(namespace)


# --------------------------------------------------------------------------- #
# uh config
# --------------------------------------------------------------------------- #
def cmd_config_show(args: argparse.Namespace) -> int:
    cfg = uh_config.load()
    _config_warnings(cfg)
    width = max(len(key) for key in uh_config.DEFAULTS)
    print(f"  {'KEY':<{width}}  {'VALUE':<24}  SOURCE")
    for key in uh_config.DEFAULTS:
        value = cfg.get(key)
        rendered = "true" if value is True else "false" if value is False else str(value)
        if rendered == "":
            rendered = '""'
        print(f"  {key:<{width}}  {rendered:<24}  {cfg.source(key)}")
    return EXIT_OK


def cmd_config_get(args: argparse.Namespace) -> int:
    if args.key not in uh_config.KEY_TYPES:
        print(
            f"uh config get: unknown key '{args.key}'; known: "
            f"{', '.join(sorted(uh_config.KEY_TYPES))}",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    cfg = uh_config.load()
    _config_warnings(cfg)
    value = cfg.get(args.key)
    print("true" if value is True else "false" if value is False else value)
    return EXIT_OK


def cmd_config_set(args: argparse.Namespace) -> int:
    layer = "project" if args.project else "user"
    try:
        path = uh_config.write_layer(layer, args.key, args.value)
    except uh_config.ConfigError as exc:
        print(f"uh config set: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"{args.key} = {args.value} ({layer}: {path})", file=sys.stderr)
    return EXIT_OK


def cmd_config_path(_args: argparse.Namespace) -> int:
    for layer, path in (
        ("user", uh_config.user_config_path()),
        ("project", uh_config.project_config_path()),
    ):
        state = "exists" if path.is_file() else "missing"
        print(f"  {layer:<8} {path}  [{state}]")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# uh memory
# --------------------------------------------------------------------------- #
def cmd_memory_show(_args: argparse.Namespace) -> int:
    path = memory_path()
    if not path.is_file():
        print(f"uh memory show: no memory file at {path}", file=sys.stderr)
        return EXIT_FAILURE
    sections, order = parse_memory(path.read_text(encoding="utf-8", errors="replace"))
    print(f"memory file: {path}")
    for name in order or MEMORY_SECTIONS:
        body = sections.get(name) or ""
        print("")
        print(f"## {name}")
        print(body if body else "(empty)")
    return EXIT_OK


def cmd_memory_migrate(args: argparse.Namespace) -> int:
    """One-shot, idempotent migration of the flat memory file into the store."""
    cfg = _load_config(args)
    source = memory_path()
    if not source.is_file():
        print(f"uh memory migrate: no memory file at {source}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        store = Store(cfg.resolved_store_path())
    except StoreError as exc:
        print(f"uh memory migrate: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    with store:
        existing = store.find_session_by_metadata("kind", "memory")
        if existing is not None:
            print(f"memory already migrated (session {display_id(existing['id'])})", file=sys.stderr)
            return EXIT_OK

        text = source.read_text(encoding="utf-8", errors="replace")
        sections, order = parse_memory(text)

        backup = _backup_memory(source)
        if backup is not None:
            print(f"backup: {backup}", file=sys.stderr)

        session_id = store.create_session(
            title="memory",
            model="",
            provider="",
            metadata={"kind": "memory", "source": str(source)},
        )
        count = 0
        for name in list(MEMORY_SECTIONS) + [n for n in order if n not in MEMORY_SECTIONS]:
            body = (sections.get(name) or "").strip("\n")
            if not body:
                continue
            store.append_message(
                session_id,
                {"role": "system", "content": f"[memory:{name}]\n{body}"},
            )
            count += 1

    print(f"migrated {count} section(s) into session {display_id(session_id)}")
    return EXIT_OK


def _backup_memory(source: Path) -> Path | None:
    """Copy ``MEMORY.md`` once per migration run; never overwrite an existing backup."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    target = source.with_name(f"{source.name}.bak.{stamp}")
    if target.exists():  # same run, same second: keep the existing backup
        return target
    try:
        shutil.copy2(source, target)
    except OSError as exc:
        print(f"uh memory migrate: cannot write backup {target}: {exc}", file=sys.stderr)
        return None
    return target


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

    sub.add_parser("doctor", help="check the local environment (python, httpx, git, ollama, store)")

    chat = sub.add_parser("chat", help="conversation loop with file/terminal/memory tools")
    chat.add_argument("prompt", nargs="?", help="one-shot prompt (otherwise read stdin)")
    chat.add_argument("--model", help="model name (default: config, then first Ollama model)")
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
        default=None,
        help=f"max provider calls per turn (default: config, else {DEFAULT_MAX_ITERATIONS})",
    )
    chat.add_argument(
        "--once",
        action="store_true",
        help="answer one line and exit (implicit when stdin is not a TTY)",
    )
    chat.add_argument(
        "--no-stream",
        action="store_true",
        help="wait for the complete answer instead of streaming tokens",
    )
    chat.add_argument("--session", metavar="<id>", help="resume this session (full id or prefix)")
    chat.add_argument(
        "--no-save",
        action="store_true",
        help="do not record the conversation in the store",
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

    sessions = sub.add_parser("sessions", help="list, inspect, search and resume sessions")
    sessions_sub = sessions.add_subparsers(dest="sessions_command", metavar="<action>")
    sessions_list = sessions_sub.add_parser("list", help="list recent sessions")
    sessions_list.add_argument("--limit", type=int, default=50, help="max sessions (default 50)")
    sessions_show = sessions_sub.add_parser("show", help="show one session and its messages")
    sessions_show.add_argument("session_id")
    sessions_show.add_argument("--all", action="store_true", help="show every message")
    sessions_search = sessions_sub.add_parser("search", help="full-text search across messages")
    sessions_search.add_argument("query")
    sessions_search.add_argument("--limit", type=int, default=20)
    sessions_delete = sessions_sub.add_parser("delete", help="delete one session")
    sessions_delete.add_argument("session_id")
    sessions_delete.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    sessions_resume = sessions_sub.add_parser("resume", help="continue a session in uh chat")
    sessions_resume.add_argument("session_id")
    sessions_resume.add_argument("--fork", action="store_true", help="copy into a new session")
    sessions_resume.add_argument("--no-save", action="store_true", help="ephemeral continuation")
    sessions_resume.add_argument("--model")
    sessions_resume.add_argument("--provider", metavar="<kind>")
    sessions_resume.add_argument("--workspace")
    sessions_resume.add_argument("--max-iterations", type=int, default=None)
    sessions_resume.add_argument("--no-stream", action="store_true")

    config_cmd = sub.add_parser("config", help="inspect and edit the layered configuration")
    config_sub = config_cmd.add_subparsers(dest="config_command", metavar="<action>")
    config_sub.add_parser("show", help="effective values and where each comes from")
    config_get = config_sub.add_parser("get", help="print one effective value")
    config_get.add_argument("key")
    config_set = config_sub.add_parser("set", help="write one key in a layer")
    config_set.add_argument("key")
    config_set.add_argument("value")
    layer = config_set.add_mutually_exclusive_group()
    layer.add_argument("--user", action="store_true", help="write ~/.uh/config.toml (default)")
    layer.add_argument("--project", action="store_true", help="write ./uh.toml")
    config_sub.add_parser("path", help="show which config files are read")

    memory = sub.add_parser("memory", help="inspect or migrate the flat memory file")
    memory_sub = memory.add_subparsers(dest="memory_command", metavar="<action>")
    memory_sub.add_parser("show", help="print the flat memory file sections")
    memory_sub.add_parser("migrate", help="copy the flat memory file into a session (idempotent)")

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
        if banner.should_show():
            print(banner.render())
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
            handlers = {
                "list": cmd_sessions_list,
                "show": cmd_sessions_show,
                "search": cmd_sessions_search,
                "delete": cmd_sessions_delete,
                "resume": cmd_sessions_resume,
            }
            handler = handlers.get(args.sessions_command)
            if handler is None:
                return _sub_help(parser, "sessions")
            return handler(args)
        if args.command == "config":
            handlers = {
                "show": cmd_config_show,
                "get": cmd_config_get,
                "set": cmd_config_set,
                "path": cmd_config_path,
            }
            handler = handlers.get(args.config_command)
            if handler is None:
                return _sub_help(parser, "config")
            return handler(args)
        if args.command == "memory":
            handlers = {
                "show": cmd_memory_show,
                "migrate": cmd_memory_migrate,
            }
            handler = handlers.get(args.memory_command)
            if handler is None:
                return _sub_help(parser, "memory")
            return handler(args)
    except Exception as exc:  # noqa: BLE001 - last-resort guard: no traceback by default
        if getattr(args, "debug", False):
            raise
        print(f"uh: unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    parser.print_help()
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":
    raise SystemExit(main())
