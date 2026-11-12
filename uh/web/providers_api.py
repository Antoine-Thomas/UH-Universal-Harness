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
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException

from .. import config as uh_config
from ..keys import read_providers
from ..router.catalog import CatalogError, load_catalog

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
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
        return {"providers": provider_rows(app)}
