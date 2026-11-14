"""Universal Harness (UH) - a provider-agnostic agent harness.

Stable surface: the agent loop and its toolsets (file, terminal, memory, web
search), a local model layer with a traced fallback chain (Ollama, Cloudflare,
DeepSeek, OmniRoute, free providers), persisted sessions with layered
configuration, the console with zero-config onboarding, and the local web UI
with its API-key panel.
"""

__version__ = "1.6.0"
__all__ = ["__version__"]
