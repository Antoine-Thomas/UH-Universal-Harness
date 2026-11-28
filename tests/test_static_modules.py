"""P2-11 — le front est découpé en modules natifs, servis tels quels.

Contrat tenu ici : pas de bundler, pas d'étape de build, des modules ES importés
par `app.js` et servis par le même montage statique, sous la même CSP. Ce fichier
vérifie la charpente ; l'activité réelle est prouvée par les e2e des panneaux
(`test_machine_panel.py`, `test_decision_authority.py`), qui cliquent dessus.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "uh" / "web" / "static"
APP = STATIC / "app.js"

#: Les modules sortis de `app.js` jusqu'ici, avec la fabrique qu'ils exposent.
MODULES = {
    "transport.js": "createTransport",
    "audio.js": "createAudio",
    "machine.js": "createMachinePanel",
    "decision.js": "createDecisionView",
}


def test_each_module_is_imported_by_the_entry_point() -> None:
    script = APP.read_text(encoding="utf-8")

    for name in MODULES:
        assert f'from "./{name}"' in script, f"{name} n'est pas importé par app.js"


def test_each_module_exposes_its_factory() -> None:
    for name, factory in MODULES.items():
        text = (STATIC / name).read_text(encoding="utf-8")
        assert f"export function {factory}(" in text, name


def test_no_module_reaches_into_the_global_state() -> None:
    """Un module reçoit ses dépendances ; il ne lit pas `state` ni ne publie de global.

    C'est la frontière qui rend le découpage utile : un module qui écrit dans
    l'état partagé serait un déplacement de ligne, pas une séparation. Lire une API
    du navigateur (`window.speechSynthesis`, `localStorage`) reste permis : ce n'est
    pas de l'état applicatif, c'est la plateforme.
    """
    for name in MODULES:
        text = (STATIC / name).read_text(encoding="utf-8")
        code = re.sub(r"//[^\n]*", "", text)
        assert "state." not in code, f"{name} lit l'état global"
        assert not re.search(r"\bwindow\.\w+\s*=(?!=)", code), f"{name} publie une global"


def test_the_split_stays_without_a_build_step() -> None:
    """Pas de bundler, pas de code évalué à la volée : la CSP reste stricte."""
    for path in [APP, *(STATIC / name for name in MODULES)]:
        text = path.read_text(encoding="utf-8")
        for forbidden in ("eval(", "new Function(", "document.write("):
            assert forbidden not in text, f"{path.name} utilise {forbidden}"


def test_the_modules_are_packaged_with_the_wheel() -> None:
    build = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"uh.web" = ["static/*"]' in build, "les modules doivent partir dans le wheel"
    for name in MODULES:
        assert (STATIC / name).is_file(), name


@pytest.mark.parametrize("name", sorted(MODULES))
def test_the_static_mount_serves_the_module(name: str) -> None:
    from uh.web.app import create_app

    app = create_app(
        token="static-token",
        port=1,
        host="127.0.0.1",
        workspace=ROOT,
        provider_factory=lambda kind, model: None,
        kind="stub",
        model="stub:1b",
    )
    mounts = [item for item in app.routes if getattr(item, "name", "") == "static"]
    assert mounts, "aucun montage /static : le module ne serait pas servi"
    served = next(path for path in mounts[0].app.all_directories if Path(path) == STATIC)
    assert (Path(served) / name).is_file(), f"{name} absent du répertoire servi"
