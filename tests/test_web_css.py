"""The stylesheet's invariants, checked on the file that is served.

A custom property used without a definition renders as nothing: `var(--danger)`
silently fell back to black, and the failed-turn border had no colour at all. A
media query with an unbalanced brace drops every rule after it, which is invisible
until someone looks at the screen. These are cheap to check and expensive to notice.
"""

from __future__ import annotations

import re
from pathlib import Path

STYLE = Path(__file__).resolve().parents[1] / "uh" / "web" / "static" / "style.css"


def css() -> str:
    return STYLE.read_text(encoding="utf-8")


def test_every_custom_property_resolves() -> None:
    """Every ``var(--x)`` is defined, or carries a fallback in every single use.

    ``--sidebar-w``/``--right-w``/``--splitter`` are written at runtime by
    ``layoutPrefs``; they are legitimate only because each use has a fallback, which
    is exactly what this test encodes.
    """
    text = css()
    defined = set(re.findall(r"^\s*(--[A-Za-z0-9_-]+)\s*:", text, re.M))
    missing = set()
    for match in re.finditer(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(,?)", text):
        name, fallback = match.group(1), match.group(2)
        if name not in defined and fallback != ",":
            missing.add(name)

    assert not missing, f"undefined custom properties without a fallback: {sorted(missing)}"


def test_the_stylesheet_is_balanced() -> None:
    """An unclosed block in a media query swallows every rule that follows it."""
    text = css()
    assert text.count("{") == text.count("}"), "unbalanced blocks in style.css"


def test_hidden_always_wins() -> None:
    """A `display: flex` on the same class used to beat the `hidden` attribute, and a
    closed modal stayed painted under the page."""
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", css())


def test_the_polish_layer_is_present() -> None:
    """The block is the contract of the 22/11 pass; its absence is a silent revert."""
    text = css()
    for needle in ("--ctl-h:", "--ctl-radius:", ".modal-body", ".modal-actions button"):
        assert needle in text, f"missing from style.css: {needle}"
