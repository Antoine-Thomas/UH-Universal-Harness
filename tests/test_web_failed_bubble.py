"""`markFailed` — une bulle en erreur ne propose ni vote ni « Écouter ».

Le drapeau `failed` est posé **avant** tout contrôle, et un bouton déjà présent est
retiré. La fonction ne touche au DOM que par l'objet qu'elle reçoit : elle se teste
sous node avec une fausse bulle, sans navigateur.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from _static_source import script_text

APP = Path(__file__).resolve().parents[1] / "uh" / "web" / "static" / "app.js"
NODE = shutil.which("node")

#: Une bulle factice : elle enregistre ce qu'on lui fait, et ne « contient » que
#: les sélecteurs demandés — c'est assez pour mesurer `markFailed`.
FAKE = """
function fakeBox(present) {
  const added = [];
  const removed = [];
  const attrs = {};
  return {
    added,
    removed,
    attrs,
    classList: { add: (name) => added.push(name) },
    setAttribute: (name, value) => { attrs[name] = value; },
    querySelector: (selector) =>
      present.includes(selector) ? { remove: () => removed.push(selector) } : null,
  };
}
"""


def failed_block() -> str:
    source = script_text()
    start = source.index("// BEGIN failedBubble")
    end = source.index("// END failedBubble") + len("// END failedBubble")
    block = source[start:end]
    assert "function markFailed" in block, "le bloc délimité ne contient pas markFailed"
    return block


def run(expression: str, cases: list[object]) -> list[object]:
    harness = (
        failed_block()
        + FAKE
        + "\nconst cases = JSON.parse(process.argv[1]);\n"
        + f"console.log(JSON.stringify(cases.map((args) => {expression})));\n"
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
def test_the_flag_and_the_role_are_set() -> None:
    [state] = run(
        "(() => { const box = fakeBox(args); markFailed(box); "
        "return { added: box.added, removed: box.removed, attrs: box.attrs }; })()",
        [[]],
    )
    assert state["added"] == ["failed"]
    assert state["attrs"] == {"role": "alert"}
    assert state["removed"] == []


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_a_control_already_drawn_is_removed() -> None:
    [state] = run(
        "(() => { const box = fakeBox(args); markFailed(box); "
        "return { added: box.added, removed: box.removed, attrs: box.attrs }; })()",
        [[".text-feedback", ".answer-audio"]],
    )
    assert state["added"] == ["failed"]
    assert state["removed"] == [".text-feedback", ".answer-audio"]


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_a_missing_bubble_is_not_an_error() -> None:
    assert run("markFailed(args[0])", [[None]]) == [None]
