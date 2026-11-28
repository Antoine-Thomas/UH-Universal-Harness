"""Le front est un ensemble de modules : les tests lisent l'ensemble, pas un fichier.

P2-11 sort `app.js` par domaines. Un test qui inspectait `app.js` par son texte doit
donc lire **tous** les fichiers statiques chargés par la page, dans l'ordre de
chargement, sinon il passe au vert en ne regardant plus que la moitié du code.

Ordre : `app.js` d'abord (le point d'entrée), puis les modules qu'il importe. Un
test qui cherche un marqueur le trouve où qu'il vive ; un test qui cherche un ordre
**entre deux marqueurs** doit préciser `only="app.js"` ou `only="machine.js"`.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "uh" / "web" / "static"

#: Les modules du front, dans l'ordre où la page les charge.
MODULES: tuple[str, ...] = (
    "app.js",
    "transport.js",
    "audio.js",
    "machine.js",
    "decision.js",
)


def script_files() -> dict[str, str]:
    """{nom: contenu}. Un module absent lève, plutôt que de rendre un test creux."""
    return {name: (STATIC / name).read_text(encoding="utf-8") for name in MODULES}


def script_text(only: str | None = None) -> str:
    """Le texte du front entier, ou d'un seul module."""
    if only is not None:
        return (STATIC / only).read_text(encoding="utf-8")
    files = script_files()
    return "\n".join(files[name] for name in MODULES)


def html_text() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def css_text() -> str:
    return (STATIC / "style.css").read_text(encoding="utf-8")
