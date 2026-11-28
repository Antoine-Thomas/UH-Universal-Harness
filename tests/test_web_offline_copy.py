"""Le texte quand UH ne répond plus, et le bandeau de version qui ne doit pas mentir.

Deux règles du terrain du 23/11 :

* l'écran affichait ``Failed to fetch`` sur « Écouter » et « Dicter » : une panne du
  serveur doit se lire comme telle, avec un mot simple et un bouton ;
* le bandeau « UH a été mis à jour » s'allumait sur un checkout de développement, où
  c'est **normal** que l'interface du dépôt et la version du paquet diffèrent.

Les deux décisions sont des fonctions pures, extraites d'``app.js`` entre deux
marqueurs, et exécutées sous node : aucun navigateur, aucun serveur.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from _static_source import script_text

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "uh" / "web" / "static" / "app.js"


def block(begin: str, end: str) -> str:
    text = script_text()
    start = text.index(begin)
    stop = text.index(end, start)
    return text[start:stop]


def run_node(script: str) -> object:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - node est requis par les autres tests web
        pytest.skip("node absent")
    result = subprocess.run(
        [node, "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_server_that_does_not_answer_says_so_in_french() -> None:
    source = block("// BEGIN offlineFailure", "// END offlineFailure")
    script = (
        source
        + """
const out = {
  ui: offlineFailureText("ui"),
  provider: offlineFailureText("provider"),
};
console.log(JSON.stringify(out));
"""
    )
    out = run_node(script)
    assert out["ui"] == "UH ne répond plus : le serveur est arrêté ou redémarré."
    assert "fournisseur" in out["provider"]
    # Le texte brut du navigateur ne doit apparaître dans aucune des deux phrases.
    assert "Failed to fetch" not in out["ui"] + out["provider"]


def test_the_banner_never_lights_up_on_a_development_checkout() -> None:
    source = block("// BEGIN versionWarning", "// END versionWarning")
    script = (
        source
        + """
const out = {
  dev: versionWarning("1.9.2", "dev-sha", "1.9.2", true),
  stale: versionWarning("1.9.2", "1.9.2", "1.9.3", false),
  bundle: versionWarning("1.9.3", "old-bundle", "1.9.3", false),
  aligned: versionWarning("1.9.3", "1.9.3", "1.9.3", false),
};
console.log(JSON.stringify(out));
"""
    )
    out = run_node(script)
    # Sur un checkout de dev, l'interface vient du dépôt : aucune alerte, même si les
    # versions diffèrent — c'est exactement le faux positif observé.
    assert out["dev"] == {"hidden": True, "text": ""}
    assert out["stale"]["hidden"] is False and "occupa" not in out["stale"]["text"]
    assert "ancienne instance" in out["stale"]["text"]
    assert "recharge la page" in out["bundle"]["text"]
    assert out["aligned"] == {"hidden": True, "text": ""}
