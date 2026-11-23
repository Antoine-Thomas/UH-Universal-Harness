"""Panneau Voix (lot 2, point 7) : le moteur, la raison, et ce qui manque.

Le plan est calculé côté serveur ; la page l'affiche. Ce que ces tests mesurent :
le choix est nommé avec sa raison, « Vérifier mon installation » liste chaque pièce
avec un état et une commande, et rien ne s'installe sans le clic sur le bouton.
"""

from __future__ import annotations

import json
import time

import pytest

pytestmark = pytest.mark.e2e

from .conftest import Server, open_app  # noqa: E402

#: Plan synthétique : la machine de test peut très bien avoir faster-whisper installé
#: (c'est le cas après un venv complet), et le test doit alors passer quand même. On
#: mesure l'interface contre un plan connu, pas contre l'état du poste.
PLAN = {
    "profile": "auto",
    "stt": {
        "engine": None,
        "kind": None,
        "label": "aucun",
        "model": "",
        "compute": "",
        "reason": "ni faster-whisper local ni clé Voxtral",
    },
    "tts": {
        "engine": "xtts",
        "kind": "local",
        "label": "XTTS-v2 (local)",
        "clones": True,
        "reason": "XTTS-v2 provisionné : synthèse et clonage locaux",
    },
    "network_allowed": False,
    "python_executable": "C:\\autre\\venv\\Scripts\\python.exe",
    "machine": {
        "tier": "xl",
        "gpu": "RTX 3070 Ti",
        "vram_gb": 8.0,
        "free_vram_gb": 3.0,
        "ram_gb": 63.9,
    },
    "install": [
        {
            "target": "faster-whisper",
            "label": "Installer la dictée locale (faster-whisper)",
            "command": '"C:\\autre\\venv\\Scripts\\python.exe" -m pip install '
            '"faster-whisper>=1.2"',
            "why": "sans lui, la dictée dépend d'un service cloud et de son quota",
        }
    ],
    "installation": [
        {"name": "Python", "state": "ok", "version": "3.14.9", "command": ""},
        {"name": "UH", "state": "ok", "version": "1.9.2", "command": ""},
        {"name": "UHB", "state": "missing", "version": "absent", "command": "uhb install"},
        {
            "name": "faster-whisper",
            "state": "missing",
            "version": "absent",
            "command": '"C:\\autre\\venv\\Scripts\\python.exe" -m pip install '
            '"faster-whisper>=1.2"',
        },
        {
            "name": "Edge",
            "state": "ok",
            "version": "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
            "command": "",
        },
        {"name": "ffmpeg", "state": "ok", "version": "trouvé", "command": ""},
    ],
}


def wait_until(page, expression: str, timeout: int = 20000) -> None:
    """Attend une condition par `evaluate` : la CSP interdit l'eval des prédicats."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        if page.evaluate(expression):
            return
        page.wait_for_timeout(150)
    raise AssertionError(f"condition jamais vraie : {expression}")


def open_voice_tab(browser, server: Server, plan: dict | None = None):
    context, page = open_app(browser, server)
    if plan is not None:
        page.route(
            "**/api/voice/plan*",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(plan)
            ),
        )
    page.evaluate("document.getElementById('group-models').click()")
    page.wait_for_selector("#tab-voice", timeout=20000)
    page.evaluate("document.getElementById('tab-voice').click()")
    page.wait_for_selector("#voice-view:not([hidden])", timeout=20000)
    page.wait_for_selector("#voice-panel .voice-engine", timeout=30000)
    return context, page


def test_the_panel_names_the_engine_and_why(browser, server: Server) -> None:
    context, page = open_voice_tab(browser, server)
    try:
        text = page.text_content("#voice-panel") or ""
        assert "Dictée" in text and "Voix" in text
        assert page.eval_on_selector_all("#voice-panel .voice-engine", "els => els.length") == 2
        # Chaque moteur porte une raison, même quand il n'y en a aucun.
        reasons = page.eval_on_selector_all(
            "#voice-panel .voice-engine-why", "els => els.map((el) => el.textContent.trim())"
        )
        assert all(reasons), reasons
        assert page.eval_on_selector_all("#voice-profile-select", "els => els.length") == 1
        assert "palier" in text or "sans GPU" in text
    finally:
        context.close()


def test_check_installation_lists_each_piece_with_a_command(browser, server: Server) -> None:
    context, page = open_voice_tab(browser, server, plan=PLAN)
    try:
        page.click("text=Vérifier mon installation")
        page.wait_for_selector("#voice-panel .voice-report-row", timeout=15000)
        rows = page.eval_on_selector_all(
            "#voice-panel .voice-report-row",
            "els => els.map((el) => el.textContent)",
        )
        joined = " | ".join(rows)
        for expected in ("Python", "faster-whisper", "Edge", "ffmpeg"):
            assert expected in joined, f"{expected} absent du rapport : {joined}"
        # La commande d'installation est affichée telle quelle, pas résumée.
        commands = page.eval_on_selector_all(
            "#voice-panel .voice-command", "els => els.map((el) => el.textContent)"
        )
        assert any(
            "pip install" in command and "faster-whisper" in command for command in commands
        ), commands
    finally:
        context.close()


def test_no_installation_starts_without_the_click(browser, server: Server) -> None:
    context, page = open_voice_tab(browser, server, plan=PLAN)
    calls: list[str] = []
    payloads: list[dict] = []

    def capture(route):
        calls.append(route.request.url)
        payloads.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"ok": True, "output": "installation simulée", "command": "pip install …"}
            ),
        )

    page.route("**/api/voice/install", capture)
    try:
        # La commande affichée vise l'interpréteur annoncé par le serveur, pas un
        # `pip` trouvé dans le PATH : sinon le raccourci installe dans un autre Python.
        interpreter = page.text_content("#voice-panel .voice-python") or ""
        assert PLAN["python_executable"] in interpreter, interpreter
        shown = page.inner_text("#voice-install-command")
        assert shown.startswith(f'"{PLAN["python_executable"]}" -m pip install'), shown

        # Rien n'a été cliqué : aucun appel ne doit être parti.
        page.wait_for_timeout(800)
        assert calls == [], f"une installation est partie sans clic : {calls}"

        page.click("#voice-install-run")
        page.wait_for_selector("#voice-panel .voice-install-output:not([hidden])", timeout=15000)
        page.wait_for_timeout(600)
        assert len(calls) == 1, calls
        assert payloads[0] == {"target": "faster-whisper", "confirm": True}
    finally:
        context.close()


def test_local_profile_refuses_the_cloud_engine(browser, server: Server) -> None:
    context, page = open_voice_tab(browser, server)
    try:
        page.select_option("#voice-profile-select", "local")
        wait_until(page, "document.getElementById('voice-profile-select').value === 'local'")
        # Le panneau est rechargé par le changement de profil : attendre le nouveau texte.
        wait_until(
            page,
            "document.getElementById('voice-panel').textContent.toLowerCase().includes('locale')",
        )
        # Les lignes de moteur (Dictée / Voix) ne doivent nommer aucun moteur cloud.
        titles = page.eval_on_selector_all(
            "#voice-panel .voice-engine-title", "els => els.map((el) => el.textContent.trim())"
        )
        assert titles, "aucune ligne de moteur"
        assert not any("Voxtral" in title for title in titles), titles
        text = page.text_content("#voice-panel") or ""
        assert "locale" in text.lower()
    finally:
        context.close()
