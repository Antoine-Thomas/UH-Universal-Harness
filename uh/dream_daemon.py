"""The daydream daemon: dream while you are away, not at a fixed hour.

A report at 03:00 is a report nobody asks for. The useful moment is the one where
you stopped typing - that is when the second brain has something new to review and
nobody is competing for the GPU.

This module only decides *when*. It never writes to SiYuan, never reindexes, and
never asks for a model that is not already configured: it starts a normal pass,
the same one `uh dream --now` runs, and stops when you come back.
"""

from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path
from typing import Callable, Optional

from .config import uh_home

#: 15 minutes is long enough to be a real pause, short enough to happen twice a day.
DEFAULT_IDLE_MINUTES = 15
#: One check per minute: a laptop that wakes up every 5 s for nothing is a laptop
#: whose fans are audible.
TICK_SECONDS = 60.0
#: How long a single pass may run before the daemon gives the machine back.
MAX_PASS_SECONDS = 900.0
#: A pass costs model tokens and a folder; two an hour apart is already generous.
MIN_PASS_INTERVAL = 3600.0

IDLE_ENV = "UH_IDLE_MINUTES"
STOP_FILE = "dream-daemon.stop"
STATUS_FILE = "dream-daemon.status"


def state_path(name: str) -> Path:
    return uh_home() / name


def idle_minutes() -> float:
    raw = (os.environ.get(IDLE_ENV) or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return float(DEFAULT_IDLE_MINUTES)
    return value if value > 0 else float(DEFAULT_IDLE_MINUTES)


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> Optional[float]:
    """Seconds since the last mouse or keyboard input, or ``None`` if unknown.

    ``None`` matters: a daemon that cannot tell whether you are there must not
    start a pass on a guess. Off Windows, this returns ``None`` by design.
    """
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        millis = int(windll.kernel32.GetTickCount()) - int(info.dwTime)
        return max(0.0, float(millis) / 1000.0)
    except Exception:  # noqa: BLE001 - an unavailable API is "unknown", not a crash
        return None


def low_priority() -> bool:
    """Ask Windows for below-normal scheduling: dream, do not heat the laptop."""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False
    try:
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        handle = windll.kernel32.GetCurrentProcess()
        return bool(windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS))
    except Exception:  # noqa: BLE001
        return False


def write_status(**fields: object) -> None:
    try:
        path = state_path(STATUS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{key}={value}" for key, value in fields.items()]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def read_status() -> dict:
    path = state_path(STATUS_FILE)
    if not path.is_file():
        return {"state": "stopped"}
    result = {"state": "unknown"}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                result[key.strip()] = value.strip()
    except OSError:
        return {"state": "unknown"}
    return result


def stop() -> bool:
    """Ask a running daemon to leave after the current pass."""
    try:
        state_path(STOP_FILE).write_text("stop\n", encoding="utf-8")
        return True
    except OSError:
        return False


def running() -> bool:
    return state_path(STOP_FILE).is_file()


def clear_stop() -> None:
    try:
        state_path(STOP_FILE).unlink()
    except OSError:
        pass


def run(
    pass_runner: Callable[[], object],
    *,
    tick: float = TICK_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    idle: Callable[[], Optional[float]] = idle_seconds,
    max_ticks: Optional[int] = None,
) -> dict:
    """Wait for a long enough pause, run one pass, then wait again.

    Decisions are taken once per tick so the loop is testable without a clock:
    everything that touches sleep, time or the OS is injected.
    """
    threshold = idle_minutes() * 60.0
    low_priority()
    # Do not clear the stop file here: asking to stop before starting is a valid
    # request ("do not dream now"), and a clean exit is what removes it.
    ticks = 0
    passes = 0
    last: str = ""
    last_pass_at: Optional[float] = None
    state_seen = "idle-monitoring"
    write_status(state="idle-monitoring", threshold_minutes=idle_minutes(), passes=0)

    while not running():
        ticks += 1
        if max_ticks is not None and ticks > max_ticks:
            break
        idle_for = idle()
        if idle_for is None:
            state_seen = "unsupported"
            write_status(state="unsupported", threshold_minutes=idle_minutes(), passes=passes)
            sleep(tick)
            continue
        if idle_for < threshold:
            write_status(
                state="idle-monitoring",
                threshold_minutes=idle_minutes(),
                idle_seconds=round(idle_for, 1),
                passes=passes,
            )
            sleep(tick)
            continue

        # Insurance on top of the wait above: whatever the idle sensor says, two
        # passes are never worth running an hour apart, let alone eight seconds.
        if last_pass_at is not None and now() - last_pass_at < MIN_PASS_INTERVAL:
            sleep(tick)
            continue

        started = now()
        write_status(state="dreaming", threshold_minutes=idle_minutes(), passes=passes,
                     idle_seconds=round(idle_for, 1))
        try:
            pass_runner()
            passes += 1
            last = "ok"
        except Exception as exc:  # noqa: BLE001 - the daemon outlives a bad pass
            last = f"failed: {type(exc).__name__}: {exc}"
        elapsed = now() - started
        if elapsed > MAX_PASS_SECONDS:  # pragma: no cover - depends on the model
            last = f"{last} (long pass: {elapsed:.0f}s)"
        write_status(state="idle-monitoring", threshold_minutes=idle_minutes(),
                     passes=passes, last_pass=last)
        last_pass_at = now()
        # Do not dream twice for the same absence: wait for real input first.
        # `running()` means "a stop has been requested", so this guard used to be
        # inverted - it waited only when asked to leave, and started a new pass
        # straight away otherwise. One night, 3594 passes, one every 8 seconds.
        # The wait counts ticks like the outer loop: a bounded run must be able to
        # end while the user is away, otherwise only a stop file frees it.
        while not running() and (idle() or 0.0) >= threshold:
            ticks += 1
            if max_ticks is not None and ticks > max_ticks:
                break
            sleep(tick)

    write_status(state="stopped", passes=passes, last_pass=last, last_state=state_seen)
    clear_stop()
    return {"passes": passes, "last_pass": last, "ticks": ticks}


__all__ = [
    "DEFAULT_IDLE_MINUTES",
    "TICK_SECONDS",
    "clear_stop",
    "idle_minutes",
    "idle_seconds",
    "low_priority",
    "read_status",
    "run",
    "running",
    "state_path",
    "stop",
    "write_status",
]
