"""What this machine can run: RAM, cores, GPU, and the tier that follows.

The point is not to benchmark anything. It is to answer one question before the
first token is spent: **which model, with which context budget, on this device?**
A phone cannot run a 7B locally; a 32 GB workstation should not be told to use a
0.5B because that was the default.

Detection is best-effort and cheap, in this order:

* RAM - ``psutil`` when installed, else ``GlobalMemoryStatusEx`` on Windows,
  ``sysconf`` on Linux/macOS, ``/proc/meminfo`` as the last resort;
* cores - ``os.cpu_count()``, which needs no dependency and no subprocess;
* GPU - ``nvidia-smi``, then ``rocm-smi``, then ``system_profiler`` on macOS.
  Any of them may be absent: no GPU is a normal answer, not an error.

Tiers and what they mean:

===========  ==========  =================================  ==============
tier         RAM         recommended local model              budget
===========  ==========  =================================  ==============
``remote``   < 2 GB      none - use a remote provider         3000
``small``    < 4 GB      ``qwen2.5:0.5b``                     3000
``medium``   4-8 GB      ``qwen2.5:1.5b``                     6000
``large``    8-16 GB     ``qwen2.5:3b``                       12000
``xl``       > 16 GB     ``qwen2.5:7b``                       24000
===========  ==========  =================================  ==============

A dedicated GPU promotes a machine to ``xl`` whatever its RAM: the model then
lives in VRAM, which is what makes 7B pleasant rather than painful.

The budget from here is a *ceiling*, never a target: the context module still
caps it at 75% of the model's real window, because telling a 4k model to accept
24 000 tokens is how a turn fails.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Optional

TIERS = ("remote", "small", "medium", "large", "xl")

#: tier -> (recommended model, context budget, smallest RAM that lands there)
TIER_MODELS: Dict[str, Optional[str]] = {
    "remote": None,
    "small": "qwen2.5:0.5b",
    "medium": "qwen2.5:1.5b",
    "large": "qwen2.5:3b",
    "xl": "qwen2.5:7b",
}
TIER_BUDGETS: Dict[str, int] = {
    "remote": 3000,
    "small": 3000,
    "medium": 6000,
    "large": 12000,
    "xl": 24000,
}
#: Fallback order when the recommended model is not installed.
TIER_ALTERNATES: Dict[str, tuple] = {
    "large": ("llama3.2:3b",),
    "xl": ("llama3.1:8b", "qwen2.5:3b"),
}


@dataclass(frozen=True)
class HardwareProfile:
    """The answer, small enough to send to a browser as-is."""

    ram_gb: float
    cpu_cores: int
    gpu: Optional[str]
    os_name: str
    arch: str
    tier: str

    @property
    def recommended_model(self) -> Optional[str]:
        return TIER_MODELS.get(self.tier)

    @property
    def recommended_budget(self) -> int:
        return TIER_BUDGETS.get(self.tier, 3000)

    @property
    def local_possible(self) -> bool:
        """False on a device where no local model fits: remote provider only."""
        return self.tier != "remote"

    def as_dict(self) -> dict:
        return {
            "tier": self.tier,
            "ram_gb": self.ram_gb,
            "cpu_cores": self.cpu_cores,
            "gpu": self.gpu,
            "os": self.os_name,
            "arch": self.arch,
            "recommended_model": self.recommended_model,
            "recommended_budget": self.recommended_budget,
            "local_possible": self.local_possible,
        }

    def sentence(self) -> str:
        """One line for the UI: what this machine is, and what it should run."""
        parts = [f"{self.ram_gb:.0f} GB RAM", f"{self.cpu_cores} cores"]
        parts.append(f"GPU {self.gpu}" if self.gpu else "no GPU")
        head = f"This machine: {self.tier} ({', '.join(parts)})"
        if not self.local_possible:
            return f"{head} - too small for a local model: use a remote provider."
        budget = self.recommended_budget // 1000
        return f"{head} - recommended model: {self.recommended_model} (context {budget}k)."


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
def _ram_from_psutil() -> Optional[float]:
    try:
        import psutil  # optional: nice when present, never required
    except Exception:  # noqa: BLE001 - a broken psutil counts as absent
        return None
    try:
        return float(psutil.virtual_memory().total) / (1024**3)
    except Exception:  # noqa: BLE001
        return None


def _ram_from_windows() -> Optional[float]:
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        windll = getattr(ctypes, "windll", None)  # absent hors Windows, par design
        if windll is None:
            return None
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if not windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return float(status.ullTotalPhys) / (1024**3)
    except Exception:  # noqa: BLE001 - never fatal
        return None


def _ram_from_sysconf() -> Optional[float]:
    sysconf = getattr(os, "sysconf", None)  # absent on Windows, by design
    if sysconf is not None:
        try:
            pages = sysconf("SC_PHYS_PAGES")
            size = sysconf("SC_PAGE_SIZE")
            if pages > 0 and size > 0:
                return float(pages * size) / (1024**3)
        except (ValueError, OSError):
            pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return float(line.split()[1]) / (1024**2)  # kB -> GB
    except OSError:
        return None
    return None


def total_ram_gb() -> float:
    """Installed RAM in GB, or 0.0 when nothing could be read."""
    for reader in (_ram_from_psutil, _ram_from_windows, _ram_from_sysconf):
        value = reader()
        if value and value > 0:
            return round(value, 1)
    return 0.0


def _run(command: list[str], timeout: float = 3.0) -> str:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False, errors="replace"
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or "").strip()


def detect_gpu() -> Optional[str]:
    """The first GPU name a standard tool reports. ``None`` is a normal answer."""
    if shutil.which("nvidia-smi"):
        name = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        if name:
            return name.splitlines()[0].strip()[:80]
    if shutil.which("rocm-smi"):
        name = _run(["rocm-smi", "--showproductname"])
        for line in name.splitlines():
            if "Card series" in line or "GPU" in line:
                return line.split(":", 1)[-1].strip()[:80]
    if platform.system() == "Darwin":
        profiler = _run(["system_profiler", "SPDisplaysDataType"], timeout=5.0)
        for line in profiler.splitlines():
            if "Chipset Model" in line:
                return line.split(":", 1)[-1].strip()[:80]
    return None


def classify(ram_gb: float, gpu: Optional[str], cores: int = 0) -> str:
    """The tier for a machine. A dedicated GPU is worth more than RAM for a 7B."""
    if ram_gb <= 0:
        # Unknown RAM: assume something workable rather than cripple the default.
        return "large"
    if gpu:
        return "xl"
    if ram_gb < 2:
        return "remote"
    if ram_gb < 4:
        return "small"
    if ram_gb < 8:
        return "medium"
    if ram_gb <= 16:
        return "large"
    return "xl"


def detect() -> HardwareProfile:
    ram = total_ram_gb()
    cores = max(1, int(os.cpu_count() or 1))
    gpu = detect_gpu()
    system = platform.system() or os.name
    machine = platform.machine() or "unknown"
    return HardwareProfile(
        ram_gb=ram,
        cpu_cores=cores,
        gpu=gpu,
        os_name=system,
        arch=machine,
        tier=classify(ram, gpu, cores),
    )


@lru_cache(maxsize=1)
def profile() -> HardwareProfile:
    """Detected once per process: RAM and GPUs do not change between two turns."""
    return detect()


def budget_for_tier() -> int:
    """The context budget the tier suggests. A ceiling, never a target."""
    return profile().recommended_budget


__all__ = [
    "HardwareProfile",
    "TIERS",
    "TIER_ALTERNATES",
    "TIER_BUDGETS",
    "TIER_MODELS",
    "budget_for_tier",
    "classify",
    "detect",
    "detect_gpu",
    "profile",
    "total_ram_gb",
]
