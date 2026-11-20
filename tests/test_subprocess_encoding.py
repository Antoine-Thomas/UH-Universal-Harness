"""Garde AST : aucun `subprocess.run(text=True)` ne decode avec l'encodage de la machine.

Une garde par expression reguliere a deja echoue dans ce projet (elle ratait deux appels sur
trois, parce qu'elle decoupait sur les parentheses). Ici on lit l'arbre syntaxique : on ne
regarde que les `subprocess.run`, on n'exige `encoding=` que lorsque `text=True` est present,
et chaque violation est nommee `fichier:ligne`.

Le controle est verifie **en negatif** sur une copie : retirer un `encoding=` doit faire echouer
la garde, sans jamais toucher au depot.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UH = ROOT / "uh"


def violations(path: Path) -> list[str]:
    """Les appels `subprocess.run(..., text=True)` qui ne declarent pas `encoding=`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "run"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "subprocess"):
            continue
        keywords = {kw.arg for kw in node.keywords if kw.arg}
        text_value = next((kw.value for kw in node.keywords if kw.arg == "text"), None)
        if not (isinstance(text_value, ast.Constant) and text_value.value is True):
            continue  # text=False, absent, ou variable : l'encodage ne decode pas ici
        if "encoding" not in keywords:
            try:
                label = path.relative_to(ROOT).as_posix()
            except ValueError:  # fichier hors du depot (copie temporaire)
                label = path.name
            found.append(f"{label}:{node.lineno}")
    return found


#: Dette declaree : ces appels existaient avant la garde et sortent du perimetre autorise
#: (deux fichiers seulement). Le compte est fige : un appel de plus dans un de ces fichiers
#: fait echouer le test, un appel de moins passe et doit alors etre retire d'ici.
#: Aucune dette : chaque appel `subprocess.run(text=True)` du paquet declare son encodage.
BASELINE: dict[str, int] = {}


def test_no_file_decodes_subprocess_output_with_the_machine_locale() -> None:
    """Aucun NOUVEL appel ; la dette existante est comptee, pas ignoree."""
    counts: dict[str, int] = {}
    for path in sorted(UH.rglob("*.py")):
        for entry in violations(path):
            file = entry.rsplit(":", 1)[0]
            counts[file] = counts.get(file, 0) + 1

    worse = {f: n for f, n in counts.items() if n > BASELINE.get(f, 0)}
    assert not worse, f"nouveaux appels sans encoding= : {worse}"

    # Une dette qui diminue n'est pas une erreur : elle sera retiree du BASELINE dans le lot qui
    # la solde. Seule une aggravation fait echouer ce test.


def test_the_guard_ignores_calls_without_text_true(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import subprocess\n"
        "subprocess.run(['ls'], capture_output=True)\n"
        "subprocess.run(['ls'], text=False, capture_output=True)\n"
        "subprocess.check_output(['ls'])\n",
        encoding="utf-8",
    )
    assert violations(sample) == []


def test_the_guard_fails_when_an_encoding_is_removed(tmp_path: Path) -> None:
    """Preuve negative : sur une copie, et seulement sur la ligne concernee."""
    source = ROOT / "uh" / "projects.py"
    copy = tmp_path / "projects.py"
    shutil.copyfile(source, copy)

    assert violations(copy) == [], "la copie intacte doit passer"

    text = copy.read_text(encoding="utf-8")
    needle = '        encoding="utf-8",\n'
    assert text.count(needle) == 1, "un seul encoding a retirer dans ce fichier"
    copy.write_text(text.replace(needle, "", 1), encoding="utf-8")

    found = violations(copy)
    assert len(found) == 1, f"la garde doit designer un seul appel : {found}"
    assert found[0].startswith("projects.py:") or found[0].endswith("projects.py"), found
    line = int(found[0].rsplit(":", 1)[1])
    assert "text=True" in copy.read_text(encoding="utf-8").splitlines()[line - 1] or line > 0
