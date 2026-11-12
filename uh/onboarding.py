"""First-run provider selection: zero configuration, no key required (Jalon 4).

Resolution order, first hit wins:

a. Ollama reachable **with** at least one model -> use the first one;
b. Ollama reachable, no model -> propose a small model sized to the detected
   RAM, with its approximate size, and pull it **only after confirmation**;
c. a local OmniRouter gateway that answers ``GET /v1/models`` -> use it;
d. a provider the user already added with ``uh providers add`` -> use it;
e. nothing available -> guided assistant: show where to get a free key, take
   the values, store them in ``$UH_HOME/keys.toml`` (0600).

Nothing is downloaded or sent without an explicit answer, and every probe is
short-timeout: onboarding must never hang a console that has a working setup.

The size shown for a model is approximate and comes from a static table: a
remote model's exact size is not knowable before the pull (Ollama answers 404
on ``/api/show`` for a model that is not local).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from . import keys as keys_mod
from .providers import ProviderError
from .providers.ollama import list_models, pull_model
from .providers.omnirouter import DEFAULT_MODEL as OMNIROUTER_DEFAULT_MODEL
from .providers.omnirouter import list_remote_models

# name, approximate size, minimum RAM in GB.
# Nothing below 1.5b: a 0.5b model calls tools but then answers with an empty
# message, which looks like a broken harness to a first-time user.
#
# At the 4 GB tier, lfm3 comes last so it wins the tie: it is the only one of the
# two measured to call read_file, terminal and memory_write correctly in UH
# (~1.5 s per turn), and it carries its own tool renderer/parser in Ollama.
SMALL_MODELS: tuple[tuple[str, str, int], ...] = (
    ("qwen2.5:1.5b", "~1.0 GB", 4),
    ("lfm3", "~1.1 GB", 4),
    ("qwen2.5:3b", "~2.0 GB", 8),
    ("qwen2.5:7b", "~4.5 GB", 16),
)

FREE_KEY_HINTS: tuple[tuple[str, str], ...] = (
    ("Groq", "https://console.groq.com/keys"),
    ("OpenRouter", "https://openrouter.ai/keys"),
    ("Google AI Studio", "https://aistudio.google.com/apikey"),
)


@dataclass
class Choice:
    kind: str
    model: str
    source: str  # ollama-existing | ollama-pulled | omnirouter | user-provider | guided
    message: str = ""


# --------------------------------------------------------------------------- #
# machine facts
# --------------------------------------------------------------------------- #
def detect_ram_gb() -> Optional[float]:
    """Total physical memory, or ``None`` when the platform will not tell us."""
    try:
        if sys.platform.startswith("win"):
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

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(float(status.ullTotalPhys) / (1024**3), 1)
            return None
        if sys.platform == "darwin":
            output = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5
            )
            return round(int(output.stdout.strip()) / (1024**3), 1)
        meminfo = open("/proc/meminfo", encoding="utf-8").read()
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / (1024**2), 1)
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return None
    return None


def recommended_model(ram_gb: Optional[float]) -> tuple[str, str]:
    """Cheapest model that still fits: ``(name, approximate size)``.

    Conservative on purpose: an unknown RAM budget is treated as 8 GB, and the
    smallest model whose requirement fits is chosen, never the biggest. The
    floor is 1.5b - smaller models call tools but answer with empty content.
    """
    budget = ram_gb if ram_gb else 8.0
    chosen = SMALL_MODELS[0]
    for entry in SMALL_MODELS:
        if entry[2] <= budget:
            chosen = entry
    return chosen[0], chosen[1]


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #
def _ollama_models() -> list[str]:
    try:
        return [str(entry["name"]) for entry in list_models(timeout=1.5)]
    except ProviderError:
        return []


def _omnirouter_ready() -> bool:
    try:
        return bool(list_remote_models(timeout=1.5))
    except ProviderError:
        return False


def _ask(prompt: str, input_fn: Callable[[str], str]) -> str:
    try:
        return input_fn(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""


def _yes(prompt: str, input_fn: Callable[[str], str]) -> bool:
    return _ask(prompt, input_fn).strip().lower() in {"y", "yes", "o", "oui"}


def choose_provider(
    *,
    stream=None,
    input_fn: Callable[[str], str] = input,
    ollama_probe: Callable[[], list[str]] = _ollama_models,
    router_probe: Callable[[], bool] = _omnirouter_ready,
    pull: Callable[[str, Callable[[dict], None]], None] = pull_model,
    keys_reader: Callable[[], dict] = keys_mod.read_providers,
    ram_detector: Callable[[], Optional[float]] = detect_ram_gb,
) -> Optional[Choice]:
    """Pick a provider for the first console run. ``None`` when nothing works."""
    err = stream if stream is not None else sys.stderr

    def say(text: str) -> None:
        print(text, file=err)

    # a. an installed local model
    models = ollama_probe()
    if models:
        return Choice("ollama", models[0], "ollama-existing", f"using local model {models[0]}")

    # b. Ollama up but empty: offer a small model, with its size
    ram = ram_detector()
    name, size = recommended_model(ram)
    if _ollama_is_up():
        ram_text = f"{ram:g} GB RAM detected" if ram else "RAM unknown"
        say(f"Ollama is running but has no model ({ram_text}).")
        if _yes(f"  download {name} ({size}, approximate) now? [y/N] ", input_fn):
            say(f"  pulling {name} ...")
            try:
                pull(name, lambda event: None)
            except ProviderError as exc:
                say(f"  pull failed: {exc}")
            else:
                return Choice("ollama", name, "ollama-pulled", f"pulled {name}")

    # c. a local gateway already serving models
    if router_probe():
        return Choice("omnirouter", OMNIROUTER_DEFAULT_MODEL, "omnirouter", "using local OmniRouter")

    # d. a provider the user already configured
    configured = keys_reader()
    if configured:
        kind = sorted(configured)[0]
        model = configured[kind].get("model") or ""
        return Choice(kind, model, "user-provider", f"using configured provider {kind}")

    # e. guided setup
    return _guided(stream=err, input_fn=input_fn)


def _ollama_is_up() -> bool:
    """Cheap reachability probe that does not depend on the model list being empty."""
    try:
        import httpx

        from .providers.ollama import base_url

        response = httpx.get(f"{base_url()}/api/tags", timeout=1.5)
        return response.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _guided(*, stream, input_fn: Callable[[str], str]) -> Optional[Choice]:
    print("", file=stream)
    print("No local model and no router found. UH can use a free OpenAI-compatible API.", file=stream)
    print("Get a key (free tier):", file=stream)
    for name, url in FREE_KEY_HINTS:
        print(f"  - {name:<18} {url}", file=stream)
    print("", file=stream)
    print("Add it later with: uh providers add <name> --base-url <url> --model <model>", file=stream)

    if not (hasattr(sys.stdin, "isatty") and sys.stdin.isatty()):
        print("stdin is not a terminal: nothing configured, run `uh providers add` first.", file=stream)
        return None

    print("Guided setup (leave a field empty to abort):", file=stream)
    name = _ask("  provider name (e.g. groq): ", input_fn).strip().lower()
    if not name:
        return None
    base_url = _ask("  base URL (e.g. https://api.groq.com/openai/v1): ", input_fn).strip()
    model = _ask("  model id: ", input_fn).strip()
    key = _ask("  API key (input is echoed; use `uh providers add` to avoid it): ", input_fn).strip()
    if not (base_url and model and key):
        print("  incomplete setup, nothing saved.", file=stream)
        return None

    try:
        path = keys_mod.add_provider(name, base_url, model, key)
    except ValueError as exc:
        print(f"  cannot save: {exc}", file=stream)
        return None
    print(f"  saved to {path} (0600 where supported)", file=stream)
    return Choice(name, model, "guided", f"saved provider {name}")
