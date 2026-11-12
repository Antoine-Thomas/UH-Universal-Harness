"""Key rotation, cooldowns and circuit breaking for the free router.

Everything here is pure bookkeeping over an injectable clock: no HTTP, no
sleep, so the behaviour is testable without waiting.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Optional

DEFAULT_COOLDOWN_SECONDS = 30.0
BREAKER_THRESHOLD = 3
BREAKER_BASE_SECONDS = 20.0
BREAKER_MAX_SECONDS = 300.0


@dataclass
class KeyState:
    """One API key of one provider."""

    key: str
    cooldown_until: float = 0.0
    failures: int = 0
    last_error: str = ""

    def ready(self, now: float) -> bool:
        return now >= self.cooldown_until


@dataclass
class ProviderState:
    name: str
    keys: list[KeyState] = field(default_factory=list)
    cursor: int = 0
    failures: int = 0
    open_until: float = 0.0
    last_error: str = ""

    def ready(self, now: float) -> bool:
        """Half-open after the cooldown: the next attempt is the probe."""
        return now >= self.open_until


class Pool:
    """Providers in priority order, each with a rotating set of keys."""

    def __init__(
        self,
        keys: Mapping[str, Iterable[str]],
        *,
        order: Optional[Iterable[str]] = None,
        strategy: str = "priority",
        clock: Callable[[], float] = time.monotonic,
        breaker_threshold: int = BREAKER_THRESHOLD,
        breaker_base: float = BREAKER_BASE_SECONDS,
        breaker_max: float = BREAKER_MAX_SECONDS,
        default_cooldown: float = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self.clock = clock
        self.strategy = (strategy or "priority").strip().lower()
        if self.strategy not in {"priority", "round-robin", "round_robin"}:
            raise ValueError("strategy must be 'priority' or 'round-robin'")
        if self.strategy == "round_robin":
            self.strategy = "round-robin"
        self.breaker_threshold = max(1, int(breaker_threshold))
        self.breaker_base = max(1.0, float(breaker_base))
        self.breaker_max = max(self.breaker_base, float(breaker_max))
        self.default_cooldown = max(0.0, float(default_cooldown))

        names = list(order) if order is not None else list(keys)
        self.states: dict[str, ProviderState] = {}
        for name in names:
            raw_keys = [key for key in (keys.get(name) or []) if key]
            if not raw_keys:
                continue
            self.states[name] = ProviderState(name=name, keys=[KeyState(key=k) for k in raw_keys])
        self._turn = 0

    # -- inspection -------------------------------------------------------- #
    @property
    def names(self) -> list[str]:
        return list(self.states)

    def snapshot(self, now: Optional[float] = None) -> list[dict]:
        """Status rows for ``uh router status`` - never includes a key."""
        moment = self.clock() if now is None else now
        rows = []
        for state in self.states.values():
            ready_keys = [key for key in state.keys if key.ready(moment)]
            rows.append(
                {
                    "provider": state.name,
                    "keys": len(state.keys),
                    "keys_ready": len(ready_keys),
                    "available": state.ready(moment) and bool(ready_keys),
                    "cooling_seconds": max(0.0, round(state.open_until - moment, 1)),
                    "failures": state.failures,
                    "last_error": state.last_error,
                }
            )
        return rows

    # -- selection --------------------------------------------------------- #
    def candidates(self, now: Optional[float] = None) -> list[tuple[str, KeyState]]:
        """Every key worth trying right now, in the order they should be tried."""
        moment = self.clock() if now is None else now
        names = list(self.states)
        if self.strategy == "round-robin" and names:
            start = self._turn % len(names)
            names = names[start:] + names[:start]
            self._turn += 1

        picked: list[tuple[str, KeyState]] = []
        for name in names:
            state = self.states[name]
            if not state.ready(moment):
                continue
            ready = [key for key in state.keys if key.ready(moment)]
            if not ready:
                continue
            if self.strategy == "round-robin":
                offset = state.cursor % len(ready)
                ready = ready[offset:] + ready[:offset]
                state.cursor += 1
            picked.extend((name, key) for key in ready)
        return picked

    # -- feedback ---------------------------------------------------------- #
    def record_success(self, provider: str, key: KeyState) -> None:
        key.cooldown_until = 0.0
        key.failures = 0
        key.last_error = ""
        state = self.states.get(provider)
        if state is not None:
            state.failures = 0
            state.open_until = 0.0
            state.last_error = ""

    def record_failure(
        self,
        provider: str,
        key: KeyState,
        *,
        error: str,
        retry_after: Optional[float] = None,
        now: Optional[float] = None,
    ) -> None:
        moment = self.clock() if now is None else now
        cooldown = self.default_cooldown if retry_after is None else max(0.0, retry_after)
        key.cooldown_until = moment + cooldown
        key.failures += 1
        key.last_error = error

        state = self.states.get(provider)
        if state is None:
            return
        state.failures += 1
        state.last_error = error
        if state.failures >= self.breaker_threshold:
            over = state.failures - self.breaker_threshold
            backoff = min(self.breaker_base * (2 ** min(over, 6)), self.breaker_max)
            state.open_until = moment + backoff

    def reset(self) -> None:
        """Forget every cooldown (used by tests and by ``uh router reset``)."""
        for state in self.states.values():
            state.failures = 0
            state.open_until = 0.0
            state.last_error = ""
            for key in state.keys:
                key.failures = 0
                key.cooldown_until = 0.0
                key.last_error = ""
