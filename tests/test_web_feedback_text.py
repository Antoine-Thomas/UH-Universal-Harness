"""`feedbackText` — la seule décision qui compte : y a-t-il de la prose ?

Un message assistant qui n'est qu'une image Markdown, un bloc de code ou une URL ne
peut être ni noté ni lu : il n'y a rien à lire. Ce calcul est pur (aucun DOM), donc il
se teste pour de vrai sous node, comme les autres helpers extraits de `app.js`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "uh" / "web" / "static" / "app.js"
NODE = shutil.which("node")


def feedback_block() -> str:
    source = APP.read_text(encoding="utf-8")
    start = source.index("// BEGIN feedbackText")
    end = source.index("// END feedbackText") + len("// END feedbackText")
    block = source[start:end]
    assert "function feedbackText" in block, "le bloc délimité ne contient pas la fonction"
    return block


def run_cases(cases: list[str]) -> list[dict]:
    harness = (
        feedback_block()
        + "\nconst cases = JSON.parse(process.argv[1]);\n"
        + "console.log(JSON.stringify(cases.map((text) => feedbackText(text))));\n"
    )
    assert NODE is not None
    result = subprocess.run(
        [NODE, "-e", harness, json.dumps(cases)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_an_image_only_answer_has_no_prose() -> None:
    """Le cas du mouton : `![Mouton](data:image/png;base64,…)` n'a pas un mot."""
    [readable] = run_cases(["![Mouton](data:image/png;base64,iVBORw0KGgo=)"])
    assert readable["words"] == 0
    assert readable["chars"] == 0


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_a_code_block_is_not_prose() -> None:
    [readable] = run_cases(["```python\nprint('bonjour')\n```"])
    assert readable["words"] == 0


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_a_url_alone_is_not_prose() -> None:
    [readable] = run_cases(["https://example.invalid/un-lien-tres-long"])
    assert readable["words"] == 0


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_a_real_sentence_is_counted() -> None:
    """Trois mots réels suffisent pour « Écouter » ; vingt caractères pour la barre."""
    sentence = "Voici la courbe demandée, tracée en Agg puis publiée comme image."
    [readable] = run_cases([sentence])
    assert readable["words"] >= 3
    assert readable["chars"] >= 20


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_links_keep_their_label_and_lose_the_url() -> None:
    [readable] = run_cases(["Voir [la documentation](https://example.invalid/x) pour le détail."])
    assert "https://" not in readable["text"]
    assert "la documentation" in readable["text"]
    assert readable["words"] >= 4
