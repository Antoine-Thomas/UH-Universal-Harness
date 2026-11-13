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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import __version__, banner, commands, console, keys, sandbox, uhb_spawn
from . import config as uh_config
from .providers import (
    Provider,
    ProviderError,
    all_provider_kinds,
    available_providers,
    build_provider,
)
from .providers.ollama import list_models, pull_model
from .providers.omnirouter import (
    DEFAULT_MODEL as DEFAULT_OMNIROUTER_MODEL,
)
from .providers.omnirouter import (
    config_path as omnirouter_config_path,
)
from .providers.omnirouter import (
    list_remote_models,
)
from .providers.omnirouter import (
    resolve_base_url as omnirouter_base_url,
)
from .store import Store, StoreError, display_id
from .toolsets import build_toolsets
from .toolsets.memory import SECTIONS as MEMORY_SECTIONS
from .toolsets.memory import memory_path, uh_home
from .toolsets.memory import parse as parse_memory

MIN_PYTHON = (3, 10)
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MAX_ITERATIONS = 8
MIN_FREE_DISK_BYTES = 500 * 1024 * 1024
PACKAGE_NAME = "uh-harness"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NOT_IMPLEMENTED = 2


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _configure_stdio() -> None:
    """Force UTF-8 on stdout/stderr.

    cmd.exe and PowerShell default to cp1252/cp850: without this, printing the
    banner's box drawing or an accented answer raises UnicodeEncodeError and the
    CLI dies on the first line. ``errors="replace"`` keeps an exotic terminal
    usable instead of crashing it.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):  # pragma: no cover - exotic streams
            pass


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
    """Resolve the workspace: argument, ``UH_WORKSPACE``, cwd, then the fallback.

    Origin ``system-fallback`` means the cwd was a system directory: the caller
    prints :data:`uh.sandbox.SYSTEM_WORKSPACE_WARNING` so the user knows why the
    workspace is not where they launched UH.
    """
    value = raw or os.environ.get("UH_WORKSPACE")
    if value:
        origin = "argument" if raw else "UH_WORKSPACE"
        return Path(value).expanduser().resolve(), origin
    cwd = Path.cwd().resolve()
    if sandbox.is_system_directory(cwd):
        return sandbox.fallback_workspace(), "system-fallback"
    return cwd, "cwd"


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

    # 11. Provider keys (Jalon 4): present/absent only, never the value
    from .keys import file_mode

    rows.append(("INFO", "keys", file_mode()))

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


def _router_entry_if_configured() -> tuple[str, Provider] | None:
    """Add the free router to the chain only when it has keys for catalogue entries."""
    try:
        from .keys import read_providers
        from .router.catalog import load_catalog

        catalog = load_catalog()
        configured = set(read_providers()) & set(catalog)
    except Exception:  # noqa: BLE001 - a missing catalogue must not break `uh chat`
        return None
    if not configured:
        return None
    try:
        return ("router", _make_provider("router", ""))
    except ProviderError:
        return None


def _cloudflare_entry() -> tuple[str, Provider] | None:
    """Cloudflare joins the chain only when the account id and a token exist."""
    from .providers.cloudflare import resolve_account_id, resolve_api_token

    if not (resolve_account_id() and resolve_api_token()):
        return None
    try:
        return ("cloudflare", _make_provider("cloudflare", ""))
    except ProviderError:
        return None


def _deepseek_entry() -> tuple[str, Provider] | None:
    """DeepSeek joins the chain only when a key is configured."""
    from .providers.deepseek import resolve_api_key

    if not resolve_api_key():
        return None
    try:
        return ("deepseek", _make_provider("deepseek", ""))
    except ProviderError:
        return None


def _build_chain(
    requested: str | None,
    model: str | None,
    omnirouter_model: str = DEFAULT_OMNIROUTER_MODEL,
    preferred: str | None = None,
) -> tuple[list[tuple[str, Provider]], str]:
    """Return ``[(kind, provider), ...]`` and the model actually selected.

    Default order::

        ollama (local) -> cloudflare (free) -> deepseek (paid, safety valve)
        -> omnirouter (local gateway) -> router (free providers with keys)

    ``requested`` (CLI ``--provider``) means one provider, no fallback.
    ``preferred`` (the configured default provider) only MOVES that entry to the
    front; it never disables the fallback - otherwise a default of "ollama" in
    config.toml would silently kill the cloud backups. A provider whose key is
    not configured is skipped, never attempted: a missing key costs a skip
    instead of a 401.
    """
    if requested:
        provider = _make_provider(requested, model or "")
        return [(requested, provider)], (model or getattr(provider, "model", "") or "")

    reasons: list[str] = []
    ollama_entry: tuple[str, Provider] | None = None
    ollama_model = model

    if not ollama_model:
        try:
            from .ollama_spawn import pick_model

            local = list_models()
            names = [str(item["name"]) for item in local]
            ollama_model = pick_model(names)
            if ollama_model is None and names:
                # Alphabetical order used to pick lfm3, which answered with no
                # JSON: a free pass that produces nothing is not a saving.
                reasons.append(
                    "ollama: available but no capable model (need >= 3B), falling back to cloud"
                )
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

    cloudflare_entry = _cloudflare_entry()
    if cloudflare_entry is None:
        reasons.append("cloudflare: not configured (account id or API key missing)")
    deepseek_entry = _deepseek_entry()
    if deepseek_entry is None:
        reasons.append("deepseek: not configured (no API key)")

    omni_entry: tuple[str, Provider] | None = None
    try:
        omni_entry = (
            "omnirouter",
            _make_provider("omnirouter", omnirouter_model or DEFAULT_OMNIROUTER_MODEL),
        )
    except ProviderError as exc:
        reasons.append(f"omnirouter: {exc}")

    router_entry = _router_entry_if_configured()

    chain = [
        entry
        for entry in (ollama_entry, cloudflare_entry, deepseek_entry, omni_entry, router_entry)
        if entry is not None
    ]

    if not chain:
        raise ProviderError("no usable provider - " + "; ".join(reasons))

    # Local first, cloud second - unless the operator says otherwise.
    # The configured default used to jump to the front of the whole chain, which
    # put omnirouter ahead of a running Ollama: the pass paid for tokens while the
    # boost badge was green. The default now decides the order of the *cloud*
    # entries only, and UH_ROUTER_PREFER can put the cloud back in front.
    prefer = (os.environ.get("UH_ROUTER_PREFER") or "auto").strip().lower()
    if prefer not in ("auto", "local", "cloud"):
        prefer = "auto"
    local_entries = [entry for entry in chain if entry[0] == "ollama"]
    cloud_entries = [entry for entry in chain if entry[0] != "ollama"]
    preferred_name = (preferred or "").strip().lower()
    if preferred_name:
        cloud_entries = [entry for entry in cloud_entries if entry[0] == preferred_name] + [
            entry for entry in cloud_entries if entry[0] != preferred_name
        ]
    chain = (cloud_entries + local_entries) if prefer == "cloud" else (local_entries + cloud_entries)

    if chain[0][0] != "ollama" and ollama_entry is None:
        reason = next(
            (item[len("ollama: "):] for item in reasons if item.startswith("ollama: ")),
            "ollama provider unavailable",
        )
        print(f"fallback: ollama -> {chain[0][0]} (reason: {reason})", file=sys.stderr)

    resolved = (
        ollama_model
        or str(getattr(chain[0][1], "model", "") or "")
        or omnirouter_model
        or DEFAULT_OMNIROUTER_MODEL
    )
    return chain, resolved


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

    configured = keys.read_providers()
    print("")
    if configured:
        for name in sorted(configured):
            entry = configured[name]
            state = "key present" if entry.get("key") else "key absent"
            print(f"  {name:<11} configured at {entry.get('base_url')} ({state})")
            print(f"  {'':<11} model: {entry.get('model') or '-'}")
    else:
        print(f"  {'(user)':<11} none added (uh providers add <name>)")

    print("")
    print(f"registered: {', '.join(all_provider_kinds())}")
    print("keys file: " + keys.file_mode())
    return EXIT_OK


def cmd_providers_add(args: argparse.Namespace) -> int:
    """Store one OpenAI-compatible provider. The key is never echoed or logged."""
    from .providers_admin import AdminError, read_secret, save_provider

    name = (args.name or "").strip().lower()
    base_url = (getattr(args, "base_url", None) or "").strip()
    model = (getattr(args, "model", None) or "").strip()
    interactive = sys.stdin is not None and sys.stdin.isatty()

    if interactive and not base_url:
        base_url = input("base URL (e.g. https://api.groq.com/openai/v1): ").strip()
    if interactive and not model:
        model = input("model id: ").strip()
    if not (base_url and model):
        print("uh providers add: --base-url and --model are required", file=sys.stderr)
        return EXIT_FAILURE

    key = read_secret()
    if not key:
        print(
            "uh providers add: an API key is required (prompt, stdin or UH_PROVIDER_KEY)",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    try:
        saved = save_provider(name, base_url, model, key)
    except AdminError as exc:
        print(f"uh providers add: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"added provider {saved['name']} (model {saved['model'] or 'none'})")
    print("the key is stored in $UH_HOME/keys.toml only; it is never printed or committed")
    return EXIT_OK


def cmd_providers_test(args: argparse.Namespace) -> int:
    """One 'ping' with max_tokens 1: no session content leaves the machine."""
    from .providers_admin import AdminError, test_connection

    try:
        result = test_connection(args.name)
    except AdminError as exc:
        print(f"uh providers test: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    if result["ok"]:
        print(f"{args.name}: ok (HTTP {result['status']})")
        return EXIT_OK
    suffix = f" (HTTP {result['status']})" if result.get("status") else ""
    print(f"{args.name}: failed: {result['error_kind']}{suffix}", file=sys.stderr)
    return EXIT_FAILURE


def cmd_keys_list(_args: argparse.Namespace) -> int:
    from .providers_admin import provider_rows

    rows = provider_rows()
    print(f"{'provider':<14}{'keys':<9}{'in chain':<10}verified")
    for row in rows:
        count = str(row["keys"]) if row["keys"] else "absent"
        print(f"{row['name']:<14}{count:<9}{'yes' if row['in_chain'] else 'no':<10}{row['verified']}")
    print("\nvalues are never displayed: only presence and count")
    return EXIT_OK


def cmd_keys_add(args: argparse.Namespace) -> int:
    from .providers_admin import AdminError, add_key_to, read_secret

    key = read_secret()
    if not key:
        print("uh keys add: no key given (prompt, stdin or UH_PROVIDER_KEY)", file=sys.stderr)
        return EXIT_FAILURE
    try:
        count = add_key_to(args.name, key)
    except AdminError as exc:
        print(f"uh keys add: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"{args.name}: {count} key(s) stored")
    return EXIT_OK


def cmd_keys_remove(args: argparse.Namespace) -> int:
    from .providers_admin import AdminError, remove_key_index

    try:
        count = remove_key_index(args.name, args.index)
    except AdminError as exc:
        print(f"uh keys remove: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"{args.name}: {count} key(s) left")
    return EXIT_OK


def cmd_docs(args: argparse.Namespace) -> int:
    """Print where the documentation lives, optionally open the README."""
    import webbrowser
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    readme = root / "README.md"
    docs = root / "docs"
    print(f"README: {readme}")
    if docs.is_dir():
        print("docs:")
        for item in sorted(docs.glob("*.md")):
            print(f"  {item.name}")
    if args.open:
        target = readme if readme.exists() else docs
        webbrowser.open(target.as_uri())
        print(f"opened {target}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# uh upgrade
# --------------------------------------------------------------------------- #
def in_virtualenv() -> bool:
    """True inside a venv: pip then writes to a throwaway prefix, not the system."""
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def installed_version() -> str | None:
    """Version recorded in the installed distribution (None when unreadable).

    Caches are invalidated first: pip has just rewritten dist-info on disk, and
    the running interpreter must report the new version, not the cached one.
    """
    try:
        import importlib
        from importlib import metadata

        importlib.invalidate_caches()
        return metadata.version(PACKAGE_NAME)
    except Exception:  # noqa: BLE001 - missing or corrupt metadata is not fatal
        return None


def pip_upgrade_command(*, user: bool = False, dry_run: bool = False) -> list[str]:
    command = [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME]
    if user:
        command.append("--user")
    if dry_run:
        command.append("--dry-run")
    return command


def restart_process() -> int:
    """Re-exec ``uh`` (no arguments: the console) in place of this process."""
    print("uh upgrade: restarting uh", file=sys.stderr)
    try:
        os.execv(sys.executable, [sys.executable, "-m", "uh"])
    except OSError as exc:
        print(f"uh upgrade: restart failed ({exc}); run uh yourself", file=sys.stderr)
        return EXIT_FAILURE
    return EXIT_OK  # pragma: no cover - execv does not return


def cmd_upgrade(args: argparse.Namespace) -> int:
    """`uh upgrade`: pip install --upgrade uh-harness, then report and maybe restart."""
    running = __version__
    installed = installed_version() or running
    print(f"uh upgrade: running {running}, installed {installed}")

    if not in_virtualenv() and not args.user:
        print(
            "uh upgrade: not inside a virtualenv - refusing to modify the system "
            "interpreter (pass --user for a user-level install)",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    command = pip_upgrade_command(user=args.user, dry_run=args.dry_run)
    # flush before handing the terminal to pip: its output is unbuffered, ours
    # is block-buffered when redirected, and the two would interleave backwards.
    print("uh upgrade: " + " ".join(command), flush=True)
    try:
        completed = subprocess.run(command, check=False)
    except OSError as exc:
        print(f"uh upgrade: cannot run pip: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    if completed.returncode != 0:
        print(
            f"uh upgrade: pip failed (exit {completed.returncode}); "
            f"{installed} stays installed, nothing changed",
            file=sys.stderr,
        )
        return completed.returncode if completed.returncode > 0 else EXIT_FAILURE

    if args.dry_run:
        print("uh upgrade: dry run, nothing was installed")
        return EXIT_OK

    upgraded = installed_version() or installed
    if upgraded == installed:
        # running can differ from installed in a dev checkout (a stale egg-info
        # next to the source): saying "1.0.0 -> 1.0.0" would be nonsense.
        print(f"uh upgrade: already up to date (installed {installed}, running {running})")
        return EXIT_OK
    print(f"uh upgrade: {installed} -> {upgraded}")
    if not args.restart:
        print("uh upgrade: relaunch uh to use the new version (or pass --restart)")
        return EXIT_OK
    return restart_process()


def cmd_providers_remove(args: argparse.Namespace) -> int:
    name = (args.name or "").strip().lower()
    if not keys.remove_provider(name):
        print(f"uh providers remove: no provider '{name}'", file=sys.stderr)
        return EXIT_FAILURE
    print(f"removed provider {name}")
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
# uh chat (Jalon 4: console session, slash commands, persistence)
# --------------------------------------------------------------------------- #
def _chat_namespace(**overrides) -> argparse.Namespace:
    defaults = dict(
        prompt=None,
        model=None,
        provider=None,
        workspace=None,
        max_iterations=None,
        once=False,
        no_stream=False,
        session=None,
        no_save=False,
        new=False,
        debug=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _build_chat_session(
    args: argparse.Namespace,
    cfg: uh_config.Config,
    *,
    chain: list[tuple[str, Provider]] | None = None,
    model: str | None = None,
    announce: bool = True,
) -> tuple[console.ChatSession | None, int]:
    """Resolve workspace/provider/store and build the console session."""
    workspace_raw = args.workspace or str(cfg.get("workspace") or "")
    workspace, _origin = _workspace_path(workspace_raw or None)
    if _origin == "system-fallback":
        # Dim when the console can colour, plain text otherwise: the operator
        # must see why the workspace is not the directory they launched from.
        console.Console().detail(sandbox.SYSTEM_WORKSPACE_WARNING)
    if not workspace.is_dir():
        print(f"uh chat: workspace not found: {workspace}", file=sys.stderr)
        return None, EXIT_FAILURE
    os.environ["UH_WORKSPACE"] = str(workspace)

    if chain is None or model is None:
        requested_model = args.model or str(cfg.get("default_model") or "") or None
        preferred = args.provider or str(cfg.get("default_provider") or "") or None
        try:
            chain, model = _build_chain(
                args.provider,
                requested_model,
                str(cfg.get("omnirouter_model") or ""),
                preferred=preferred,
            )
        except ProviderError as exc:
            print(f"uh chat: {exc}", file=sys.stderr)
            return None, EXIT_FAILURE

    stream = False if args.no_stream else bool(cfg.get("stream"))
    max_iterations = max(1, int(cfg.get("max_iterations") or DEFAULT_MAX_ITERATIONS))
    toolsets = build_toolsets(workspace)

    store: Store | None = None
    if not args.no_save:
        try:
            store = _open_store(cfg.resolved_store_path())
        except StoreError as exc:
            print(f"uh chat: {exc}", file=sys.stderr)
            return None, EXIT_FAILURE

    session = console.ChatSession(
        provider_factory=_make_provider,
        provider_kind=chain[0][0],
        chain=chain,
        model=model,
        toolsets=toolsets,
        workspace=workspace,
        cfg=cfg,
        stream=stream,
        store=store,
        max_iterations=max_iterations,
        announce=announce,
    )

    requested_session = getattr(args, "session", None)
    if requested_session:
        if store is None:
            print("uh chat: --session needs persistence (drop --no-save)", file=sys.stderr)
            session.close()
            return None, EXIT_FAILURE
        message = session.resume(requested_session)
        if not message.startswith("resumed"):
            print(f"uh: {message}", file=sys.stderr)
            session.close()
            return None, EXIT_FAILURE
        print(f"uh chat: {message}", file=sys.stderr)
        if getattr(args, "new", False):
            source = session.session_id
            assert source is not None and store is not None
            fork_id = store.create_session(
                title="fork",
                model=str(session.model or ""),
                provider=session.provider_kind or "",
                metadata={"forked_from": source},
            )
            for message_dict in store.get_messages(source):
                store.append_message(fork_id, message_dict)
            session.session_id = fork_id
            print(f"session {display_id(fork_id)} (forked)", file=sys.stderr)

    return session, EXIT_OK


# First tokens that mean "the user thinks this is a shell". Kept to the ones
# people actually type at a prompt; the heuristic is documented in the helper.
SHELL_COMMANDS = frozenset(
    {
        "uh",
        "pip",
        "pip3",
        "pipx",
        "python",
        "python3",
        "py",
        "uv",
        "conda",
        "git",
        "ls",
        "cd",
        "pwd",
        "dir",
        "cat",
        "type",
        "mkdir",
        "rmdir",
        "rm",
        "del",
        "cp",
        "copy",
        "mv",
        "move",
        "touch",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "cargo",
        "go",
        "dotnet",
        "docker",
        "systemctl",
        "sudo",
        "export",
        "ollama",
    }
)

SHELL_WARNING = (
    "uh chat: that looks like a shell command. Type /exit first to run it, "
    "or prefix with / to use a chat command."
)

STARTUP_HEADER = (
    "Web UI: run 'uh web'",
    "Update: run 'uh upgrade' (current: v{version})",
    "Session: /new for a new session · /help for commands",
)


def startup_header(version: str | None = None) -> list[str]:
    """The three header lines printed once, above the first prompt.

    One line per surface instead of a comma-separated tip: the browser UI, the
    updater, the session commands. The version is the package's ``__version__``
    and not installed metadata - a checkout and its wheel disagree, and the
    header must describe the code that is actually running.
    """
    current = __version__ if version is None else version
    return [line.format(version=current) for line in STARTUP_HEADER]


def quiet_mode() -> bool:
    """``UH_QUIET=1``: banner and header are skipped, the prompt stays.

    Deliberately narrow: answers, tool activity and per-turn metrics are the
    user's own output, so they keep their stream. This silences the *banner* a
    script does not want, not the session.
    """
    return os.environ.get("UH_QUIET", "").strip().lower() in {"1", "true", "yes"}


def shell_command_hint(text: str) -> str | None:
    """The warning when a typed line looks like a shell command, else ``None``.

    Heuristic on the first token only, so a sentence that happens to start with
    one of those words ("git est un outil de versionnage") is also caught: that
    costs one line of explanation, never a wrong tool call. The alternative -
    sending ``uh doctor`` to the model - spends tokens to answer nonsense.
    """
    words = (text or "").strip().split(maxsplit=1)
    if words and words[0].lower() in SHELL_COMMANDS:
        return SHELL_WARNING
    return None


def _stop_embedded_web() -> None:
    """Stop a UI started by ``/web``, and the UHB it may have started. Never raises."""
    try:
        from .web import embedded
    except ImportError:  # pragma: no cover - uh.web is always importable
        embedded = None  # type: ignore[assignment]
    for stopper in (getattr(embedded, "stop_running", None), uhb_spawn.stop_running):
        if stopper is None:
            continue
        try:
            stopper()
        except Exception:  # noqa: BLE001 - a shutdown must not mask the exit code
            pass


def _run_repl(session: console.ChatSession) -> int:
    """Interactive loop: prompts go to the model, ``/command`` to the registry.

    TODO(UHB): Ctrl+C on an empty ``you>`` prompt leaves the console, unlike the
    CPython REPL which prints a fresh prompt. Left as is on purpose (a different
    behaviour, not a bug); revisit with the UHB interface so the two ergonomics
    can be compared side by side.
    """
    ui = session.console
    quiet = quiet_mode()
    if not quiet and bool(session.cfg.get("banner")) and banner.should_show():
        # Full logo: render() already degrades to the short art under 40 columns.
        ui.show_banner(short=False)
    mode = "stream" if session.stream else "blocking"
    if not quiet:
        ui.detail(f"provider {session.provider_kind} · model {session.model} · {mode}")
        ui.detail(
            f"workspace {session.workspace} · session "
            f"{display_id(session.session_id) if session.session_id else 'unsaved'}"
        )
        ui.detail(
            f"{len(session.history)} msg · ctx ~{session.context_estimate()} tok"
            f" · session {session.total_usage['total']} tok"
        )
        # UHB is optional: the toolset only exists when it answered at startup.
        uhb_line = console.uhb_header_line(session.toolsets.get("uhb"))
        if uhb_line:
            ui.detail(uhb_line)
        # Three discoverable lines, one per surface: UI, update, session.
        for line in startup_header():
            ui.detail(line)

    while True:
        ui.out("")  # one blank line before the prompt, always
        try:
            line = input("you> ")
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            break
        text = line.strip()
        if not text:
            continue
        try:
            if commands.is_command(text):
                result = commands.dispatch(session, text)
                if result.out:
                    ui.out(result.out)
                if result.err:
                    ui.errout(result.err)
                if result.exit:
                    break
                continue
            warning = shell_command_hint(text)
            if warning:
                # A shell line typed at you>: say so, spend no token, keep the
                # history clean (the user's *next* message must not inherit it).
                ui.errout(warning)
                continue
            ui.out("")  # and one between the question and the answer
            session.run_text(text)
        except KeyboardInterrupt:
            # Second net: run_text already swallows an interrupt raised by the
            # provider, this one covers a slash command or a provider rebuild.
            # The REPL never dies on Ctrl+C; the prompt comes back.
            print("", file=sys.stderr)
            ui.error(console.INTERRUPT_NOTE)

    # `/web` starts a UI in a thread: it dies with the console, whatever ended
    # the loop (/exit, EOF, Ctrl+C), so nothing keeps port 8765 afterwards.
    _stop_embedded_web()

    if session.session_id:
        ui.errout(f"resume with: uh chat --session {display_id(session.session_id)}")
    return EXIT_OK


def cmd_chat(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    session, code = _build_chat_session(args, cfg)
    if session is None:
        return code
    try:
        interactive = not args.once and sys.stdin is not None and sys.stdin.isatty()
        if interactive:
            return _run_repl(session)
        text = (getattr(args, "prompt", None) or "").strip()
        if not text:
            line = sys.stdin.readline() if sys.stdin is not None else ""
            text = (line or "").strip()
        if not text:
            return EXIT_OK
        return session.run_text(text)
    finally:
        session.close()


def cmd_console(_args: argparse.Namespace) -> int:
    """`uh` with no subcommand: chat when interactive, help when piped.

    On a first run with nothing configured, :mod:`uh.onboarding` picks a
    provider (local model, offer to pull one, local router, configured
    provider, or a guided key setup) before the console starts.
    """
    if not (sys.stdin is not None and sys.stdin.isatty()):
        if banner.should_show():
            print(banner.render())
        build_parser().print_help()
        return EXIT_OK

    cfg = _load_config(None)
    args = _chat_namespace()
    chain: list[tuple[str, Provider]] | None = None
    model: str | None = None

    try:
        chain, model = _build_chain(
            None, str(cfg.get("default_model") or "") or None,
            str(cfg.get("omnirouter_model") or ""), preferred=str(cfg.get("default_provider") or "") or None,
        )
    except ProviderError:
        chain = None

    if chain is None:
        from . import onboarding

        choice = onboarding.choose_provider()
        if choice is None:
            print(
                "uh: no provider available - run `uh providers add <name>` or start Ollama.",
                file=sys.stderr,
            )
            return EXIT_FAILURE
        try:
            provider = _make_provider(choice.kind, choice.model)
        except ProviderError as exc:
            print(f"uh: {exc}", file=sys.stderr)
            return EXIT_FAILURE
        chain = [(choice.kind, provider)]
        model = choice.model
        print(f"uh: {choice.message}", file=sys.stderr)

    session, code = _build_chat_session(args, cfg, chain=chain, model=model)
    if session is None:
        return code
    try:
        return _run_repl(session)
    finally:
        session.close()

# --------------------------------------------------------------------------- #
# uh providers free / uh router status (Jalon 6a)
# --------------------------------------------------------------------------- #
def cmd_providers_free(args: argparse.Namespace) -> int:
    """List the free-provider catalogue and (in a TTY) store a key."""
    import getpass

    from .keys import add_key, add_provider, read_providers
    from .router.catalog import CatalogError, load_catalog
    from .router.policy import CONSENT_WARNING, record_consent

    try:
        catalog = load_catalog()
    except CatalogError as exc:
        print(f"uh providers free: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    configured = read_providers()
    print("free providers, cheapest-first candidates (catalogue data, verified per entry)")
    print(f"  {'name':<11} {'status':<11} {'keys':>4}  models / limits")
    for name, spec in catalog.items():
        keys = [item for item in configured.get(name, {}).get("keys", []) if item]
        models = ", ".join(model.id for model in spec.models[:2]) or "discovered at runtime"
        label = "verified" if spec.verified == "verified" else "UNVERIFIED"
        print(f"  {name:<11} {label:<11} {len(keys):>4}  {models}")
        if spec.limits:
            print(f"  {'':<11} {'':<11} {'':>4}  limits: {spec.limits}")
        if spec.key_url:
            print(f"  {'':<11} {'':<11} {'':>4}  key: {spec.key_url}")
        if spec.notes:
            print(f"  {'':<11} {'':<11} {'':>4}  note: {spec.notes}")
        if spec.verified != "verified":
            print(
                f"  {'':<11} {'':<11} {'':>4}  excluded from the automatic chain; "
                f"opt in with: uh config set router_extra_providers {name}"
            )

    if not (sys.stdin and sys.stdin.isatty()):
        print("\nrun `uh providers free` in a terminal to add a key (input is hidden).")
        return EXIT_OK

    try:
        name = input("\nprovider to configure (empty to cancel): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("cancelled (no terminal input)", file=sys.stderr)
        return EXIT_OK
    if not name:
        return EXIT_OK
    chosen = catalog.get(name)
    if chosen is None:
        print(f"uh: unknown provider '{name}'", file=sys.stderr)
        return EXIT_FAILURE
    if chosen.verified == "unverified":
        print(f"uh: {name} is unverified in the catalogue; not using it", file=sys.stderr)
        return EXIT_FAILURE
    spec = chosen

    if spec.key_url:
        print(f"register a key at: {spec.key_url}")
    if spec.signup_note:
        print(f"note: {spec.signup_note}")
    print()
    print(CONSENT_WARNING)
    try:
        answer = input("\nStore a key for this provider and enable remote calls? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("cancelled (no terminal input)", file=sys.stderr)
        return EXIT_FAILURE
    if answer not in {"y", "yes"}:
        print("cancelled", file=sys.stderr)
        return EXIT_FAILURE

    try:
        key = getpass.getpass("API key (not echoed): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("cancelled (no terminal input)", file=sys.stderr)
        return EXIT_FAILURE
    if not key:
        print("uh: empty key, nothing stored", file=sys.stderr)
        return EXIT_FAILURE

    try:
        existing = configured.get(name)
        model = spec.models[0].id if spec.models else ""
        if existing:
            add_key(name, key)
        else:
            add_provider(name, spec.base_url, model, key)
    except OSError as exc:
        print(f"uh: cannot write the key file: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except ValueError as exc:
        print(f"uh: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    uh_config.write_layer("user", "allow_remote", True)
    record_consent()
    print(f"stored 1 key for {name}; remote providers enabled for this user")
    print("the key is never printed, logged or committed")
    return EXIT_OK


def cmd_router_status(args: argparse.Namespace) -> int:
    from .providers import build_provider
    from .router.policy import allow_remote, has_consent

    cfg = _load_config(args)
    local_only = bool(getattr(args, "local_only", False))
    allowed = allow_remote(cfg, local_only=local_only)
    print(f"router - {'remote allowed' if allowed else 'local-only: no remote call will be made'}")
    print(f"  consent: {'recorded' if has_consent(cfg) else 'not recorded yet'}")
    print(f"  strategy: {cfg.get('router_strategy') or 'priority'}")

    from .router.provider import RouterProvider

    try:
        provider = build_provider("router", "", cfg=cfg, local_only=local_only)
    except ProviderError as exc:
        print(f"uh router status: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    if not isinstance(provider, RouterProvider):
        print("uh router status: unexpected provider type", file=sys.stderr)
        return EXIT_FAILURE

    rows = provider.status_rows()
    if not rows:
        print("  no provider in the catalogue")
        return EXIT_OK
    print(f"  {'provider':<11} {'status':<20} {'keys':>4} {'chain':<6} {'state':<12} last error")
    for row in rows:
        if not row["keys"]:
            state = "no key"
        else:
            state = "ready" if row["available"] else f"cooling {row['cooling_seconds']}s"
        chain = "yes" if row["selected"] else "no"
        print(
            f"  {row['provider']:<11} {row['label']:<20} {row['keys']:>4} {chain:<6} "
            f"{state:<12} {row['last_error'] or '-'}"
        )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# uh web (Jalon 5)
# --------------------------------------------------------------------------- #
def cmd_router_serve(args: argparse.Namespace) -> int:
    """Local OpenAI-compatible endpoint. Loopback only, own token."""
    try:
        from .router.serve import run_server
    except ImportError as exc:
        print(
            "uh router serve: the [web] extra is required "
            f"({type(exc).__name__}: {exc}).",
            file=sys.stderr,
        )
        print('  install it with: pip install "uh-harness[web]"', file=sys.stderr)
        return EXIT_FAILURE
    return run_server(host=str(args.host), port=int(args.port))


def cmd_router_token_show(_args: argparse.Namespace) -> int:
    from .router.serve import ensure_token, token_path

    token = ensure_token()
    print(f"token file: {token_path()}")
    print(token)
    print("this token is a local secret: it gives access to your provider quotas.")
    print("copy it into the client; never paste it in a chat, a URL or a commit.")
    return EXIT_OK


def cmd_router_token_rotate(_args: argparse.Namespace) -> int:
    from .router.serve import rotate_token, token_path

    token = rotate_token()
    print(f"token file: {token_path()} (replaced)")
    print(token)
    print("clients using the previous token now get 401.")
    return EXIT_OK


def cmd_web(args: argparse.Namespace) -> int:
    """Serve the local web interface. The heavy imports stay inside the extra."""
    host = (getattr(args, "host", None) or "127.0.0.1").strip()
    try:
        from .web.app import LOOPBACK_NAMES, run_server
    except ImportError as exc:
        print(
            "uh web: the [web] extra is not installed "
            f"({type(exc).__name__}: {exc}).",
            file=sys.stderr,
        )
        print('  install it with: pip install "uh-harness[web]"', file=sys.stderr)
        return EXIT_FAILURE

    if host not in LOOPBACK_NAMES:
        print(
            f"uh web: WARNING binding {host} (not loopback). The interface is reachable "
            "from the network and the token in the URL is the only protection.",
            file=sys.stderr,
        )

    workspace = Path(args.workspace).expanduser() if getattr(args, "workspace", None) else None

    static_dir = None
    raw_static = getattr(args, "static_dir", None)
    if raw_static:
        static_dir = Path(raw_static).expanduser()
        if not (static_dir / "index.html").is_file():
            print(
                f"uh web: {static_dir} does not contain index.html",
                file=sys.stderr,
            )
            return EXIT_FAILURE

    # The second brain starts with the cockpit: without it the desktop shortcut
    # opens an interface whose tools are all absent. A failure here is a warning,
    # never a crash - the UI is useful on its own.
    if not getattr(args, "no_uhb", False):
        try:
            child = uhb_spawn.ensure()
        except uhb_spawn.SpawnError as exc:
            print(f"uh web: UHB not started ({exc})", file=sys.stderr)
        else:
            if child is not None:
                print(f"uh web: UHB started (pid {child.pid}) - {child.url} - log {child.log}")
    try:
        return run_server(
            host=host,
            port=int(args.port or 8765),
            open_browser=not getattr(args, "no_open", False),
            allow_terminal=bool(getattr(args, "allow_terminal", False)),
            workspace=workspace,
            static_dir=static_dir,
        )
    finally:
        # Ctrl+C, an error, a normal exit: the child goes with the parent.
        uhb_spawn.stop_running()


def _log_pass(line: str, started: float) -> None:
    """One line per pass in ``dreams/schedule.log``: the only trace of 03:00."""
    from .dream import dreams_dir

    try:
        path = dreams_dir() / "schedule.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}  [{time.time() - started:.0f}s]  {line}\n")
    except OSError:
        pass  # a log that cannot be written must not fail the pass


def _run_dream_pass(args: argparse.Namespace) -> int:
    """`uh dream --now`: one read-only pass, with a budget that actually stops it."""
    from .dream_pass import DreamConfig, DreamScheduler

    sources = tuple(
        name.strip() for name in (args.source or "siyuan,rag").split(",") if name.strip()
    )
    config = DreamConfig(
        sources=sources or ("siyuan", "rag"),
        budget_tokens=int(args.budget) if args.budget is not None else DreamConfig().budget_tokens,
        write=not getattr(args, "no_write", False),
    )

    provider = None
    model = ""
    provider_kind = ""
    if not args.dry_run:
        cfg = uh_config.load()
        try:
            chain, model = _build_chain(None, str(cfg.get("default_model") or "") or None,
                                        preferred=str(cfg.get("default_provider") or "") or None)
        except Exception as exc:  # noqa: BLE001 - a pass without a model still inventories
            print(f"uh dream: no provider ({type(exc).__name__}: {exc}); inventory only", file=sys.stderr)
            chain = []
        if chain:
            provider = chain[0][1]
            provider_kind = chain[0][0]

    if getattr(args, "start_uhb", False):
        from . import uhb_spawn

        try:
            uhb_spawn.ensure()
        except Exception as exc:  # noqa: BLE001 - the pass reports a skip by itself
            # stderr is a console nobody sees at 03:00: leave a line in the log,
            # otherwise a skipped night and a night that never ran look alike.
            _log_pass(f"uhb start failed: {type(exc).__name__}: {exc}", time.time())
            print(f"uh dream: could not start UHB ({type(exc).__name__}: {exc})", file=sys.stderr)

    if getattr(args, "start_ollama", False):
        # Best effort, in this order of importance: a local model is cheaper, an
        # absent Ollama is normal, and the pass must run either way.
        from . import ollama_spawn

        try:
            state = ollama_spawn.ensure()
        except Exception as exc:  # noqa: BLE001 - a start failure is a log line
            _log_pass(f"ollama: failed {type(exc).__name__}: {exc}", time.time())
        else:
            _log_pass(
                f"ollama: {state}"
                if state == ollama_spawn.NOT_INSTALLED
                else f"ollama: {state}",
                time.time(),
            )

    fallback = next((entry[1] for entry in chain if entry[0] != "ollama"), None) if provider else None
    engine = DreamScheduler(
        config=config,
        provider=provider,
        provider_kind=provider_kind,
        fallback_provider=fallback,
    )
    started = time.time()
    try:
        report = engine.run_once(dry_run=bool(args.dry_run))
    except Exception as exc:  # noqa: BLE001 - a nightly run has no console to crash on
        # Nobody watches 03:00: the only proof a pass ran (or died) is a line in
        # a file. Without this, Task Scheduler says "success" and nothing happened.
        _log_pass(f"crashed: {type(exc).__name__}: {exc}", started)
        raise
    # What answered, not what UHB would have preferred: the plan and the provider
    # chain are two different things, and only one of them spent the tokens.
    chosen = f"{provider_kind}/{model}" if provider_kind else "?"
    if provider_kind and provider_kind != "ollama":
        try:
            from . import ollama_spawn

            if ollama_spawn.is_up():
                _log_pass(
                    "ollama: available but no capable model (need >= 3B), falling back to cloud",
                    time.time(),
                )
        except Exception:  # noqa: BLE001 - a log line never fails a pass
            pass
    _log_pass(
        f"{report.status} - {len(report.ideas)} idea(s), {report.tokens.get('total', 0)} tokens, "
        f"{report.wall_time_s:.0f}s, routed: {chosen}"
        + (f", {report.artifacts[0]}" if report.artifacts else ""),
        started,
    )
    if getattr(args, "start_uhb", False):
        from . import uhb_spawn

        try:
            uhb_spawn.stop_running()
        except Exception:  # noqa: BLE001 - a server left up is not a pass failure
            pass

    if getattr(args, "no_write", False) or args.dry_run:
        print(report.render())
    else:
        print(f"uh dream: {report.status} - {len(report.ideas)} idea(s), "
              f"{report.tokens.get('total', 0)} tokens, {report.wall_time_s:.0f}s")
        for path in report.artifacts:
            print(f"  {path}")
        if report.reason:
            print(f"  {report.reason}")
        if not report.artifacts:
            print("  (nothing written: the pass had no output folder)")
    if report.status == "skipped":
        return EXIT_FAILURE
    return EXIT_OK


def cmd_dream(args: argparse.Namespace) -> int:
    """`uh dream`: snapshots and the journal. The pass itself lands in P4.2."""
    from . import dream as dream_mod

    if args.status:
        from . import dream_schedule

        state = dream_schedule.status(args.at)
        print(f"installed : {state['installed']} ({state['mechanism'] or 'nothing'})")
        print(f"task      : {'yes' if state['task'] else 'no'}")
        print(f"startup   : {state['startup'] or 'no'}")
        print(f"last pass : {state['last_pass'] or 'never'}")
        print(f"next run  : {state['next_run'] or '-'}")
        return EXIT_OK

    if args.uninstall:
        from . import dream_schedule

        try:
            removed = dream_schedule.uninstall()["removed"]
        except dream_schedule.ScheduleError as exc:
            print(f"uh dream: {exc}", file=sys.stderr)
            return EXIT_FAILURE
        if removed:
            for item in removed:
                print(f"removed: {item}")
        else:
            print("uh dream: nothing was installed")
        return EXIT_OK

    if args.schedule:
        from . import dream_schedule

        try:
            installed = dream_schedule.install(at=args.at)
        except dream_schedule.ScheduleError as exc:
            print(f"uh dream: {exc}", file=sys.stderr)
            return EXIT_FAILURE
        print(f"scheduled: {installed['detail']}")
        if installed["path"]:
            print(f"  script: {installed['path']}")
        print(f"  command: {installed['command']}")
        return EXIT_OK

    if args.now:
        return _run_dream_pass(args)

    if args.list_snapshots:
        found = dream_mod.list_snapshots()
        if not found:
            print(f"no snapshot in {dream_mod.dreams_dir()}")
            return EXIT_OK
        for item in found:
            files = "?" if item["files"] is None else item["files"]
            size = "?" if item["bytes"] is None else f"{item['bytes'] / 1024:.0f} KB"
            print(f"  {item['id']:<26} {str(item['created'] or '?'):<20} {files:>4} files  {size}")
        return EXIT_OK

    if args.journal:
        entries = dream_mod.DreamJournal().read(args.date)
        if not entries:
            print(f"uh dream: no entry for {args.date or 'today'}")
            return EXIT_OK
        for entry in entries:
            reason = f"  ({entry['reason']})" if entry.get("reason") else ""
            print(
                f"  {entry.get('timestamp', '?')}  {str(entry.get('type', '?')):<7}"
                f"  {entry.get('path', '')}{reason}"
            )
        return EXIT_OK

    if args.rollback:
        snapshot_id = args.snapshot or None
        target = snapshot_id or "the latest snapshot"
        if not args.yes:
            if not sys.stdin.isatty():
                print(
                    "uh dream: refusing to roll back without a terminal; pass --yes.",
                    file=sys.stderr,
                )
                return EXIT_FAILURE
            answer = input(f"Restore {target} over the current files? [y/N] ").strip().lower()
            if answer not in ("y", "yes", "o", "oui"):
                print("uh dream: nothing done")
                return EXIT_OK
        try:
            restored = dream_mod.rollback(snapshot_id)
        except dream_mod.DreamError as exc:
            print(f"uh dream: {exc}", file=sys.stderr)
            return EXIT_FAILURE
        print(f"restored {restored} file(s) from {target}")
        print(f"  safety copy kept in {dream_mod.dreams_dir()}")
        return EXIT_OK

    if args.snapshot is not None:
        try:
            created = dream_mod.create_snapshot(journal=dream_mod.DreamJournal())
        except dream_mod.DreamError as exc:
            print(f"uh dream: {exc}", file=sys.stderr)
            return EXIT_FAILURE
        manifest = json.loads((created / "manifest.json").read_text(encoding="utf-8"))
        print(f"snapshot: {created}")
        print(f"  {len(manifest['files'])} file(s), {manifest['total_bytes'] / 1024:.0f} KB")
        if not manifest["files"]:
            print("  (nothing to copy: no wiki/, docs/ or MEMORY.md here)")
        return EXIT_OK

    print("uh dream: nothing to do. Use --snapshot, --rollback, --journal or --list-snapshots.")
    return EXIT_FAILURE


def cmd_install_shortcut(_args: argparse.Namespace) -> int:
    """Windows-only: a desktop shortcut that starts the web UI in one click."""
    from . import shortcut

    if not shortcut.windows():
        print(
            f"uh install-shortcut: Windows only (this is {sys.platform}).",
            file=sys.stderr,
        )
        print(
            "  elsewhere, point your desktop launcher at `uh web` yourself.",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    try:
        created = shortcut.install()
    except shortcut.ShortcutError as exc:
        print(f"uh install-shortcut: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"shortcut: {created.link}")
    print(f"  launcher: {created.script}")
    print(f"  icon: {created.icon}")
    print("  double-click to open the web UI; no admin rights, nothing starts at boot")
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
        "--local-only",
        action="store_true",
        help="never call a remote provider, whatever the config says",
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
    providers_sub.add_parser("free", help="list the free-provider catalogue and store a key")
    providers_sub.add_parser("list", help="show providers and their status")
    providers_add = providers_sub.add_parser(
        "add", help="store an OpenAI-compatible provider (key never printed)"
    )
    providers_add.add_argument("name", help="short name, e.g. groq")
    providers_add.add_argument("--base-url", help="e.g. https://api.groq.com/openai/v1")
    providers_add.add_argument("--model", help="model id used by default")
    # No --key on purpose: argv is visible in the process list and in shell
    # history. The key comes from UH_PROVIDER_KEY, stdin, or a masked prompt.
    providers_test = providers_sub.add_parser(
        "test", help="send one 'ping' to a provider (no session content)"
    )
    providers_test.add_argument("name")
    providers_remove = providers_sub.add_parser("remove", help="forget a stored provider")
    providers_remove.add_argument("name")

    keys_cmd = sub.add_parser("keys", help="inspect and manage stored keys (values never shown)")
    keys_sub = keys_cmd.add_subparsers(dest="keys_command", metavar="<action>")
    keys_sub.add_parser("list", help="present/absent and how many keys per provider")
    keys_add = keys_sub.add_parser("add", help="store a key (prompt, stdin or UH_PROVIDER_KEY)")
    keys_add.add_argument("name")
    keys_remove = keys_sub.add_parser("remove", help="delete a key by index (never by value)")
    keys_remove.add_argument("name")
    keys_remove.add_argument("index", type=int)

    docs_cmd = sub.add_parser("docs", help="show the local documentation (no network)")
    docs_cmd.add_argument("--open", action="store_true", help="open the README in a browser")

    upgrade = sub.add_parser("upgrade", help="upgrade uh-harness with pip, then restart")
    upgrade.add_argument(
        "--restart",
        action="store_true",
        help="re-exec uh once the upgrade succeeded (default: print a hint only)",
    )
    upgrade.add_argument(
        "--user",
        action="store_true",
        help="allow a user-level install when no virtualenv is active",
    )
    upgrade.add_argument(
        "--dry-run",
        action="store_true",
        help="let pip report what it would install, change nothing",
    )

    router = sub.add_parser("router", help="free-provider router (Jalon 6a)")
    router_sub = router.add_subparsers(dest="router_command", metavar="<action>")
    router_sub.add_parser("status", help="per-provider state: keys, cooldown, last error")
    router_serve = router_sub.add_parser(
        "serve", help="local OpenAI-compatible endpoint (loopback only)"
    )
    router_serve.add_argument("--port", type=int, default=8780, help="port (default 8780)")
    router_serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="loopback only; any other address is refused (no override)",
    )
    router_token = router_sub.add_parser(
        "token", help="token used by `uh router serve` (separate from the web token)"
    )
    token_sub = router_token.add_subparsers(dest="token_command", metavar="<action>")
    token_sub.add_parser("show", help="print the token, creating it on first use")
    token_sub.add_parser("rotate", help="replace the token; existing clients stop working")

    sub.add_parser(
        "install-shortcut",
        help="create a desktop shortcut to the web UI (Windows only)",
    )

    dream_cmd = sub.add_parser(
        "dream",
        help="journal and snapshots for the dream pass (P4.1: nothing runs on its own yet)",
    )
    dream_cmd.add_argument(
        "--snapshot",
        nargs="?",
        const="",
        default=None,
        metavar="ID",
        help="create a snapshot; with --rollback, the snapshot to restore",
    )
    dream_cmd.add_argument(
        "--rollback", action="store_true", help="restore a snapshot (the latest by default)"
    )
    dream_cmd.add_argument("--journal", action="store_true", help="print the journal of a day")
    dream_cmd.add_argument("--date", default=None, metavar="YYYY-MM-DD", help="day for --journal")
    dream_cmd.add_argument("--list-snapshots", action="store_true", help="list available snapshots")
    dream_cmd.add_argument("--now", action="store_true", help="run one pass (read-only on the sources)")
    dream_cmd.add_argument("--dry-run", action="store_true", help="inventory only: no model call, nothing written")
    dream_cmd.add_argument("--budget", type=int, default=None, metavar="TOKENS", help="token budget for this pass")
    dream_cmd.add_argument(
        "--source", default=None, metavar="siyuan,rag", help="sources to read (default siyuan,rag)"
    )
    dream_cmd.add_argument("--no-write", action="store_true", help="print the report, write nothing")
    dream_cmd.add_argument(
        "--schedule", action="store_true", help="install the nightly pass (Task Scheduler or Startup)"
    )
    dream_cmd.add_argument("--at", default="03:00", metavar="HH:MM", help="hour of the nightly pass")
    dream_cmd.add_argument("--uninstall", action="store_true", help="remove the nightly pass")
    dream_cmd.add_argument("--status", action="store_true", help="is it installed, when did it last run")
    dream_cmd.add_argument(
        "--start-uhb", action="store_true", help="start UHB for this pass, then stop it"
    )
    dream_cmd.add_argument(
        "--start-ollama",
        action="store_true",
        help="start Ollama if it is installed (never installed, never required)",
    )
    dream_cmd.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    web = sub.add_parser("web", help="local web interface (needs the [web] extra)")
    web.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    web.add_argument("--port", type=int, default=8765, help="port (default 8765)")
    web.add_argument("--no-open", action="store_true", help="do not open a browser")
    web.add_argument(
        "--no-uhb",
        action="store_true",
        help="do not start UHB alongside the UI (the uhb tools are then absent)",
    )
    web.add_argument(
        "--allow-terminal",
        action="store_true",
        help="enable the terminal toolset in the web UI (dangerous, off by default)",
    )
    web.add_argument("--workspace", help="workspace for the file tools")
    web.add_argument(
        "--static-dir",
        help="serve a replacement interface from this folder (must contain index.html)",
    )

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
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    # --local-only wins over config and environment: one global kill switch.
    if getattr(args, "local_only", False):
        os.environ["UH_ALLOW_REMOTE"] = "0"

    if args.command is None:
        return cmd_console(args)

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
            if args.providers_command == "free":
                return cmd_providers_free(args)
            if args.providers_command == "add":
                return cmd_providers_add(args)
            if args.providers_command == "remove":
                return cmd_providers_remove(args)
            if args.providers_command == "test":
                return cmd_providers_test(args)
            return _sub_help(parser, "providers")
        if args.command == "keys":
            key_handlers: dict[str, Callable[[argparse.Namespace], int]] = {
                "list": cmd_keys_list,
                "add": cmd_keys_add,
                "remove": cmd_keys_remove,
            }
            handler = key_handlers.get(args.keys_command)
            if handler is None:
                return _sub_help(parser, "keys")
            return handler(args)
        if args.command == "docs":
            return cmd_docs(args)
        if args.command == "upgrade":
            return cmd_upgrade(args)
        if args.command == "router":
            if args.router_command == "status":
                return cmd_router_status(args)
            if args.router_command == "serve":
                return cmd_router_serve(args)
            if args.router_command == "token":
                if args.token_command == "show":
                    return cmd_router_token_show(args)
                if args.token_command == "rotate":
                    return cmd_router_token_rotate(args)
                return _sub_help(parser, "router token")
            return _sub_help(parser, "router")
        if args.command == "web":
            return cmd_web(args)
        if args.command == "install-shortcut":
            return cmd_install_shortcut(args)
        if args.command == "dream":
            return cmd_dream(args)
        if args.command == "sessions":
            session_handlers: dict[str, Callable[[argparse.Namespace], int]] = {
                "list": cmd_sessions_list,
                "show": cmd_sessions_show,
                "search": cmd_sessions_search,
                "delete": cmd_sessions_delete,
                "resume": cmd_sessions_resume,
            }
            handler = session_handlers.get(args.sessions_command)
            if handler is None:
                return _sub_help(parser, "sessions")
            return handler(args)
        if args.command == "config":
            config_handlers: dict[str, Callable[[argparse.Namespace], int]] = {
                "show": cmd_config_show,
                "get": cmd_config_get,
                "set": cmd_config_set,
                "path": cmd_config_path,
            }
            handler = config_handlers.get(args.config_command)
            if handler is None:
                return _sub_help(parser, "config")
            return handler(args)
        if args.command == "memory":
            memory_handlers: dict[str, Callable[[argparse.Namespace], int]] = {
                "show": cmd_memory_show,
                "migrate": cmd_memory_migrate,
            }
            handler = memory_handlers.get(args.memory_command)
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
