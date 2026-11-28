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
from _static_source import script_text

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = ROOT / "uh" / "web" / "static" / "markdown.js"
NODE = shutil.which("node")


def render(cases: list[str], artifacts: list[str] | None = None) -> list[str]:
    script = (
        'import { renderMarkdown } from "'
        + MARKDOWN.as_uri()
        + '";\n'
        + "const cases = "
        + json.dumps(cases)
        + ";\n"
        + "const options = "
        + json.dumps({"artifacts": artifacts or []})
        + ";\n"
        + "for (const text of cases) console.log(renderMarkdown(text, options));\n"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], capture_output=True, text=True
    )
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
def test_a_bare_url_becomes_a_clickable_link() -> None:
    (html,) = render(["voir https://example.com/Connexions"])
    assert html.count("<a ") == 1
    assert (
        '<a href="https://example.com/Connexions" target="_blank" '
        'rel="noopener noreferrer">https://example.com/Connexions</a>' in html
    )
    # Le nom d'onglet dans l'adresse ne devient pas un second lien.
    assert "?panel=" not in html


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize(
    ("text", "href"),
    [
        # Fin de phrase : le point reste dans la phrase.
        ("Voir https://exemple.fr/page.", "https://exemple.fr/page"),
        # Entre parenthèses : la fermeture appartient à la phrase.
        ("Voir (https://exemple.fr/page).", "https://exemple.fr/page"),
        # Parenthèses ouvertes par l'adresse elle-même : elles restent dedans.
        (
            "https://fr.wikipedia.org/wiki/Python_(langage_de_programmation)",
            "https://fr.wikipedia.org/wiki/Python_(langage_de_programmation)",
        ),
        # Esperluette : le href porte l'entité, jamais un & nu.
        ("https://exemple.fr/a?x=1&y=2", "https://exemple.fr/a?x=1&amp;y=2"),
    ],
)
def test_the_link_stops_where_the_address_stops(text: str, href: str) -> None:
    (html,) = render([text])
    assert html.count("<a ") == 1, html
    assert f'href="{href}"' in html, html


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_quote_or_an_angle_bracket_never_enters_the_link() -> None:
    quoted, bracketed = render(['Voir "https://exemple.fr/x".', "Voir <https://exemple.fr/y>"])
    assert 'href="https://exemple.fr/x"' in quoted
    assert "&quot;" not in quoted.split("<a ")[1].split("</a>")[0]
    assert 'href="https://exemple.fr/y"' in bracketed
    assert "&gt;" not in bracketed.split("<a ")[1].split("</a>")[0]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_only_http_and_https_become_links() -> None:
    html = render(["javascript:alert(1) data:text/html,x file:///etc/passwd"])
    assert "<a " not in "\n".join(html)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_bare_url_in_code_is_never_a_link() -> None:
    inline, fenced = render(
        ["Le code `https://exemple.fr` reste du code", "```\nhttps://exemple.fr\n```"]
    )
    assert "<a " not in inline and "https://exemple.fr" in inline
    assert "<a " not in fenced


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_image_that_names_an_artifact_disappears() -> None:
    # Le cas de la capture : la figure du cochon est déjà dessinée juste en
    # dessous, et « ![cochon](cochon.png) » restait écrit au-dessus.
    (html,) = render(["Voici le cochon.\n\n![cochon](cochon.png)"], artifacts=["cochon.png"])
    assert html == "<p>Voici le cochon.</p>"
    assert "![" not in html and "<img" not in html


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_image_that_is_not_an_artifact_becomes_a_text_link() -> None:
    (html,) = render(["![photo](photo.jpg)"])
    assert html == '<p><a href="photo.jpg">photo</a></p>'
    assert "<img" not in html


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_external_image_is_a_link_never_an_img() -> None:
    # La politique de sécurité n'autorise que img-src 'self' data: : une image
    # distante ne peut pas s'afficher, et l'interface ne doit pas l'injecter.
    (html,) = render(["![un chat](https://exemple.fr/chat.png)"])
    assert (
        '<a href="https://exemple.fr/chat.png" target="_blank" rel="noopener noreferrer">un chat</a>'
        in html
    )
    assert "<img" not in html


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_image_url_that_must_not_be_fetched_stays_text() -> None:
    data_url, javascript = render(["![x](data:image/png;base64,AAA)", "![x](javascript:alert(1))"])
    assert "<a " not in data_url and "<img" not in data_url
    assert "data:image/png" in data_url
    assert "<a " not in javascript and "<img" not in javascript
    assert "javascript:alert(1)" in javascript


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_image_in_code_is_never_touched() -> None:
    (html,) = render(["Le code `![a](a.png)` reste du code"], artifacts=["a.png"])
    assert "<code>![a](a.png)</code>" in html


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_existing_link_is_not_nested() -> None:
    (html,) = render(["[Connexions](https://example.com/x)"])
    assert html.count("<a ") == 1


def test_the_source_keeps_the_rule() -> None:
    source = MARKDOWN.read_text(encoding="utf-8")
    assert "const TAB_LINKS" in source
    assert '"/?panel=conn"' in source and '"/?panel=local"' in source
    assert "linkTabs(" in source
    # L'autolien des adresses nues, avec la même sûreté que les liens Markdown.
    assert "function autolink(" in source and "BARE_URL" in source
    assert 'rel="noopener noreferrer"' in source
    assert "javascript:" not in source.split("function autolink(")[1].split("function ")[0]
    # Une image Markdown ne devient jamais <img>, et son nom d'artefact est connu.
    assert "function imageLink(" in source and "MARKDOWN_IMAGE" in source


def test_the_page_hands_the_artifact_names_to_the_renderer() -> None:
    """Le retrait du texte brut ne vaut que si `app.js` transmet les noms."""
    lines = script_text().splitlines()
    calls = [index for index, line in enumerate(lines) if "renderMarkdown(" in line]
    assert len(calls) == 4, calls
    for index in calls:
        window = " ".join(lines[index : index + 2])
        assert "artifacts" in window, lines[index]
