"""What a model can actually read, asked to the provider that serves it.

The rule of P1-14 is that capability is **measured**, never deduced from a name: a
model called ``…-vl-…`` is not vision because of its name, and an unknown model is
not capable by default. So this module has three sources, in order, and it says which
one answered:

* ``api`` - the provider's own metadata. Ollama exposes ``capabilities`` through
  ``POST /api/show`` (``vision`` is in the list or it is not); OpenAI-compatible
  gateways expose ``input_modalities`` (OmniRouter, 1592 models on this machine);
  Mistral exposes ``capabilities.vision``.
* ``catalogue`` - the provider catalogue shipped with UH (a data file), for providers
  that publish nothing machine-readable.
* ``unknown`` - neither answered. The interface then says « capacité non vérifiée »
  and treats the model as blind, which is the only safe default: a warning that
  over-claims is worse than one that says it does not know.

No model name is hard-coded here. Every key in the returned dict exists to be shown
or to justify a refusal.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import httpx

#: How long a measured table is trusted. Long enough to spare the provider on every
#: turn, short enough that a new model shows up without a restart.
TTL_S = 300.0
_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}

#: Which input modality each attachment kind needs.
KIND_MODALITY = {
    "image": "image",
    "video": "image",
    "audio": "audio",
    "document": "text",
}


def _remember(key: str, value: Dict[str, Any]) -> Dict[str, Any]:
    _cache[key] = (time.monotonic(), value)
    return value


def _cached(key: str) -> Optional[Dict[str, Any]]:
    entry = _cache.get(key)
    if not entry:
        return None
    stamp, value = entry
    if time.monotonic() - stamp > TTL_S:
        _cache.pop(key, None)
        return None
    return value


def clear_cache() -> None:
    """A test needs a cold table; a user needs "re-measure now" after a config change."""
    _cache.clear()


def _configured(provider: str) -> Dict[str, Any]:
    from . import keys

    return (keys.read_providers() or {}).get(provider) or {}


def _key_of(provider: str) -> str:
    entry = _configured(provider)
    for candidate in entry.get("keys") or []:
        if candidate:
            return str(candidate)
    return ""


def _base_url(provider: str) -> str:
    entry = _configured(provider)
    base = str(entry.get("base_url") or "").rstrip("/")
    if base:
        return base
    from .router.catalog import catalog_path, load_catalog

    try:
        spec = load_catalog(catalog_path()).get(provider)
    except Exception:  # noqa: BLE001 - a broken catalogue must not break the panel
        return ""
    return (spec.base_url.rstrip("/") if spec else "") or ""


# --------------------------------------------------------------------------- #
# the three sources
# --------------------------------------------------------------------------- #
def _from_omnirouter(provider: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """``input_modalities`` is the field that decides: image in the list or not."""
    base = _base_url(provider)
    token = _key_of(provider)
    if not base:
        return None
    cached = _cached(f"models:{provider}")
    if cached is not None:
        return cached.get("models")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = httpx.get(f"{base}/models", headers=headers, timeout=20.0)
        if response.status_code != 200:
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    items = payload.get("data") or payload.get("models") or []
    table: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        modalities = [str(value).lower() for value in (item.get("input_modalities") or ["text"])]
        table[str(item["id"])] = {
            "modalities": sorted(set(modalities) | {"text"}),
            "context": item.get("context_length") or item.get("max_input_tokens"),
            "tools": bool((item.get("capabilities") or {}).get("tool_calling")),
        }
    _remember(f"models:{provider}", {"models": table})
    return table


def _from_ollama(provider: str, model: str) -> Optional[Dict[str, Any]]:
    base = _base_url(provider) or "http://127.0.0.1:11434"
    cached = _cached(f"show:{provider}:{model}")
    if cached is not None:
        return cached
    try:
        response = httpx.post(f"{base}/api/show", json={"model": model}, timeout=20.0)
        if response.status_code != 200:
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    capabilities = [str(value).lower() for value in (payload.get("capabilities") or [])]
    info = payload.get("model_info") or {}
    context = None
    for key, value in info.items():
        if str(key).endswith("context_length") and isinstance(value, int):
            context = value
            break
    modalities = ["text"]
    if "vision" in capabilities:
        modalities.append("image")
    return _remember(
        f"show:{provider}:{model}",
        {
            "modalities": modalities,
            "context": context,
            "tools": "tools" in capabilities or "tool_calling" in capabilities,
        },
    )


def _from_mistral(provider: str, model: str) -> Optional[Dict[str, Any]]:
    base = _base_url(provider) or "https://api.mistral.ai"
    token = _key_of(provider)
    if not token:
        return None
    cached = _cached(f"mistral:{provider}")
    if cached is None:
        try:
            response = httpx.get(
                f"{base}/v1/models", headers={"Authorization": f"Bearer {token}"}, timeout=20.0
            )
            payload = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return None
        table: Dict[str, Dict[str, Any]] = {}
        for item in payload.get("data") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            caps = item.get("capabilities") or {}
            modalities = ["text"]
            if caps.get("vision"):
                modalities.append("image")
            if caps.get("audio"):
                modalities.append("audio")
            table[str(item["id"])] = {
                "modalities": modalities,
                "context": item.get("max_context_length"),
                "tools": bool(caps.get("function_calling")),
            }
        cached = _remember(f"mistral:{provider}", {"models": table})
    table = cached.get("models") or {}
    return table.get(model) or table.get(model.split("-latest")[0] + "-latest")


def _from_catalogue(provider: str, model: str) -> Optional[Dict[str, Any]]:
    from .router.catalog import catalog_path, load_catalog

    try:
        spec = load_catalog(catalog_path()).get(provider)
    except Exception:  # noqa: BLE001
        return None
    if spec is None:
        return None
    for entry in spec.models:
        if entry.id == model:
            modalities = ["text"] + (["image"] if getattr(entry, "vision", False) else [])
            return {"modalities": modalities, "context": None, "tools": entry.tools}
    return None


# --------------------------------------------------------------------------- #
# the answer the interface uses
# --------------------------------------------------------------------------- #
def describe(provider: str, model: str) -> Dict[str, Any]:
    """Modalities, context and where the answer came from - per model."""
    provider = str(provider or "")
    model = str(model or "")
    found: Optional[Dict[str, Any]] = None
    source = "unknown"

    if provider and model:
        table = _from_omnirouter(provider) if provider == "omnirouter" else None
        if table is not None:
            found = table.get(model)
            source = "api" if found else "unknown"
        if found is None and provider == "ollama":
            found = _from_ollama(provider, model)
            source = "api" if found else "unknown"
        if found is None and provider in {"mistral", "mistralai"}:
            found = _from_mistral(provider, model)
            source = "api" if found else "unknown"
        if found is None:
            found = _from_catalogue(provider, model)
            source = "catalogue" if found else "unknown"

    modalities = list((found or {}).get("modalities") or ["text"])
    return {
        "provider": provider,
        "model": model,
        "modalities": modalities,
        "context": (found or {}).get("context"),
        "tools": bool((found or {}).get("tools")),
        "source": source,
        # The honest bit: "unknown" is not "capable".
        "verified": source != "unknown",
        "reason": (
            ""
            if source != "unknown"
            else "capacité non vérifiée : ce modèle n'est pas dans une table mesurée"
        ),
    }


def can_read(provider: str, model: str, kind: str) -> Dict[str, Any]:
    """Whether this model can use an attachment of ``kind``, and what to say if not."""
    modality = KIND_MODALITY.get(kind, "text")
    info = describe(provider, model)
    capable = modality in info["modalities"]
    return {
        **info,
        "kind": kind,
        "needs": modality,
        "capable": capable,
        "loss": (
            ""
            if capable
            else {
                "image": "ce modèle ne verra pas l'image : il recevra son nom de fichier",
                "video": "ce modèle ne verra pas la vidéo : il recevra son nom et sa durée",
                "audio": "ce modèle n'écoutera pas le son : il recevra son nom de fichier",
                "document": "ce modèle lira le fichier comme du texte",
            }.get(kind, "ce modèle ne lira pas cette pièce jointe")
        ),
    }


def local_vision_models() -> List[str]:
    """Models installed on the local Ollama that can see - no key, no upstream 404."""
    base = str(os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
    cached = _cached(f"tags:{base}")
    if cached is not None:
        return list(cached.get("models") or [])
    try:
        response = httpx.get(f"{base}/api/tags", timeout=10.0)
        payload = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return []
    names = [str(item.get("name")) for item in payload.get("models") or [] if item.get("name")]
    _remember(f"tags:{base}", {"models": names})
    return names


def vision_candidates() -> List[Dict[str, Any]]:
    """Configured models that can read an image - the one-click switch's menu.

    Only what this machine can actually reach is offered: the providers the operator
    configured, plus the models installed on the local Ollama (switching to one of
    those has never failed for want of a key or an upstream). Proposing a model the
    operator has no key for is a button that fails, which is worse than no button.
    """
    from . import keys

    rows = keys.read_providers() or {}
    offers: List[Dict[str, Any]] = []
    for provider in rows:
        table = _from_omnirouter(provider) if provider == "omnirouter" else None
        models: List[str] = list(table) if table is not None else []
        if not models:
            entry_model = str((rows.get(provider) or {}).get("model") or "")
            if entry_model:
                models = [entry_model]
        mine: List[Dict[str, Any]] = []
        for model in models[:400]:
            info = describe(provider, model)
            if "image" in info["modalities"] and info["verified"]:
                mine.append(
                    {
                        "provider": provider,
                        "model": model,
                        "modalities": info["modalities"],
                        "context": info["context"],
                        "tools": info["tools"],
                    }
                )
        mine.sort(key=_rank)
        offers.extend(mine)

    # The local install, always last in `read_providers` but first in reliability:
    # it needs no key and cannot answer 404 from an upstream nobody controls.
    for model in local_vision_models():
        info = describe("ollama", model)
        if "image" in info["modalities"] and info["verified"]:
            offers.append(
                {
                    "provider": "ollama",
                    "model": model,
                    "modalities": info["modalities"],
                    "context": info["context"],
                    "tools": info["tools"],
                }
            )
    offers.sort(key=_rank)
    return offers


def _rank(offer: Dict[str, Any]) -> tuple[int, str]:
    """Local first, then a model named for vision, then the gateway's combos."""
    name = str(offer["model"]).lower()
    local = str(offer["provider"]).lower() == "ollama"
    named = "vision" in name or "-vl" in name or "vl-" in name
    if local and named:
        return (0, name)
    if local:
        return (1, name)
    if named:
        return (2, name)
    if name.startswith("auto/"):
        return (3, name)
    return (4, name)


__all__ = [
    "local_vision_models",
    "KIND_MODALITY",
    "TTL_S",
    "can_read",
    "clear_cache",
    "describe",
    "vision_candidates",
]
