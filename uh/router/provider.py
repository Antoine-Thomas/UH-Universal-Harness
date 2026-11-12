"""The integrated free-provider router: one Provider, many free backends.

Order of trust, unchanged from the rest of UH: Ollama first (the fallback chain
the CLI builds), then this router if the user enabled remote access. A call
walks the candidate list - provider, then key - and only gives up when every
candidate has been tried or cooled down.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

import httpx

from ..providers import Provider, ProviderError
from .catalog import CatalogError, ModelSpec, ProviderSpec, load_catalog
from .client import RouterError, chat_completion, discover_models
from .policy import RemoteRefused, ensure_consent
from .pool import Pool


class RouterProvider(Provider):
    """A ``Provider`` that picks whichever free backend answers first."""

    name = "router"
    model = ""
    streaming = True

    def __init__(
        self,
        model: str = "",
        *,
        catalog: Optional[dict[str, ProviderSpec]] = None,
        keys: Optional[dict[str, list[str]]] = None,
        strategy: str = "priority",
        clock: Callable[[], float] = time.monotonic,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 60.0,
        cfg: Any = None,
        local_only: bool = False,
        stdin: Any = None,
        stream: Any = None,
        input_fn: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.model = model or ""
        self.timeout = timeout
        self.transport = transport
        self.local_only = local_only
        self._stdin = stdin
        self._stream = stream
        self._input_fn = input_fn
        self.last_trace: Optional[dict] = None
        self._discovered: dict[str, list[ModelSpec]] = {}
        self._cfg = cfg

        try:
            self.catalog = catalog if catalog is not None else load_catalog()
        except CatalogError as exc:  # a broken catalogue is not silently empty
            raise ProviderError(str(exc)) from exc

        key_map: dict[str, list[str]] = {}
        if keys is not None:
            key_map = {name: list(values) for name, values in keys.items()}
        else:
            from ..keys import read_providers

            for name, entry in read_providers().items():
                values = entry.get("keys") or ([entry["key"]] if entry.get("key") else [])
                if values:
                    key_map[name] = list(values)

        self.keys = key_map
        # Only entries verified on the provider's own documentation enter the
        # automatic chain. A "partial" entry needs an explicit opt-in
        # (router_extra_providers) so an unverified endpoint is never trusted
        # silently.
        explicit = {
            item.strip().lower()
            for item in str(self._cfg_value().get("router_extra_providers") or "").split(",")
            if item.strip()
        }
        order = [
            name
            for name, spec in self.catalog.items()
            if spec.verified == "verified" and name in key_map
        ]
        order += [
            name
            for name, spec in self.catalog.items()
            if spec.verified != "verified" and name in key_map and name in explicit
        ]
        self.pool = Pool(key_map, order=order, strategy=strategy, clock=clock)
        self._strategy = self.pool.strategy

    # -- policy ------------------------------------------------------------ #
    def _cfg_value(self) -> Any:
        if self._cfg is not None:
            return self._cfg
        from .. import config as uh_config

        self._cfg = uh_config.load()
        return self._cfg

    def _check_remote(self) -> None:
        try:
            ensure_consent(
                self._cfg_value(),
                local_only=self.local_only,
                stdin=self._stdin,
                stream=self._stream,
                input_fn=self._input_fn or input,
            )
        except RemoteRefused as exc:
            raise ProviderError(f"router: {exc}") from exc

    # -- model resolution -------------------------------------------------- #
    def _models_for(self, spec: ProviderSpec, key: str) -> list[ModelSpec]:
        if spec.models:
            return list(spec.models)
        if spec.name in self._discovered:
            return self._discovered[spec.name]
        found = discover_models(spec, key, transport=self.transport, timeout=min(15.0, self.timeout))
        self._discovered[spec.name] = found
        return found

    # -- Provider API ------------------------------------------------------ #
    def chat(self, messages: list[dict], tools: list[dict], *, on_token=None) -> dict:
        if not self.pool.states:
            raise ProviderError(
                "no free provider configured yet: run `uh providers free` to add a key"
            )
        self._check_remote()

        candidates = self.pool.candidates()
        if not candidates:
            cooling = ", ".join(row["provider"] for row in self.pool.snapshot())
            raise ProviderError(f"router: every provider is cooling down ({cooling})")

        failures: list[str] = []
        for name, key_state in candidates:
            spec = self.catalog.get(name)
            if spec is None:
                failures.append(f"{name}: not in the catalogue")
                continue
            available = self._models_for(spec, key_state.key)
            usable = [entry for entry in available if entry.tools] if tools else list(available)
            if not usable:
                reason = "no tool-capable model" if tools else "no model advertised"
                failures.append(f"{name}: {reason}")
                continue
            chosen = next((entry.id for entry in usable if entry.id == self.model), usable[0].id)
            model = chosen
            try:
                message = chat_completion(
                    spec,
                    key_state.key,
                    model,
                    messages,
                    tools,
                    on_token=on_token,
                    timeout=self.timeout,
                    transport=self.transport,
                )
            except RouterError as exc:
                self.pool.record_failure(
                    name, key_state, error=f"{exc.kind}: {exc}", retry_after=exc.retry_after
                )
                self.last_trace = {
                    "provider": name,
                    "model": model,
                    "strategy": self._strategy,
                    "ok": False,
                    "error": exc.kind,
                }
                if exc.emitted:
                    raise ProviderError(
                        f"router: {name} broke mid-stream after already answering: {exc}"
                    ) from exc
                failures.append(f"{name}/{model}: {exc.kind}")
                continue

            self.pool.record_success(name, key_state)
            self.last_trace = {
                "provider": name,
                "model": model,
                "strategy": self._strategy,
                "ok": True,
            }
            return message

        raise ProviderError(
            "router: no free provider answered ("
            + "; ".join(failures[:4])
            + (f"; +{len(failures) - 4} more" if len(failures) > 4 else "")
            + ")"
        )

    # -- status ------------------------------------------------------------ #
    def status_rows(self) -> list[dict]:
        rows = []
        snapshot = {row["provider"]: row for row in self.pool.snapshot()}
        for name, spec in self.catalog.items():
            state = snapshot.get(name)
            rows.append(
                {
                    "provider": name,
                    "verified": spec.verified,
                    "label": "verified" if spec.verified == "verified" else "UNVERIFIED (opt-in)",
                    "selected": name in self.pool.states,
                    "checked_at": spec.checked_at,
                    "keys": (state or {}).get("keys", 0),
                    "models": [model.id for model in spec.models][:3]
                    or (["(discovered at runtime)"] if spec.discover else []),
                    "available": (state or {}).get("available", False),
                    "cooling_seconds": (state or {}).get("cooling_seconds", 0.0),
                    "last_error": (state or {}).get("last_error", ""),
                    "limits": spec.limits,
                    "key_url": spec.key_url,
                }
            )
        return rows
