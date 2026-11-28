"""`micClockText` et `micErrorText` — les deux décisions pures de la dictée.

L'horloge du plafond et le texte d'une erreur se testent sans navigateur : ils ne
touchent ni au DOM ni au micro. Les cas d'erreur sont ceux que le serveur renvoie
réellement (503 + message), pas des codes inventés.
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


def mic_block() -> str:
    source = script_text()
    start = source.index("// BEGIN micText")
    end = source.index("// END micText") + len("// END micText")
    block = source[start:end]
    assert "function micErrorText" in block and "function micClockText" in block
    return block


def run(expression: str, cases: list[list[object]]) -> list[object]:
    harness = (
        mic_block()
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
def test_the_clock_counts_down_in_minutes_and_seconds() -> None:
    assert run("micClockText(args[0])", [[120000], [9000], [0], [-500]]) == [
        "2:00",
        "0:09",
        "0:00",
        "0:00",
    ]


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_a_quota_error_says_quota_and_what_to_do() -> None:
    [text] = run("micErrorText(args[0], args[1])", [[429, ""]])
    assert "429" in text
    assert "quota" in text


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_a_missing_faster_whisper_names_the_install() -> None:
    [text] = run(
        "micErrorText(args[0], args[1])",
        [[503, "faster-whisper absent : aucune transcription locale disponible"]],
    )
    assert "faster-whisper" in text
    assert "pip install -e" in text


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_no_engine_at_all_is_said_plainly() -> None:
    [text] = run("micErrorText(args[0], args[1])", [[503, "aucun moteur de dictée disponible"]])
    assert "aucun moteur" in text


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_an_unknown_detail_is_kept_verbatim() -> None:
    assert run("micErrorText(args[0], args[1])", [[503, "disque plein"]]) == ["disque plein"]


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_an_empty_detail_still_names_the_status() -> None:
    [text] = run("micErrorText(args[0], args[1])", [[500, ""]])
    assert "500" in text
