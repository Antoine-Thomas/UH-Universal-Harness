"""Free-provider catalogue: the data lives in ``catalog.toml``, never in code.

The catalogue is read-only metadata (endpoints, free model ids, documented
limits, where to get a key, and the date each fact was checked). Keeping it as
data means a limit change is a one-line diff, not a code change, and no
provider name is ever hard-wired into the routing logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .._compat import tomllib

CATALOG_FILE = "catalog.toml"
VERIFICATION_STATES = ("verified", "partial", "unverified")


class CatalogError(RuntimeError):
    """The catalogue is missing or malformed. Never silently ignored."""


@dataclass(frozen=True)
class ModelSpec:
    id: str
    tools: bool = False


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    key_url: str = ""
    docs_url: str = ""
    verified: str = "unverified"
    checked_at: str = ""
    limits: str = ""
    notes: str = ""
    signup_note: str = ""
    auth: str = "bearer"
    models: tuple[ModelSpec, ...] = ()
    discover: str = ""
    discover_suffix: str = ""

    @property
    def tool_models(self) -> tuple[ModelSpec, ...]:
        """Models that advertise tool calling - the only ones the agent may use."""
        return tuple(model for model in self.models if model.tools)

    @property
    def is_usable(self) -> bool:
        """A provider nobody verified is listed for reference, never selected."""
        return self.verified != "unverified" and bool(self.models or self.discover)


def catalog_path() -> Path:
    return Path(__file__).with_name(CATALOG_FILE)


def _spec(name: str, raw: Any, source: Path) -> ProviderSpec:
    if not isinstance(raw, dict):
        raise CatalogError(f"{source}: [providers.{name}] is not a table")

    base_url = str(raw.get("base_url") or "").rstrip("/")
    if not base_url.startswith("https://"):
        raise CatalogError(f"{source}: providers.{name}.base_url must be https://")

    verified = str(raw.get("verified") or "unverified").strip().lower()
    if verified not in VERIFICATION_STATES:
        raise CatalogError(
            f"{source}: providers.{name}.verified must be one of {VERIFICATION_STATES}"
        )

    models: list[ModelSpec] = []
    for entry in raw.get("models") or []:
        if isinstance(entry, dict) and entry.get("id"):
            models.append(ModelSpec(id=str(entry["id"]), tools=bool(entry.get("tools"))))

    discover = str(raw.get("discover") or "")
    if not models and not discover:
        raise CatalogError(
            f"{source}: providers.{name} has neither models nor a discover endpoint"
        )

    return ProviderSpec(
        name=name,
        base_url=base_url,
        key_url=str(raw.get("key_url") or ""),
        docs_url=str(raw.get("docs_url") or ""),
        verified=verified,
        checked_at=str(raw.get("checked_at") or ""),
        limits=str(raw.get("limits") or ""),
        notes=str(raw.get("notes") or ""),
        signup_note=str(raw.get("signup_note") or ""),
        auth=str(raw.get("auth") or "bearer").lower(),
        models=tuple(models),
        discover=discover,
        discover_suffix=str(raw.get("discover_suffix") or ""),
    )


def load_catalog(path: Optional[Path] = None) -> dict[str, ProviderSpec]:
    """Parse the catalogue, in file order (that order is the priority order)."""
    target = path or catalog_path()
    if not target.is_file():
        raise CatalogError(f"provider catalogue not found: {target}")
    try:
        with target.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CatalogError(f"cannot read {target}: {exc}") from exc

    providers = data.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise CatalogError(f"{target}: no [providers.*] table")

    return {str(name): _spec(str(name), raw, target) for name, raw in providers.items()}
