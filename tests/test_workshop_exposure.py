"""The workshop is offered when UHB is in the workshop mode - whatever launched UH.

Measured on 19/11 in the real cockpit: clicking **Atelier** switched UHB to `dev`
(`available: true` on `execute_code`) and the chat kept its seven local tools, because
the tool was gated on `UH_UHB_EXECUTE=1` being set **at launch** - and the desktop
shortcut never set it. The mode is the fact that matters; the approval nonce stays the
gate that actually prevents a run.
"""

from __future__ import annotations

import re
from pathlib import Path

from uh.web import app as web_app

APP = Path(__file__).resolve().parents[1] / "uh" / "web" / "app.py"


class _Status:
    def __init__(self, mode: str) -> None:
        self._mode = mode

    def status(self) -> dict:
        return {"mode": self._mode, "connected": True}


def test_a_uhb_already_in_dev_exposes_the_tool(monkeypatch, tmp_path: Path) -> None:
    # Un test precedent (la route de bascule) a pu poser "0" dans le processus :
    # une valeur vide dit « pas de refus explicite », ce que la fonction teste.
    monkeypatch.setenv("UH_UHB_EXECUTE", "")
    monkeypatch.setattr("uh.toolsets.uhb.resolve_url", lambda: "http://127.0.0.1:8101")
    monkeypatch.setattr("uh.toolsets.uhb.Uhbtoolset", lambda url, timeout=1.0: _Status("dev"))

    web_app._WORKSHOP_PROBE[0] = 0.0

    # `force` : on mesure la sonde elle-meme, pas le cache qu'un tour precedent a rempli.
    assert web_app._expose_workshop_when_uhb_is_in_dev(force=True) is True

    import os

    assert os.environ.get("UH_UHB_EXECUTE") == "1"


def test_a_uhb_in_chat_does_not_expose_it(monkeypatch) -> None:
    # Un test precedent (la route de bascule) a pu poser "0" dans le processus :
    # une valeur vide dit « pas de refus explicite », ce que la fonction teste.
    monkeypatch.setenv("UH_UHB_EXECUTE", "")
    monkeypatch.setattr("uh.toolsets.uhb.resolve_url", lambda: "http://127.0.0.1:8101")
    monkeypatch.setattr("uh.toolsets.uhb.Uhbtoolset", lambda url, timeout=1.0: _Status("chat"))

    web_app._expose_workshop_when_uhb_is_in_dev()

    import os

    assert os.environ.get("UH_UHB_EXECUTE") in (None, "")


def test_an_explicit_refusal_is_respected(monkeypatch) -> None:
    """`UH_UHB_EXECUTE=0` is the operator saying no: no probe, no switch."""
    monkeypatch.setenv("UH_UHB_EXECUTE", "0")
    called: list[int] = []
    monkeypatch.setattr("uh.toolsets.uhb.resolve_url", lambda: called.append(1) or "http://x")

    web_app._expose_workshop_when_uhb_is_in_dev()

    assert called == [] and __import__("os").environ["UH_UHB_EXECUTE"] == "0"


def test_the_mode_route_also_exposes_and_withdraws(monkeypatch) -> None:
    """The switch is the gesture: dev exposes, chat withdraws, in the same process."""
    source = APP.read_text(encoding="utf-8")
    assert 'os.environ["UH_UHB_EXECUTE"] = "1" if wanted == "dev" else "0"' in source
    assert "execute_exposed" in source


def test_the_vocabulary_names_the_technical_mode() -> None:
    js = (Path(__file__).resolve().parents[1] / "uh" / "web" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    for label in ('"Discussion (chat)"', '"Atelier (dev)"', '"Relecture (review)"',
                  '"Observation (observe)"'):
        assert label in js, f"{label} doit apparaître dans les libellés de mode"
    assert re.search(r"dev: \"Atelier \(dev\) : exécute du code", js), (
        "l'en-tête doit nommer le mode technique, pas seulement son nom français"
    )
