"""Start Ollama when it is installed - never install it, never require it.

Ollama is an optimisation for people who already have it: a local model costs no
tokens and no network. UH does not download it, does not bundle it, and the pass
runs exactly the same without it - the router chain simply starts with the free
cloud tier instead of the local one.

That is why there is no ``stop()`` here: Ollama is a service the user keeps, not
a child of one pass. UHB is spawned and killed with the UI; Ollama is started if
absent and then left alone, so the next pass finds it already up.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

from .config import uh_home

#: Where Ollama listens when nothing says otherwise.
DEFAULT_URL = "http://127.0.0.1:11434"
#: An escape hatch for tests and for a portable install.
BINARY_ENV = "UH_OLLAMA_BINARY"
#: A short probe: a local service that takes 2 s to answer is not answering.
PROBE_TIMEOUT = 2.0
#: A cold start of the server, on a busy laptop, takes a few seconds.
START_TIMEOUT = 15.0
POLL_INTERVAL = 0.5

ALREADY_UP = "already_up"
STARTED = "started"
NOT_INSTALLED = "not_installed"


def log_path() -> Path:
    """The same log file UHB writes to: two services, one file to read at 03:00."""
    return uh_home() / "uhb.log"


def url() -> str:
    return (os.environ.get("UH_OLLAMA_URL") or DEFAULT_URL).rstrip("/")


def is_up(target: Optional[str] = None, timeout: float = PROBE_TIMEOUT) -> bool:
    """True when ``GET /api/tags`` answers 200."""
    try:
        with httpx.Client(timeout=timeout) as http:
            return http.get(f"{(target or url())}/api/tags").status_code == 200
    except Exception:  # noqa: BLE001 - a refused connection is a "no"
        return False


def find_binary() -> Optional[Path]:
    """``UH_OLLAMA_BINARY``, then the usual Windows install, then ``PATH``.

    When the variable is set it is authoritative: a path that does not exist
    means "not installed", which is how the no-Ollama case is tested without
    uninstalling anything.
    """
    override = (os.environ.get(BINARY_ENV) or "").strip()
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_file() else None
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "Programs" / "Ollama" / "ollama.exe"
        if candidate.is_file():
            return candidate
    found = shutil.which("ollama")
    return Path(found) if found else None


def ensure(target: Optional[str] = None, timeout: float = START_TIMEOUT) -> str:
    """``already_up``, ``started`` or ``not_installed``. Never raises a pass."""
    base = target or url()
    if is_up(base):
        return ALREADY_UP
    binary = find_binary()
    if binary is None:
        return NOT_INSTALLED

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        with log_path().open("ab") as sink:
            sink.write(f"\n[uh] starting ollama serve ({binary})\n".encode("utf-8", "replace"))
            sink.flush()
            subprocess.Popen(  # noqa: S603 - a fixed binary, no shell
                [str(binary), "serve"],
                stdout=sink,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
    except OSError:
        return NOT_INSTALLED

    deadline = time.monotonic() + max(1.0, timeout)
    while time.monotonic() < deadline:
        if is_up(base, timeout=1.0):
            return STARTED
        time.sleep(POLL_INTERVAL)
    # Started, but the API never answered: the router will fall through to the
    # cloud tier by itself, which is the same outcome as "not installed".
    return NOT_INSTALLED


def models(target: Optional[str] = None, timeout: float = PROBE_TIMEOUT) -> list:
    """Installed model names, or an empty list - never an exception."""
    try:
        with httpx.Client(timeout=timeout) as http:
            payload = http.get(f"{(target or url())}/api/tags").json()
    except Exception:  # noqa: BLE001 - not answering is not an error here
        return []
    entries = payload.get("models") if isinstance(payload, dict) else None
    names = []
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("name"):
            names.append(str(entry["name"]))
    return names


def _boost(days: int = 30) -> dict:
    """Tokens the local model saved, counted from the passes that used it.

    Not a forecast: the number is the number of logged passes routed to a local
    model, times the average size of a pass measured on this machine. If no pass
    used a local model, the answer is 0 and the reason says so.
    """
    import re

    from .dream import dreams_dir

    # The pass log, not the UHB log: only schedule.log records "routed: ...".
    log = dreams_dir() / "schedule.log"
    local = 0
    seen = 0
    if log.is_file():
        pattern = re.compile(r"routed:\s*(\S+)")
        try:
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]:
                match = pattern.search(line)
                if not match:
                    continue
                seen += 1
                if match.group(1).lower().startswith(("ollama/", "qwen", "lfm")):
                    local += 1
        except OSError:
            pass
    cloud = 0
    cloud_tokens = 0
    if log.is_file():
        try:
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]:
                match = pattern.search(line)
                size = re.search(r"([0-9]+) tokens", line)
                if not match or not size:
                    continue
                if not match.group(1).lower().startswith(("ollama/", "qwen", "lfm")):
                    cloud += 1
                    cloud_tokens += int(size.group(1))
        except OSError:
            pass
    average = int(cloud_tokens / cloud) if cloud else 0
    saved = local * average
    basis = (
        f"{local} local passes x {average} avg cloud tokens (from {cloud} cloud passes)"
        if average
        else f"{local} local passes, no cloud pass to average yet"
    )
    return {
        "local_passes": local,
        "cloud_passes": cloud,
        "passes_logged": seen,
        "average_cloud_tokens": average,
        "tokens_saved_estimate": saved,
        "basis": basis,
    }


#: Models known to hold a JSON instruction. Ordered by usefulness for a review
#: pass, not by size: an 8B that answers beats a 14B that rambles.
PREFERRED_MODELS = ("qwen3:8b", "qwen2.5:7b", "qwen2.5:3b", "qwen3:4b")
#: Below this, a model is not asked to produce a structured report.
MIN_PARAMETERS_B = 3.0
MODEL_ENV = "UH_OLLAMA_MODEL"


def _parameters(name: str) -> float:
    """``qwen2.5:3b`` -> 3.0. ``None``-ish when the tag carries no size."""
    tag = name.rsplit(":", 1)[-1].lower()
    number = ""
    for char in tag:
        if char.isdigit() or char == ".":
            number += char
        elif char == "b" and number:
            try:
                return float(number)
            except ValueError:
                return 0.0
        else:
            break
    return 0.0


def pick_model(names: Optional[list] = None) -> Optional[str]:
    """The local model a pass should use, or ``None`` if none is capable.

    Alphabetical order gave ``lfm3:latest`` - a model that answered with no JSON
    and produced an empty report. A free pass that produces nothing is not a
    saving, so capability decides: ``UH_OLLAMA_MODEL`` first, then the preferred
    list, then any installed model of at least 3B.
    """
    override = (os.environ.get(MODEL_ENV) or "").strip()
    installed = [str(name) for name in (names if names is not None else models())]
    if override:
        if override in installed:
            return override
        return None
    for candidate in PREFERRED_MODELS:
        if candidate in installed:
            return candidate
    sized = [name for name in installed if _parameters(name) >= MIN_PARAMETERS_B]
    if sized:
        return sorted(sized, key=_parameters)[-1]
    return None


def status(target: Optional[str] = None) -> dict:
    """The three states the UI shows, always as a 200 with a short reason."""
    installed = find_binary() is not None
    running = is_up(target)
    if running:
        boost = "active"
        reason = "Ollama answers: local runs cost no tokens"
    elif installed:
        boost = "available"
        reason = "Ollama is installed but not running"
    else:
        boost = "unavailable"
        reason = "Ollama is not installed"
    return {
        "installed": installed,
        "running": running,
        "models": models(target) if running else [],
        "boost": boost,
        "reason": reason,
        "optional": True,
        "url": target or url(),
        "savings": _boost(),
    }


__all__ = [
    "status",
    "models",
    "ALREADY_UP",
    "BINARY_ENV",
    "DEFAULT_URL",
    "NOT_INSTALLED",
    "STARTED",
    "ensure",
    "find_binary",
    "is_up",
    "log_path",
    "url",
]
