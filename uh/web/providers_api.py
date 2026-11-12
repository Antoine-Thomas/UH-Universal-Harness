"""Provider panel API for ``uh web`` (Jalon 7a).

Invariant, enforced by tests: **no route ever returns a key**, not even
truncated. ``GET /api/providers`` reports how many keys exist, never what they
are; additions and deletions take the key in the request body and the index in
the body too (a key in a URL ends up in the server's access log).

Key entry is refused unless ``uh web`` is bound to loopback: a token in a URL can
leak through browser history, a key written from a network-reachable page is one
step worse.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Any, Callable
from urllib.parse import urlsplit

import httpx
from fastapi import Body, Depends, FastAPI, HTTPException

from .. import config as uh_config
from ..keys import add_key, add_provider, read_providers, remove_key_at
from ..router.catalog import CatalogError, load_catalog
from ..router.client import classify_status
from ..router.policy import CONSENT_WARNING, has_consent

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
PROBE_TIMEOUT = 10.0
LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")


def is_loopback_bind(host: str) -> bool:
    raw = (host or "").strip().lower()
    if raw in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(raw.strip("[]")).is_loopback
    except ValueError:
        return False


def validate_base_url(raw: str) -> str:
    """Return a normalised base URL or raise ``ValueError`` with a safe message.

    The message never echoes the URL's credentials (we refuse them first) and
    never contains a key: it is shown in the browser.
    """
    value = (raw or "").strip()
    if not value:
        raise ValueError("base_url is required")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        raise ValueError("base_url must use http or https")
    if parts.username or parts.password:
        raise ValueError("base_url must not embed credentials")
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValueError("base_url has no host")
    if parts.query or parts.fragment:
        raise ValueError("base_url must not carry a query or a fragment")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    # Link-local first: 169.254.169.254 is the cloud metadata endpoint, and the
    # refusal must say so even when the scheme would already be refused.
    if address is not None and address in LINK_LOCAL:
        raise ValueError("link-local addresses are refused (cloud metadata endpoint)")

    loopback = is_loopback_bind(host)
    if not loopback and parts.scheme != "https":
        raise ValueError("base_url must use https (http is allowed on loopback only)")
    return value.rstrip("/")


def _catalog() -> dict:
    try:
        return load_catalog()
    except CatalogError:
        return {}


def _in_chain(app: FastAPI) -> set[str]:
    """Names the router would actually try, from the real router object."""
    from ..router.provider import RouterProvider

    try:
        router = RouterProvider(cfg=uh_config.load())
    except Exception:  # noqa: BLE001 - the panel must render even when the router cannot
        return set()
    return set(router.pool.states)


def provider_rows(app: FastAPI) -> list[dict[str, Any]]:
    catalog = _catalog()
    configured = read_providers()
    active = _in_chain(app)

    names = list(catalog) + sorted(name for name in configured if name not in catalog)
    rows: list[dict[str, Any]] = []
    for name in names:
        spec = catalog.get(name)
        entry = configured.get(name, {})
        keys = [item for item in (entry.get("keys") or []) if item]
        first_model = spec.models[0].id if spec and spec.models else ""
        rows.append(
            {
                "name": name,
                "base_url": entry.get("base_url") or (spec.base_url if spec else ""),
                "model": entry.get("model") or first_model,
                "keys": len(keys),
                "verified": spec.verified if spec else "custom",
                "custom": spec is None,
                "in_chain": name in active,
            }
        )
    return rows


def register_provider_routes(app: FastAPI, dependency: Callable[..., Any]) -> None:
    """Attach the provider routes. ``dependency`` is the token check of the app."""

    def require_loopback() -> None:
        if not is_loopback_bind(getattr(app.state.settings, "host", "")):
            raise HTTPException(
                status_code=403,
                detail="key entry is only allowed when uh web listens on loopback",
            )

    @app.get("/api/providers", dependencies=[Depends(dependency)])
    def list_providers() -> dict:
        return {
            "providers": provider_rows(app),
            "key_entry_allowed": is_loopback_bind(getattr(app.state.settings, "host", "")),
        }

    @app.post("/api/providers/{name}/keys", dependencies=[Depends(dependency)])
    def add_provider_key(name: str, payload: Annotated[dict, Body()]) -> dict:
        require_loopback()
        clean = (name or "").strip().lower()
        raw = (payload or {}).get("key")
        if not isinstance(raw, str) or not raw.strip():
            raise HTTPException(status_code=400, detail="key is required")
        # Never echo the value back, not even in a validation error.
        if len(raw) > 512:
            raise HTTPException(status_code=400, detail="key is too long (max 512 characters)")
        if any(ord(character) < 0x20 for character in raw):
            raise HTTPException(status_code=400, detail="key contains control characters")

        catalog = _catalog()
        configured = read_providers()
        if clean not in catalog and clean not in configured:
            raise HTTPException(status_code=404, detail=f"unknown provider '{clean}'")

        try:
            if clean in configured:
                add_key(clean, raw.strip())
            else:
                spec = catalog[clean]
                model = spec.models[0].id if spec.models else ""
                add_provider(clean, spec.base_url, model, raw.strip())
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"cannot store the key: {exc}") from exc

        return {"stored": True, "keys": len(read_providers().get(clean, {}).get("keys") or [])}

    @app.post("/api/providers/custom", dependencies=[Depends(dependency)])
    def create_custom_provider(payload: Annotated[dict, Body()]) -> dict:
        require_loopback()
        body = payload or {}
        name = str(body.get("name") or "").strip().lower()
        if not NAME_RE.match(name):
            raise HTTPException(
                status_code=400,
                detail="name must be lowercase letters, digits, '-' or '_' (max 32 characters)",
            )
        if name in _catalog():
            raise HTTPException(status_code=400, detail=f"'{name}' is a catalogue provider")
        try:
            base_url = validate_base_url(str(body.get("base_url") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        model = str(body.get("model") or "").strip()
        key = body.get("key")
        if key is not None and not isinstance(key, str):
            raise HTTPException(status_code=400, detail="key must be a string")
        try:
            add_provider(name, base_url, model, str(key or "").strip())
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"cannot store the provider: {exc}") from exc
        return {"created": True, "name": name, "base_url": base_url, "model": model}

    @app.post("/api/providers/{name}/test", dependencies=[Depends(dependency)])
    def test_provider(name: str) -> dict:
        clean = (name or "").strip().lower()
        catalog = _catalog()
        configured = read_providers()
        spec = catalog.get(clean)
        entry = configured.get(clean)
        if spec is None and entry is None:
            raise HTTPException(status_code=404, detail=f"unknown provider '{clean}'")

        base_url = (entry or {}).get("base_url") or (spec.base_url if spec else "")
        if not base_url:
            return {"ok": False, "status": None, "error_kind": "no_base_url"}
        model = (entry or {}).get("model") or (
            spec.models[0].id if spec and spec.models else ""
        )
        keys = [item for item in ((entry or {}).get("keys") or []) if item]
        headers = {"Content-Type": "application/json"}
        if keys:
            headers["Authorization"] = f"Bearer {keys[0]}"

        transport = getattr(app.state, "probe_transport", None)
        try:
            with httpx.Client(
                timeout=PROBE_TIMEOUT, follow_redirects=False, transport=transport
            ) as client:
                response = client.post(
                    f"{base_url}/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                    },
                    headers=headers,
                )
        except httpx.TimeoutException:
            return {"ok": False, "status": None, "error_kind": "timeout"}
        except httpx.TransportError:
            return {"ok": False, "status": None, "error_kind": "transport"}

        if 300 <= response.status_code < 400:
            # following it would send the key wherever the redirect points
            return {"ok": False, "status": response.status_code, "error_kind": "redirect_refused"}
        if response.status_code >= 400:
            return {
                "ok": False,
                "status": response.status_code,
                "error_kind": classify_status(response.status_code),
            }
        return {"ok": True, "status": response.status_code, "error_kind": None}

    def _settings() -> dict:
        cfg = uh_config.load()
        return {
            "allow_remote": bool(cfg.get("allow_remote")),
            "consent": has_consent(cfg),
            "consent_date": str(cfg.get("remote_consent") or ""),
            "strategy": str(cfg.get("router_strategy") or "priority"),
            "extra_providers": str(cfg.get("router_extra_providers") or ""),
            "warning": CONSENT_WARNING,
        }

    @app.get("/api/router/settings", dependencies=[Depends(dependency)])
    def read_settings() -> dict:
        return _settings()

    @app.put("/api/router/settings", dependencies=[Depends(dependency)])
    def write_settings(payload: Annotated[dict, Body()]) -> dict:
        # Enabling remote providers is a privacy decision: same loopback rule as
        # key entry, no exception.
        require_loopback()
        body = payload or {}
        want_remote = bool(body.get("allow_remote"))
        consent = bool(body.get("consent"))
        strategy = str(body.get("strategy") or "").strip().lower()

        if want_remote and not has_consent(uh_config.load()) and not consent:
            raise HTTPException(
                status_code=400,
                detail="enabling remote providers requires consent: true in the same request",
            )
        if strategy and strategy not in {"priority", "round-robin"}:
            raise HTTPException(status_code=400, detail="strategy must be priority or round-robin")

        uh_config.write_layer("user", "allow_remote", want_remote)
        if consent:
            from ..router.policy import record_consent

            record_consent()
        if strategy:
            uh_config.write_layer("user", "router_strategy", strategy)
        return _settings()

    @app.delete("/api/providers/{name}/keys", dependencies=[Depends(dependency)])
    def delete_provider_key(name: str, payload: Annotated[dict, Body()]) -> dict:
        require_loopback()
        clean = (name or "").strip().lower()
        index = (payload or {}).get("index")
        if not isinstance(index, int) or index < 1:
            raise HTTPException(status_code=400, detail="index must be a positive integer")
        if not remove_key_at(clean, index):
            raise HTTPException(status_code=404, detail=f"no key at index {index} for '{clean}'")
        return {"deleted": True, "keys": len(read_providers().get(clean, {}).get("keys") or [])}
