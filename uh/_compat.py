"""Standard-library compatibility shims (UH supports Python 3.10 to 3.13).

``tomllib`` joined the standard library in 3.11. On 3.10 the ``tomli`` package
is installed as a conditional dependency (see ``pyproject.toml``) and exposes
the same ``load``/``loads``/``TOMLDecodeError`` API, so a single import point
keeps the rest of the code version-agnostic.
"""

from __future__ import annotations

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on the interpreter
    import tomli as tomllib

__all__ = ["tomllib"]
