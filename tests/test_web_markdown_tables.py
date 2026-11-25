"""G15 — un tableau Markdown doit être un tableau, pas du texte brut.

Deux exigences, et la seconde compte autant que la première :

* les lignes `| a | b |` suivies d'une séparation `|---|:--:|` forment un
  `<table>` avec son en-tête et son alignement ;
* une cellule n'est **jamais** du HTML : `escapeHtml` d'abord, mise en forme
  inline ensuite, comme partout ailleurs. Un `<script>` dans une cellule reste du
  texte, et l'alignement passe par une classe, jamais par un `style=` (la CSP
  pourra un jour refuser `unsafe-inline`).

Le rendu est exécuté par node, comme les autres tests de `markdown.js`.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
MODULE = "uh/web/static/markdown.js"


def render(source: str) -> str:
    script = (
        f"import {{ renderMarkdown }} from './{MODULE}';\n"
        f"process.stdout.write(renderMarkdown({json.dumps(source)}));\n"
    )
    done = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def test_a_table_becomes_a_table() -> None:
    html = render("| Fichier | Taille |\n|---|---|\n| chat.png | 3,35 Mo |")

    assert "<table>" in html, f"tableau non rendu : {html}"
    assert "<th>Fichier</th>" in html
    assert "<td>chat.png</td>" in html


def test_the_separator_line_does_not_leak_as_text() -> None:
    html = render("| a | b |\n|---|---|\n| 1 | 2 |")

    assert "|---" not in html, f"la ligne de séparation reste visible : {html}"


def test_a_cell_can_not_inject_html() -> None:
    html = render("| x |\n|---|\n| <script>alert(1)</script> |")

    assert "<script>" not in html, f"HTML non échappé dans une cellule : {html}"
    assert "&lt;script&gt;" in html


def test_alignment_uses_a_class_not_an_inline_style() -> None:
    html = render("| g | c | d |\n|:--|:-:|--:|\n| 1 | 2 | 3 |")

    assert "md-align-center" in html, f"alignement absent : {html}"
    assert "style=" not in html, f"style inline interdit : {html}"


def test_a_pipe_inside_a_code_block_stays_code() -> None:
    source = "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```"

    html = render(source)

    assert "<table>" not in html, f"un tableau dans un bloc de code a été rendu : {html}"
    assert "| a | b |" in html
