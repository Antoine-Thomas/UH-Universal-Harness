"""`providerStatus` — trois états, trois phrases, pas deux.

Avant ce correctif le panneau Fournisseurs n'avait que deux libellés : tout ce qui
n'était pas `verified` s'appelait « personnalisé », y compris une entrée du
catalogue encore marquée `partial` ou `unverified`. Une entrée maison n'est pas une
entrée non vérifiée : elle n'est pas au catalogue du tout. La distinction se teste
sans navigateur, la fonction ne lit qu'une ligne renvoyée par `/api/providers`.
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


def badge_block() -> str:
    source = script_text()
    start = source.index("// BEGIN providerBadge")
    end = source.index("// END providerBadge") + len("// END providerBadge")
    block = source[start:end]
    assert "function providerStatus" in block, "le bloc délimité ne contient pas providerStatus"
    return block


def statuses(rows: list[dict]) -> list[dict]:
    harness = (
        badge_block()
        + "\nconst rows = JSON.parse(process.argv[1]);\n"
        + "console.log(JSON.stringify(rows.map((row) => providerStatus(row))));\n"
    )
    assert NODE is not None
    result = subprocess.run(
        [NODE, "-e", harness, json.dumps(rows)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_a_verified_catalogue_entry_says_verified() -> None:
    assert statuses([{"name": "mistral", "custom": False, "verified": "verified"}]) == [
        {"status": "verified", "label": "vérifié"}
    ]


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_an_unverified_catalogue_entry_says_so() -> None:
    rows = [
        {"name": "openrouter", "custom": False, "verified": "unverified"},
        {"name": "deepseek", "custom": False, "verified": "partial"},
    ]
    assert [row["label"] for row in statuses(rows)] == ["non vérifié", "non vérifié"]


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_a_home_made_entry_says_personalised() -> None:
    assert statuses([{"name": "ma passerelle", "custom": True, "verified": "custom"}]) == [
        {"status": "custom", "label": "personnalisé"}
    ]


@pytest.mark.skipif(NODE is None, reason="node n'est pas installé")
def test_the_three_states_never_share_a_label() -> None:
    rows = [
        {"custom": False, "verified": "verified"},
        {"custom": False, "verified": "unverified"},
        {"custom": True, "verified": "custom"},
    ]
    labels = [row["label"] for row in statuses(rows)]
    assert labels == ["vérifié", "non vérifié", "personnalisé"]
    assert len(set(labels)) == 3
