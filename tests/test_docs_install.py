"""The install guide answers the two questions that cost the most time.

Where a connection lives (so a guide, or a model, cannot send someone to the wrong
tab), and the pip ceilings that keep a shared interpreter quiet.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = (ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
APP_JS = (ROOT / "uh" / "web" / "static" / "app.js").read_text(encoding="utf-8")


def test_the_install_guide_names_the_one_door_per_subject() -> None:
    section = INSTALL.split("## Where to connect things")[1].split("## ")[0]

    assert "Connexions" in section and "(`/?panel=conn`)" in section
    assert "Données" in section and "(`/?panel=data`)" in section
    assert "no** MCP button in `Données`" in section
    # The three things that live in Connexions, named.
    assert "S3" in section and "MCP" in section and "Positron" in section


def test_the_install_guide_carries_the_pip_ceilings() -> None:
    section = INSTALL.split("## Pip conflicts")[1].split("\n## ")[0]
    assert "tomlkit<0.15.0" in section
    assert "importlib-metadata<9.0" in section
    assert "openai<3.0.0" in section
    # The range measured on the machine that raised the conflict, not the old
    # `pydantic<2.12` ceiling: that one contradicted every constraint it named.
    assert "pydantic>=2.12.5,<3" in section
    assert "warning" in section, "a conflict message is not a broken install"


def test_the_guide_names_what_really_constrains_pydantic() -> None:
    """The attribution is the part that cost time.

    A guide blaming `mixpanel` (which declares nothing) or `gradio` (which constrains
    pydantic only through an extra) sends the operator after the wrong package, and the
    conflict survives the fix. Both were measured, so both are pinned here.
    """
    section = INSTALL.split("## Pip conflicts")[1].split("\n## ")[0]

    assert "mcp-types" in section and "google-genai" in section
    assert "mixpanel" in section and "declares no dependency" in section
    assert "`mcp` extra" in section, "gradio constrains pydantic only through its extra"
    assert "google-antigravity" in section, "the conflict pydantic will not fix is named"


def test_the_readme_points_at_the_guide() -> None:
    assert "docs/INSTALL.md#where-to-connect-things" in README


def test_the_ui_agrees_with_the_guide() -> None:
    """A doc that contradicts the buttons is worse than no doc."""
    assert APP_JS.count("mcpConnectModal()") == 1
    assert "Gérez ici toutes vos connexions externes" in APP_JS
