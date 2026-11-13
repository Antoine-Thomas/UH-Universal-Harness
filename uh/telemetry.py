"""The cockpit's numbers: what was spent, and what the machine is doing now.

All three sources already exist, so this module adds no service and no
dependency:

* ``sessions.metadata.decisions`` - one record per finished turn, written by
  :mod:`uh.decision`. ``usage.total`` is what the provider reported; a provider
  that reports nothing is counted as a turn, never as zero tokens;
* ``$UH_HOME/dreams/schedule.log`` - one line per dream pass, with its status,
  its tokens, its wall time and what answered;
* the machine - CPU, RAM and VRAM, sampled at request time, ``None`` when a probe
  cannot answer.

Deliberately absent: **cost**. UH ships no price table, and a euro figure derived
from a hardcoded rate would be a guess wearing a currency symbol. The payload
carries ``cost: {"value": None, "reason": ...}`` and the panel prints the reason
instead of a number nobody can defend. Give UH a price table (provider, model,
currency, input/output rates) and the number becomes honest; until then, tokens
are the truth.

Nothing in here writes, and no probe raises: an absent GPU is ``None``.
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: The window the panel draws. Seven days is the question "what did this week
#: cost me", not a storage decision - the store keeps everything.
WINDOW_DAYS = 7

#: Providers that run on this machine. Everything else is billed somewhere.
LOCAL_PROVIDERS = ("ollama", "llamacpp", "llama.cpp", "lmstudio", "vllm", "local")

#: ``2026-11-13 16:59:48  [163s]  ok - 2 idea(s), 2063 tokens, 164s, routed: ...``
PASS_LINE = re.compile(
    r"^(?P<at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"\[(?P<elapsed>\d+)s\]\s+"
    r"(?P<status>[a-z][a-z-]*)\s+-\s+"
    r"(?P<detail>.*)$"
)
IDEAS = re.compile(r"(\d+)\s+idea\(s\)")
TOKENS = re.compile(r"(\d+)\s+tokens")
WALL = re.compile(r"(\d+)s\s*(?:,|$)")
ROUTED = re.compile(r"routed:\s*(\S+?)\.?(?:,|$)")


# --------------------------------------------------------------------------- #
# dream passes
# --------------------------------------------------------------------------- #
def is_local(provider: object) -> bool:
    """``True`` when the provider ran here, so nothing was billed for it."""
    return str(provider or "").strip().lower() in LOCAL_PROVIDERS


def parse_pass_line(line: str) -> Optional[Dict[str, Any]]:
    """One parsed pass, or ``None`` for a diagnostic line.

    ``schedule.log`` mixes both kinds: a real pass reads ``<status> - ...``, while
    ``ollama: already_up`` is a note about the environment. Dropping the notes is
    the point - a log line is not a measurement.
    """
    match = PASS_LINE.match((line or "").strip())
    if match is None:
        return None
    detail = match.group("detail")
    routed = ROUTED.search(detail)
    provider = ""
    model = ""
    if routed is not None:
        ref = routed.group(1)
        provider, _, model = ref.partition("/")
        if not model:
            provider, model = "", ref
    ideas = IDEAS.search(detail)
    tokens = TOKENS.search(detail)
    wall = WALL.search(detail)
    return {
        "at": match.group("at"),
        "elapsed_s": int(match.group("elapsed")),
        "status": match.group("status"),
        "ideas": int(ideas.group(1)) if ideas else 0,
        "tokens": int(tokens.group(1)) if tokens else None,
        "wall_time_s": int(wall.group(1)) if wall else None,
        "provider": provider,
        "model": model,
        "local": bool(provider) and is_local(provider),
    }


def read_schedule_log(path: Optional[Path] = None) -> Dict[str, Any]:
    """The whole log, oldest first. Missing file is an empty log, not an error."""
    if path is None:
        from .dream import dreams_dir

        path = dreams_dir() / "schedule.log"
    passes: List[Dict[str, Any]] = []
    lines = 0
    diagnostics = 0
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"passes": passes, "lines": 0, "diagnostics": 0, "source": str(path)}
    for raw in text.splitlines():
        if not raw.strip():
            continue
        lines += 1
        parsed = parse_pass_line(raw)
        if parsed is None:
            diagnostics += 1
            continue
        passes.append(parsed)
    return {"passes": passes, "lines": lines, "diagnostics": diagnostics, "source": str(path)}


def dream_history(limit: int = 10, path: Optional[Path] = None) -> Dict[str, Any]:
    """The last ``limit`` passes, newest first, plus what the log held."""
    found = read_schedule_log(path)
    passes = found["passes"][-max(1, int(limit)) :]
    return {
        "passes": list(reversed(passes)),
        "lines": found["lines"],
        "diagnostics": found["diagnostics"],
        "source": found["source"],
    }


# --------------------------------------------------------------------------- #
# token usage
# --------------------------------------------------------------------------- #
COST_REASON = (
    "UH records tokens, not money: no price table ships with it, and a cost "
    "derived from a hardcoded rate would be invented. Local tokens (ollama) are "
    "billed to nobody."
)


def day_keys(days: int = WINDOW_DAYS, now: Optional[datetime] = None) -> List[str]:
    """The window's dates, oldest first, ending today."""
    today = (now or datetime.now().astimezone()).date()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def _entry_day(entry: Dict[str, Any]) -> Optional[str]:
    """The date part of ``entry["at"]``, or ``None`` when the record predates it."""
    raw = str(entry.get("at") or "").strip()
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return None


def _session_day(session: Dict[str, Any]) -> Optional[str]:
    """The session's own last-activity date, for records written before ``at``."""
    for key in ("updated_at", "created_at"):
        raw = str(session.get(key) or "").strip()
        if len(raw) < 10:
            continue
        try:
            return date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            continue
    return None


def usage_summary(
    sessions: Iterable[Dict[str, Any]],
    days: int = WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Tokens per day and per tier, over the window, from recorded decisions.

    Two honesty rules:

    * a record written before ``at`` existed is placed on its **session's** last
      activity and counted in ``estimated`` - the tokens were really spent, only
      the day is inferred, and the panel marks it;
    * a turn older than the window is reported in ``outside_window`` instead of
      disappearing, and one whose session has no date either stays in
      ``undated``.
    """
    keys = day_keys(days, now)
    window: Dict[str, Dict[str, Any]] = {
        key: {"date": key, "local": 0, "cloud": 0, "turns": 0, "estimated": 0} for key in keys
    }
    totals = {
        "local": 0,
        "cloud": 0,
        "tokens": 0,
        "prompt": 0,
        "completion": 0,
        "turns": 0,
        "estimated": 0,
    }
    outside = {"turns": 0, "tokens": 0}
    undated = {"turns": 0, "tokens": 0}
    unreported = 0
    sessions_read = 0

    for session in sessions:
        sessions_read += 1
        metadata = session.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        for entry in metadata.get("decisions") or []:
            if not isinstance(entry, dict):
                continue
            usage = entry.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            reported = bool(usage.get("reported"))
            total = int(usage.get("total") or 0)
            by_day = _entry_day(entry)
            estimated = by_day is None
            if estimated:
                by_day = _session_day(session)
            local = is_local(entry.get("provider"))
            if not reported:
                unreported += 1
            if by_day is None:
                undated["turns"] += 1
                undated["tokens"] += total
                continue
            if by_day not in window:
                outside["turns"] += 1
                outside["tokens"] += total
                continue
            bucket = window[by_day]
            bucket["turns"] += 1
            if estimated:
                bucket["estimated"] += total
                totals["estimated"] += total
            if local:
                bucket["local"] += total
                totals["local"] += total
            else:
                bucket["cloud"] += total
                totals["cloud"] += total
            totals["tokens"] += total
            totals["turns"] += 1
            totals["prompt"] += int(usage.get("prompt") or 0)
            totals["completion"] += int(usage.get("completion") or 0)

    return {
        "window_days": days,
        "days": [window[key] for key in keys],
        "totals": totals,
        "outside_window": outside,
        "undated": undated,
        "turns_without_usage": unreported,
        "sessions_read": sessions_read,
        "cost": {"value": None, "reason": COST_REASON},
    }


# --------------------------------------------------------------------------- #
# the machine
# --------------------------------------------------------------------------- #
class _FILETIME(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    def value(self) -> int:
        return (int(self.high) << 32) | int(self.low)


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def _windows_memory() -> Optional[Dict[str, float]]:
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    try:
        status = _MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        total = float(status.ullTotalPhys) / (1024**3)
        free = float(status.ullAvailPhys) / (1024**3)
        return {
            "total_gb": round(total, 1),
            "used_gb": round(total - free, 1),
            "percent": round(float(status.dwMemoryLoad), 1),
        }
    except Exception:  # noqa: BLE001 - a probe that cannot answer is None
        return None


def _linux_memory() -> Optional[Dict[str, float]]:
    try:
        fields = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            name, _, rest = line.partition(":")
            fields[name.strip()] = rest.strip()
        total_kb = float(fields["MemTotal"].split()[0])
        free_kb = float(fields["MemAvailable"].split()[0])
    except (OSError, KeyError, ValueError, IndexError):
        return None
    total = total_kb / (1024**2)
    used = (total_kb - free_kb) / (1024**2)
    return {
        "total_gb": round(total, 1),
        "used_gb": round(used, 1),
        "percent": round(100.0 * used / total, 1) if total else 0.0,
    }


def memory_usage() -> Optional[Dict[str, float]]:
    """RAM in use, from psutil if the process already has it, else the OS."""
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            vm = psutil.virtual_memory()
            total = float(vm.total) / (1024**3)
            used = float(vm.total - vm.available) / (1024**3)
            return {"total_gb": round(total, 1), "used_gb": round(used, 1), "percent": float(vm.percent)}
        except Exception:  # noqa: BLE001
            return None
    return _windows_memory() if os.name == "nt" else _linux_memory()


def _cpu_times() -> Optional[tuple[int, int]]:
    """(idle, total) kernel counters, cumulative since boot."""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    try:
        idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
        if not windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None
        return idle.value(), kernel.value() + user.value()
    except Exception:  # noqa: BLE001
        return None


def cpu_percent(sample: float = 0.12) -> Optional[float]:
    """Busy share over a short window.

    Windows has no instantaneous counter, so this reads the kernel counters twice.
    It is called through ``asyncio.to_thread`` by the route: sleeping 120 ms inside
    the event loop would stall every other request for the same 120 ms.
    """
    if os.name == "nt":
        first = _cpu_times()
        if first is None:
            return None
        time.sleep(max(0.02, float(sample)))
        second = _cpu_times()
        if second is None:
            return None
        idle_delta = second[0] - first[0]
        total_delta = second[1] - first[1]
        if total_delta <= 0:
            return None
        return round(100.0 * (1.0 - idle_delta / total_delta), 1)
    try:
        load_average = getattr(os, "getloadavg", None)
        if load_average is None:  # Windows: no load average, by design
            return None
        cores = os.cpu_count() or 1
        return round(100.0 * float(load_average()[0]) / cores, 1)
    except (AttributeError, OSError, TypeError):
        return None


#: nvidia-smi costs 60-120 ms: the panel polls every 10 s, the GPU does not change
#: that fast, and a subprocess per keystroke is what makes a UI feel heavy.
VRAM_TTL = 5.0
_VRAM: Dict[str, Any] = {"at": 0.0, "value": None}


def vram_usage(ttl: float = VRAM_TTL) -> Optional[Dict[str, Any]]:
    now = time.monotonic()
    cached = _VRAM.get("value")
    if isinstance(cached, dict) and now - float(_VRAM.get("at") or 0.0) < ttl:
        return cached
    value = _read_vram()
    _VRAM["at"] = now
    _VRAM["value"] = value
    return value


def _read_vram() -> Optional[Dict[str, Any]]:
    command = [
        "nvidia-smi",
        "--query-gpu=memory.used,memory.total,name",
        "--format=csv,noheader,nounits",
    ]
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command, capture_output=True, text=True, timeout=2.0
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    for line in (done.stdout or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            used = float(parts[0])
            total = float(parts[1])
        except ValueError:
            continue
        return {
            "name": ", ".join(parts[2:]),
            "used_mb": int(used),
            "total_mb": int(total),
            "percent": round(100.0 * used / total, 1) if total else 0.0,
        }
    return None


def system_snapshot() -> Dict[str, Any]:
    """One reading of the machine. Every field may be ``None``; none may raise."""
    cores = os.cpu_count() or 0
    gpu = None
    try:
        from . import hardware

        found = hardware.profile()
        cores = found.cpu_cores or cores
        gpu = found.gpu
    except Exception:  # noqa: BLE001 - hardware detection is best-effort
        pass
    return {
        "cpu_percent": cpu_percent(),
        "cores": cores,
        "ram": memory_usage(),
        "vram": vram_usage(),
        "gpu": gpu,
        "platform": f"{platform.system()} {platform.release()}",
    }


def snapshot(sessions: Iterable[Dict[str, Any]], days: int = WINDOW_DAYS) -> Dict[str, Any]:
    """The whole payload, in one call, for the route and for the tests."""
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "window_days": days,
        "usage": usage_summary(sessions, days=days),
        "system": system_snapshot(),
        "dreams": dream_history(),
    }


__all__ = [
    "COST_REASON",
    "LOCAL_PROVIDERS",
    "WINDOW_DAYS",
    "cpu_percent",
    "day_keys",
    "dream_history",
    "is_local",
    "memory_usage",
    "parse_pass_line",
    "read_schedule_log",
    "snapshot",
    "system_snapshot",
    "usage_summary",
    "vram_usage",
]
