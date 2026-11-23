"""Lot 2, point 2 : le micro ne survit à aucun contexte.

Chromium est lancé avec un faux micro (`--use-fake-device-for-media-stream`) : aucun
appareil réel n'est ouvert, et la piste existe quand même — c'est exactement ce qu'il
faut pour compter les pistes vivantes après un arrêt.

L'instrumentation est côté test (`add_init_script`) : elle enveloppe `getUserMedia` et
`track.stop` pour compter appels et arrêts, et recopie le compteur dans
`sessionStorage` pour qu'il survive à un F5.
"""

from __future__ import annotations

import json
import time

import pytest

pytestmark = pytest.mark.e2e

from .conftest import Server, open_app, pw_api  # noqa: E402

TRANSCRIBE = "**/api/audio/transcribe*"

#: Enveloppe getUserMedia et track.stop ; compte appels, pistes vivantes et arrêts.
INIT = """
(() => {
  const media = navigator.mediaDevices;
  if (!media || media.__uhWrapped) return;
  media.__uhWrapped = true;
  const original = media.getUserMedia.bind(media);
  window.__uhStreams = [];
  window.__uhStops = Number(sessionStorage.getItem("uhMicStops") || 0);
  media.getUserMedia = async (constraints) => {
    const stream = await original(constraints);
    window.__uhStreams.push(stream);
    for (const track of stream.getTracks()) {
      const stop = track.stop.bind(track);
      track.stop = () => {
        window.__uhStops += 1;
        sessionStorage.setItem("uhMicStops", String(window.__uhStops));
        stop();
      };
    }
    return stream;
  };
  window.__uhLiveTracks = () =>
    window.__uhStreams.reduce(
      (count, stream) => count + stream.getTracks().filter((t) => t.readyState === "live").length,
      0
    );
})();
"""


@pytest.fixture()
def fake_mic():
    """Un vrai Chromium, un faux micro : rien d'autre ne change."""
    with pw_api.sync_playwright() as playwright:
        instance = playwright.chromium.launch(
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ]
        )
        try:
            yield instance
        finally:
            instance.close()


def wait_until(page, expression: str, timeout: int = 15000) -> None:
    """Attend une condition sans `wait_for_function` : la CSP interdit `unsafe-eval`,
    et Playwright évalue ses prédicats chaîne avec `eval`."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        if page.evaluate(expression):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"condition jamais vraie : {expression}")


def start(browser, server: Server, *, cap_ms: int | None = None):
    context, page = open_app(browser, server)
    page.add_init_script(INIT)
    if cap_ms is not None:
        page.add_init_script(f"window.UH_MIC_MAX_MS = {cap_ms};")
    page.goto(server.url, wait_until="domcontentloaded")
    page.wait_for_selector("#input", timeout=30000)
    # `#splash` couvre l'écran au démarrage. Sur un runner Windows lent il est encore
    # là quand les contrôles deviennent visibles et il intercepte les clics (flake
    # mesuré en CI : `click`/`fill` en timeout de 30 s). On attend sa disparition
    # avant tout clic sur `#mic`.
    page.wait_for_selector("#splash", state="hidden", timeout=30000)
    # Sans moteur configuré, le bouton est masqué par les capacités : on l'affiche
    # pour éprouver l'enregistrement lui-même, qui ne dépend pas du moteur.
    page.evaluate("document.getElementById('mic').hidden = false")
    page.route(
        TRANSCRIBE,
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"text": "bonjour le monde", "engine": "stub"}),
        ),
    )
    return context, page


def test_the_indicator_is_visible_and_stop_releases_the_microphone(
    fake_mic, server: Server
) -> None:
    context, page = start(fake_mic, server)
    try:
        page.click("#mic")
        page.wait_for_selector("#mic-bar:not([hidden])", timeout=10000)
        assert "Micro actif" in page.inner_text("#mic-bar")
        assert page.eval_on_selector_all(".mic-dot", "els => els.length") == 1
        assert page.evaluate("window.__uhLiveTracks()") == 1
        assert page.eval_on_selector("#mic", "el => el.getAttribute('aria-pressed')") == "true"

        page.click("#mic-stop")
        page.wait_for_selector("#mic-bar", state="hidden", timeout=10000)
        wait_until(page, "window.__uhLiveTracks() === 0")
        # Le texte transcrit arrive dans le compositeur, pas dans une bulle.
        wait_until(page, "document.getElementById('input').value.includes('bonjour le monde')")
        assert page.eval_on_selector("#mic", "el => el.getAttribute('aria-pressed')") == "false"
    finally:
        context.close()


def test_a_reload_while_recording_leaves_no_live_track(fake_mic, server: Server) -> None:
    context, page = start(fake_mic, server)
    try:
        page.click("#mic")
        page.wait_for_selector("#mic-bar:not([hidden])", timeout=10000)
        page.evaluate("sessionStorage.setItem('uhMicStops', '0')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#input", timeout=30000)
        page.wait_for_selector("#splash", state="hidden", timeout=30000)
        page.wait_for_timeout(500)
        stops = page.evaluate("Number(sessionStorage.getItem('uhMicStops') || 0)")
        assert stops >= 1, "pagehide doit fermer les pistes avant que la page meure"
        assert page.evaluate("document.getElementById('mic-bar').hidden") is True
    finally:
        context.close()


def test_the_two_minute_cap_stops_by_itself(fake_mic, server: Server) -> None:
    """Le plafond réel est de 2 min ; le test l'abaisse à 1,5 s pour le mesurer."""
    context, page = start(fake_mic, server, cap_ms=1500)
    try:
        page.click("#mic")
        page.wait_for_selector("#mic-bar:not([hidden])", timeout=10000)
        assert page.evaluate("window.__uhLiveTracks()") == 1
        page.wait_for_selector("#mic-bar", state="hidden", timeout=15000)
        wait_until(page, "window.__uhLiveTracks() === 0")
        wait_until(page, "document.getElementById('input').value.includes('bonjour le monde')")
    finally:
        context.close()


def test_changing_session_stops_without_transcribing(fake_mic, server: Server) -> None:
    context, page = start(fake_mic, server)
    calls: list[str] = []
    page.route(
        TRANSCRIBE,
        lambda route: (
            calls.append(route.request.url),
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"text": "hors session", "engine": "stub"}),
            ),
        )[1],
    )
    try:
        page.click("#mic")
        page.wait_for_selector("#mic-bar:not([hidden])", timeout=10000)
        page.click("#new-session")
        page.wait_for_selector("#mic-bar", state="hidden", timeout=10000)
        page.wait_for_timeout(1200)
        assert page.evaluate("window.__uhLiveTracks()") == 0
        assert calls == [], "une dictée d'une autre session ne doit pas être transcrite"
    finally:
        context.close()


def test_a_quota_error_is_readable(fake_mic, server: Server) -> None:
    context, page = start(fake_mic, server)
    page.route(
        TRANSCRIBE,
        lambda route: route.fulfill(
            status=429,
            content_type="application/json",
            body=json.dumps({"detail": "voxtral: 429 too many requests"}),
        ),
    )
    try:
        page.click("#mic")
        page.wait_for_selector("#mic-bar:not([hidden])", timeout=10000)
        page.click("#mic-stop")
        page.wait_for_selector("#mic-status:not([hidden])", timeout=10000)
        wait_until(page, "document.getElementById('mic-status').textContent.includes('429')")
        text = page.inner_text("#mic-status")
        assert "quota de dictée atteint" in text, text
    finally:
        context.close()
