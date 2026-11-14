"""The application factory `uh web` uses when the reloader is on (development).

``uvicorn --reload`` restarts the worker on every change, so the app cannot be
the object the launcher built: it has to be importable by path, and its settings
have to survive the restart. They travel in the environment for exactly that
reason. Production never takes this path - `UH_DEV_RELOAD=1` is opt-in, and the
default `uh web` keeps the single, explicit `create_app(...)` call.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI


def settings() -> dict:
    workspace = os.environ.get("UH_WEB_WORKSPACE") or ""
    return {
        "host": os.environ.get("UH_WEB_HOST") or "127.0.0.1",
        "port": int(os.environ.get("UH_WEB_PORT") or "8765"),
        "token": os.environ.get("UH_WEB_TOKEN") or None,
        "allow_terminal": os.environ.get("UH_WEB_ALLOW_TERMINAL") == "1",
        "workspace": Path(workspace) if workspace else None,
    }


def app() -> FastAPI:
    """Build the cockpit from the environment: the reloader's entry point."""
    from .app import create_app

    return create_app(**settings())


__all__ = ["app", "settings"]
