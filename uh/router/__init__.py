"""Integrated free-provider router (Jalon 6a).

Public surface: :class:`~uh.router.provider.RouterProvider`, the catalogue
loader, and the privacy helpers. No key ever lives here: keys come from the
user's ``$UH_HOME/keys.toml``.
"""

from __future__ import annotations

from .catalog import CatalogError, ModelSpec, ProviderSpec, catalog_path, load_catalog
from .client import RouterError, chat_completion, discover_models, pick_model
from .policy import RemoteRefused, allow_remote, ensure_consent, has_consent
from .pool import KeyState, Pool, ProviderState
from .provider import RouterProvider

__all__ = [
    "CatalogError",
    "KeyState",
    "ModelSpec",
    "Pool",
    "ProviderSpec",
    "ProviderState",
    "RemoteRefused",
    "RouterError",
    "RouterProvider",
    "allow_remote",
    "catalog_path",
    "chat_completion",
    "discover_models",
    "ensure_consent",
    "has_consent",
    "load_catalog",
    "pick_model",
]
