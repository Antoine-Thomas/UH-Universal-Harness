"""First-run provider selection: zero configuration, no key required (Jalon 4).

Resolution order, first hit wins:

a. Ollama reachable **with** at least one model -> use the first one;
b. Ollama reachable, no model -> pull the zero-config model
   (``ZERO_CONFIG_MODEL``), announced on stderr, no prompt;
c. a local OmniRouter gateway that answers ``GET /v1/models`` -> use it;
d. a provider the user already added with ``uh providers add`` -> use it;
e. nothing available -> guided assistant: show where to get a free key, take
   the values, store them in ``$UH_HOME/keys.toml`` (0600).

The zero-config pull is automatic but announced; nothing is ever *sent* to a
remote provider without consent, and the guided setup still asks before
writing. Every probe is short-timeout: onboarding must never hang a console
that has a working setup.

The size shown for a model is approximate: a remote model's exact size is not
knowable before the pull (Ollama answers 404 on ``/api/show`` for a model that
is not local).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Optional

from . import keys as keys_mod
from .providers import ProviderError
from .providers.ollama import list_models, pull_model
from .providers.omnirouter import DEFAULT_MODEL as OMNIROUTER_DEFAULT_MODEL
from .providers.omnirouter import list_remote_models

# The one model pulled when nothing is installed: small enough to run on any
# machine (~400 MB) and tool-capable.
#
# History: the first-run suggestion used to be RAM-tiered (SMALL_MODELS, floor
# at 1.5b - a measured qwen2.5:0.5b called tools, then answered with an empty
# message). Operator ruling, 2026-11-12: 0.5b is the zero-config default; the
# console now nudges once when a post-tool answer comes back empty
# (console.run_text), which covers the measured failure mode. Re-measure if
# the nudge ever goes away.
ZERO_CONFIG_MODEL = ("qwen2.5:0.5b", "~400 MB")

FREE_KEY_HINTS: tuple[tuple[str, str], ...] = (
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


def choose_provider(
    *,
    stream=None,
    input_fn: Callable[[str], str] = input,
    ollama_probe: Callable[[], list[str]] = _ollama_models,
    router_probe: Callable[[], bool] = _omnirouter_ready,
    pull: Callable[[str, Callable[[dict], None]], None] = pull_model,
    keys_reader: Callable[[], dict] = keys_mod.read_providers,
) -> Optional[Choice]:
    """Pick a provider for the first console run. ``None`` when nothing works."""
    err = stream if stream is not None else sys.stderr

    def say(text: str) -> None:
        print(text, file=err)

    # a. an installed local model
    models = ollama_probe()
    if models:
        return Choice("ollama", models[0], "ollama-existing", f"using local model {models[0]}")

    # b. Ollama up but empty: pull the zero-config model, announced, automatic
    if _ollama_is_up():
        name, size = ZERO_CONFIG_MODEL
        say("Ollama is running but no model is installed.")
        say(f"  downloading {name} ({size}, approximate) so you can start right away ...")
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
    name = _ask("  provider name (e.g. openrouter): ", input_fn).strip().lower()
    if not name:
        return None
    base_url = _ask("  base URL (e.g. https://openrouter.ai/api/v1): ", input_fn).strip()
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
