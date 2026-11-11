"""Provider layer: one blocking ``chat`` call, no streaming at Jalon 1."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List


class ProviderError(RuntimeError):
    """Any provider failure: network, HTTP status, malformed answer, missing model.

    Deliberately distinct from :class:`uh.agent.BudgetExhausted`: callers must be
    able to tell "the model is unreachable" from "the model kept calling tools".
    """


class Provider(ABC):
    """Base class for every provider (local or remote)."""

    name = "base"

    @abstractmethod
    def chat(self, messages: List[dict], tools: List[dict]) -> dict:
        """Return one assistant message dict (``role``/``content``/``tool_calls``)."""
        raise NotImplementedError
