"""HTTP front-end for :mod:`uh.providers_admin` (Jalons 7a and 7b.1).

This module holds no rule of its own: it parses a body, checks the loopback
bind, and translates :class:`~uh.providers_admin.AdminError` into a status code.
Everything that decides *what is allowed* lives in ``providers_admin``, so the
console, the CLI and the web panel cannot drift apart.

Invariant, enforced by tests: **no route ever returns a key**, not even
truncated. Deletion takes an index in the body, never a value in the URL.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable

from fastapi import Body, Depends, FastAPI, HTTPException

from ..providers_admin import (
    AdminError,
    add_key_to,
    create_custom,
    is_loopback_bind,
    provider_rows,
    remove_key_index,
    settings,
    test_connection,
    update_settings,
)

_STATUS = {"invalid": 400, "unknown": 404, "forbidden": 403}

__all__ = ["register_provider_routes", "is_loopback_bind"]


def register_provider_routes(app: FastAPI, dependency: Callable[..., Any]) -> None:
    """Attach the provider routes. ``dependency`` is the token check of the app."""

    def bound_host() -> str:
        return str(getattr(app.state.settings, "host", ""))

    def require_loopback(what: str = "key entry") -> None:
        if not is_loopback_bind(bound_host()):
            raise HTTPException(
                status_code=403,
                detail=f"{what} is only allowed when uh web listens on loopback",
            )

    def translate(exc: AdminError) -> HTTPException:
        return HTTPException(status_code=_STATUS.get(exc.kind, 400), detail=str(exc))

    @app.get("/api/providers", dependencies=[Depends(dependency)])
    def list_providers() -> dict:
        return {
            "providers": provider_rows(),
            "key_entry_allowed": is_loopback_bind(bound_host()),
        }

    @app.post("/api/providers/{name}/keys", dependencies=[Depends(dependency)])
    def add_provider_key(name: str, payload: Annotated[dict, Body()]) -> dict:
        require_loopback()
        try:
            count = add_key_to(name, (payload or {}).get("key"))
        except AdminError as exc:
            raise translate(exc) from exc
        return {"stored": True, "keys": count}

    @app.delete("/api/providers/{name}/keys", dependencies=[Depends(dependency)])
    def delete_provider_key(name: str, payload: Annotated[dict, Body()]) -> dict:
        require_loopback()
        index = (payload or {}).get("index")
        if not isinstance(index, int):
            raise HTTPException(status_code=400, detail="index must be a positive integer")
        try:
            count = remove_key_index(name, index)
        except AdminError as exc:
            raise translate(exc) from exc
        return {"deleted": True, "keys": count}

    @app.post("/api/providers/custom", dependencies=[Depends(dependency)])
    def create_custom_provider(payload: Annotated[dict, Body()]) -> dict:
        require_loopback()
        body = payload or {}
        try:
            created = create_custom(
                str(body.get("name") or ""),
                str(body.get("base_url") or ""),
                str(body.get("model") or ""),
                str(body.get("key") or ""),
            )
        except AdminError as exc:
            raise translate(exc) from exc
        return {"created": True, **created}

    @app.post("/api/providers/{name}/test", dependencies=[Depends(dependency)])
    def test_provider(name: str) -> dict:
        try:
            return test_connection(
                name, transport=getattr(app.state, "probe_transport", None)
            )
        except AdminError as exc:
            raise translate(exc) from exc

    @app.get("/api/router/settings", dependencies=[Depends(dependency)])
    def read_settings() -> dict:
        return settings()

    @app.put("/api/router/settings", dependencies=[Depends(dependency)])
    def write_settings(payload: Annotated[dict, Body()]) -> dict:
        # Enabling remote providers is a privacy decision: same loopback rule as
        # key entry, no exception.
        require_loopback("changing privacy settings")
        body = payload or {}
        try:
            return update_settings(
                allow_remote=bool(body.get("allow_remote")),
                consent=bool(body.get("consent")),
                strategy=str(body.get("strategy") or ""),
            )
        except AdminError as exc:
            raise translate(exc) from exc
