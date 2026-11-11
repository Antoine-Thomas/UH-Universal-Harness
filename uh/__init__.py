"""Universal Harness (UH) - a provider-agnostic agent harness.

Jalon 2: streamed agent loop, file/terminal/memory toolsets, real ``uh chat``,
``uh model pull`` and a provider registry (ollama, omnirouter) with a traced
fallback chain. No persistence yet (flat memory file only); storage arrives in
Jalon 3.
"""

__version__ = "0.3.0"
__all__ = ["__version__"]
