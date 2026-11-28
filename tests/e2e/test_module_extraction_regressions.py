"""Trois régressions réelles de l'extraction P2-11, prouvées par le code chargé.

Codex les a trouvées sur la PR #91 (`4508eed`) et il avait raison : sortir un bloc
de `app.js` laisse des références derrière lui. Un `showSessionBanner()` resté dans
le module, un `httpErrorText()` qui n'y existe pas, un contexte audio perdu en
chemin — trois défauts que la lecture ne voit pas et qu'un `ReferenceError` en
production révèle.

Les deux premiers tests importent le **vrai module** servi par l'application
(`/static/transport.js`) avec un `fetch` simulé : c'est le code livré qui tourne,
pas une copie. Le troisième vérifie l'adaptateur audio dans la source servie.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402


def test_a_401_calls_the_injected_callback(browser, server) -> None:
    """`signalExpired()` appelle `onAuthLost`, jamais `showSessionBanner` (absent ici)."""
    context, page = open_app(browser, server)
    try:
        page.route(
            "**/probe/401",
            lambda route: route.fulfill(status=401, json={"detail": "jeton périmé"}),
        )
        out = page.evaluate(
            """async () => {
              const { createTransport } = await import('/static/transport.js');
              let lost = 0;
              const t = createTransport({
                onAuthLost: () => { lost += 1; },
                httpErrorText: (status) => `erreur ${status}`,
              });
              const r = await t.apiResult('/probe/401');
              return { lost, ok: r.ok, status: r.status };
            }"""
        )

        assert out["status"] == 401
        assert out["ok"] is False
        assert out["lost"] == 1, "le rappel injecté doit être appelé une fois"
    finally:
        context.close()


def test_a_500_without_json_uses_the_injected_text(browser, server) -> None:
    """Un 500 sans corps JSON passe par `httpErrorText` fourni, pas par un nom global."""
    context, page = open_app(browser, server)
    try:
        page.route(
            "**/probe/500",
            lambda route: route.fulfill(
                status=500, body="", headers={"Content-Type": "text/plain"}
            ),
        )
        out = page.evaluate(
            """async () => {
              const { createTransport } = await import('/static/transport.js');
              const t = createTransport({
                onAuthLost: () => {},
                httpErrorText: (status) => `serveur en erreur ${status}`,
              });
              try {
                await t.api('/probe/500');
                return { message: null };
              } catch (error) {
                return { message: String(error.message || error) };
              }
            }"""
        )

        assert out["message"] == "serveur en erreur 500", out

        # Et le câblage : sans cette ligne dans `app.js`, le module lèverait
        # `ReferenceError: httpErrorText is not defined` en production.
        app_source = page.evaluate("async () => (await fetch('/static/app.js')).text()")
        assert "httpErrorText: (status) => httpErrorText(status)" in app_source
    finally:
        context.close()


def test_the_audio_adapter_forwards_the_whole_context(browser, server) -> None:
    """L'adaptateur `drawAudio` transmet le contexte : le clip garde sa session."""
    context, page = open_app(browser, server)
    try:
        app_source = page.evaluate("async () => (await fetch('/static/app.js')).text()")
        # Trois paramètres, et un contexte par défaut : un clip dessiné depuis un
        # message stocké n'en a pas, et ne doit pas lever. La forme à deux
        # paramètres est explicitement refusée : c'est le défaut que Codex a vu.
        assert "drawAudio: (box, clips, context) => drawAudio(box, clips, context)" in app_source
        assert "drawAudio: (box, clips) => drawAudio(box, clips)" not in app_source
        assert "function drawAudio(container, artifacts, context = {})" in app_source

        audio_source = page.evaluate("async () => (await fetch('/static/audio.js')).text()")
        assert "drawAudio(box, clips, { sessionId: currentSessionId() })" in audio_source
    finally:
        context.close()
