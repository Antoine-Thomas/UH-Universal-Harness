"""A tab named in an answer becomes the link that opens it.

Run through Node when it is available (the renderer is an ES module and has no
Python twin): the assertion is the rendered HTML, not the source. Without Node the
file-level checks still run, so the rule cannot disappear silently.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = ROOT / "uh" / "web" / "static" / "markdown.js"
NODE = shutil.which("node")


def render(cases: list[str]) -> list[str]:
    script = (
        'import { renderMarkdown } from "'
        + MARKDOWN.as_uri()
        + '";\n'
        + "const cases = " + json.dumps(cases) + ";\n"
        + "for (const text of cases) console.log(renderMarkdown(text));\n"
    )
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_plain_tab_name_becomes_a_link() -> None:
    (html,) = render(["Ouvre l'onglet Connexions, puis + Connecter un MCP"])
    assert '<a href="/?panel=conn">Connexions</a>' in html


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_deep_link_from_the_model_is_rendered_as_a_link() -> None:
    (html,) = render(["Passe par [Ouvrir Connexions](/?panel=conn)."])
    assert '<a href="/?panel=conn">Ouvrir Connexions</a>' in html


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_code_is_never_turned_into_a_link() -> None:
    inline, fenced = render(["Le code `Connexions` reste du code", "```\nConnexions\n```"])
    assert "<a " not in inline and "<code>Connexions</code>" in inline
    assert "<a " not in fenced


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_url_that_contains_a_tab_name_is_left_alone() -> None:
    (html,) = render(["voir https://example.com/Connexions"])
    assert "<a " not in html
    assert "https://example.com/Connexions" in html


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_existing_link_is_not_nested() -> None:
    (html,) = render(["[Connexions](https://example.com/x)"])
    assert html.count("<a ") == 1


def test_the_source_keeps_the_rule() -> None:
    source = MARKDOWN.read_text(encoding="utf-8")
    assert "const TAB_LINKS" in source
    assert '"/?panel=conn"' in source and '"/?panel=local"' in source
    assert "linkTabs(" in source
