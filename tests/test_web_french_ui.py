"""L'interface parle français : les restes d'anglais visibles sont mesurés, pas promis.

Le lot E du 23/11 listait des chaînes anglaises lues par l'opérateur (panneau
Fournisseurs, champ de saisie, quelques états vides). Ce fichier tient la ligne :
chaque phrase bannie est absente du HTML et du JS servis, et la phrase française qui
la remplace est présente.

Portée : les textes VISIBLES (contenu, placeholder, titre, libellé d'accessibilité).
Les commentaires de code restent en anglais — c'est la convention du dépôt, et les
confondre ferait un test qui rougit sur une phrase que personne ne lit.
"""

from __future__ import annotations

from pathlib import Path

from _static_source import script_text

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "uh" / "web" / "static"
APP = STATIC / "app.js"
INDEX = STATIC / "index.html"

#: Les phrases anglaises relevées le 23/11, plus celles trouvées en balayant les
#: littéraux servis. Une chaîne qui revient dans le HTML ou le JS fait rougir ici.
BANNED = (
    "Ask UH",
    "Ctrl/Cmd-K for commands",
    "drop a file here",
    "Allow remote (free) providers",
    "I understand and consent",
    "Test connection",
    "Test routing",
    "keys are write-only",
    "key entry needs a loopback bind",
    "no model configured",
    "in chain",
    "Name a folder",
    "Resize the sessions column",
    "Resize the configuration column",
    "Drag to resize",
    "Toggle theme",
    "Terminal toolset is ENABLED",
    "key state unavailable",
    "no reason given",
    "no notes in this wiki",
    "no wiki found",
    "the file exists but has no content",
    "not applied to the turn",
    "no chain: UHB did not plan this turn",
    "the RAG service is not running",
    "search the wiki, MEMORY.md and the sessions",
    "SIYUAN_TOKEN not set",
    "SiYuan rejected the token",
    "no block for this path",
    "open a session first",
    "no match",
    "no hit",
    "not built",
    "key(s)",
    "UH runs remote on this device",
    "MEMORY.md not created yet",
    "RAG not built yet",
    "No local model fits here",
    "UHB offline",
    "UHB was not consulted",
    "attachment refused",
)


def _served() -> tuple[str, str]:
    return script_text(), INDEX.read_text(encoding="utf-8")


def test_no_visible_english_phrase_survives_in_the_served_ui() -> None:
    script, page = _served()
    for phrase in BANNED:
        assert phrase not in script, f"anglais visible dans app.js : {phrase!r}"
        assert phrase not in page, f"anglais visible dans index.html : {phrase!r}"


def test_the_replacements_are_the_french_ones() -> None:
    """Une phrase bannie ne doit pas disparaître : elle doit être remplacée, et lisible."""
    script, page = _served()
    expected = {
        "page": "Demandez à UH…",
        "script": "Autoriser les fournisseurs distants (gratuits)",
    }
    assert expected["page"] in page
    for phrase in (
        "Les clés sont en écriture seule",
        "Lecture seule : la saisie d'une clé exige une écoute en loopback",
        "Je comprends et je consens",
        "Tester la connexion",
        "aucun modèle configuré",
        "dans la chaîne",
        "Tester le routage",
        "ouvrez d'abord une session",
        'keyField(form, "clé"',
        'keyField(form, "compte"',
        'keyField(form, "jeton"',
    ):
        assert phrase in script, f"remplacement absent : {phrase!r}"


def test_the_english_words_that_stay_are_product_names() -> None:
    """« Ollama », « UHB », « RAG », « SiYuan » sont des noms : ils ne se traduisent pas."""
    script, page = _served()
    for name in ("Ollama", "UHB", "RAG", "SiYuan", "MEMORY.md"):
        assert name in script or name in page
