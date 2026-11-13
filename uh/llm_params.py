"""The knobs a turn can turn, and what each provider can actually honour.

One table decides the names, the ranges and the support matrix. The web panel
draws its sliders from the same table the API validates against, so a control can
never promise a parameter the provider would drop on the floor - and when a model
ignores one, the panel says which, with the reason.

Two rules that keep this honest:

* **nothing is sent unless the operator enabled the panel**: an untouched setting
  must not change an answer that used to come from the provider's own defaults;
* **unknown keys are dropped, not forwarded**: a provider that receives a field it
  does not know is a provider that answers ``400 unknown parameter``, and a
  settings panel must not be able to break a turn.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

#: type, bounds and label for each control. ``step`` is what the slider uses.
SPEC: Dict[str, Dict[str, Any]] = {
    "temperature": {
        "kind": "float",
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
        "default": 0.7,
        "label": "Température",
        "hint": "0 = factuel, 1,5+ = imaginatif",
    },
    "top_p": {
        "kind": "float",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "default": 0.95,
        "label": "Top P",
        "hint": "nucléaire : garde les tokens les plus probables",
    },
    "max_tokens": {
        "kind": "int",
        "min": 16,
        "max": 32000,
        "step": 16,
        "default": 2048,
        "label": "Tokens max",
        "hint": "la réponse, pas le contexte",
    },
    "frequency_penalty": {
        "kind": "float",
        "min": -2.0,
        "max": 2.0,
        "step": 0.1,
        "default": 0.0,
        "label": "Pénalité de fréquence",
        "hint": "pénalise un token déjà répété",
    },
    "presence_penalty": {
        "kind": "float",
        "min": -2.0,
        "max": 2.0,
        "step": 0.1,
        "default": 0.0,
        "label": "Pénalité de présence",
        "hint": "pousse vers des sujets pas encore abordés",
    },
    "reasoning_effort": {
        "kind": "choice",
        "choices": ["", "low", "medium", "high"],
        "default": "",
        "label": "Effort de raisonnement",
        "hint": "seulement pour les modèles qui l'acceptent",
    },
    "web_search": {
        "kind": "bool",
        "default": True,
        "label": "Recherche web",
        "hint": "outil web_search du tour (désactivé par défaut côté CLI)",
    },
}

#: Sent to the provider as a chat parameter (``web_search`` is a toolset switch).
PROVIDER_KEYS = tuple(key for key in SPEC if key != "web_search")

#: Ollama's ``/api/chat`` takes these in ``options``; ``max_tokens`` becomes
#: ``num_predict``. The rest is not part of its surface.
OLLAMA_KEYS = ("temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty")

#: Models that document ``reasoning_effort``. Anything else gets a 400 from some
#: gateways and silence from others, so the field is simply not sent.
REASONING_MODELS = re.compile(r"^(o\d|gpt-5|gpt-6)", re.IGNORECASE)


def blank() -> Dict[str, Any]:
    """The panel's initial state: disabled, every control at its default."""
    return {
        "enabled": False,
        "values": {key: spec.get("default") for key, spec in SPEC.items()},
    }


def _clamp(key: str, value: Any) -> Any:
    spec = SPEC[key]
    if spec["kind"] == "bool":
        return bool(value)
    if spec["kind"] == "choice":
        text = str(value or "")
        return text if text in spec["choices"] else spec["default"]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return spec["default"]
    number = max(float(spec["min"]), min(float(spec["max"]), number))
    if spec["kind"] == "int":
        return int(round(number))
    return round(number, 3)


def sanitise(payload: Optional[dict]) -> Dict[str, Any]:
    """A settings blob from the browser into values that cannot break a turn.

    ``enabled: false`` (or a missing payload) returns ``{}``: nothing is sent to
    the provider, which is what "the panel is off" must mean. When it is on, a
    control the client forgot to send takes its default rather than disappearing:
    otherwise two clients would disagree about what "enabled" means.
    """
    data = payload or {}
    if not isinstance(data, dict) or not data.get("enabled"):
        return {}
    values = data.get("values")
    if not isinstance(values, dict):
        values = {}
    clean: Dict[str, Any] = {}
    for key, spec in SPEC.items():
        if key == "web_search":
            continue  # a toolset switch, not a provider parameter
        raw = values.get(key, spec.get("default"))
        value = _clamp(key, raw)
        if key == "reasoning_effort" and value == "":
            continue  # "" means "do not send this field at all"
        clean[key] = value
    return clean


def web_search_enabled(payload: Optional[dict]) -> bool:
    """The toolset switch, independent of the provider parameters."""
    data = payload or {}
    if not isinstance(data, dict) or not data.get("enabled"):
        return True  # absent settings keep today's behaviour: the toolset is there
    values = data.get("values")
    if not isinstance(values, dict) or "web_search" not in values:
        return True
    return bool(values["web_search"])


def describe(provider_kind: str, model: str = "") -> Dict[str, Any]:
    """Which of the enabled keys the provider will use, and why the rest is not.

    This is what the panel prints under the sliders: an honest matrix beats a
    tooltip that promises everything.
    """
    kind = (provider_kind or "").lower()
    applied: List[str] = []
    ignored: List[Dict[str, str]] = []
    if kind == "ollama":
        for key in PROVIDER_KEYS:
            if key in OLLAMA_KEYS:
                applied.append(key)
            elif key == "reasoning_effort":
                ignored.append(
                    {
                        "name": key,
                        "reason": "Ollama n'expose pas reasoning_effort dans /api/chat",
                    }
                )
    else:
        for key in PROVIDER_KEYS:
            if key == "reasoning_effort":
                if REASONING_MODELS.match(model or ""):
                    applied.append(key)
                else:
                    ignored.append(
                        {
                            "name": key,
                            "reason": f"{model or 'ce modèle'} n'accepte pas reasoning_effort",
                        }
                    )
                continue
            applied.append(key)
    return {
        "provider": kind or "?",
        "model": model,
        "applied": applied,
        "ignored": ignored,
        "supports_reasoning": bool(REASONING_MODELS.match(model or "")),
    }


def public() -> Dict[str, Any]:
    """The spec, for the panel to draw itself from - no duplicated constants."""
    return {
        "spec": SPEC,
        "defaults": blank(),
        "provider_keys": list(PROVIDER_KEYS),
    }


__all__ = [
    "OLLAMA_KEYS",
    "PROVIDER_KEYS",
    "REASONING_MODELS",
    "SPEC",
    "blank",
    "describe",
    "public",
    "sanitise",
    "web_search_enabled",
]
