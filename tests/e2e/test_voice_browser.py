"""G12 : « Écouter » lit par la voix du navigateur, phrase par phrase, et s'arrête.

La voix du navigateur est gratuite, instantanée et le texte ne quitte pas la
machine : c'est le défaut. Ce fichier injecte un faux `speechSynthesis` (Chromium
n'en a pas en mode test) et mesure trois choses : la lecture se fait par phrases,
le bouton devient « Arrêter », et un second clic annule vraiment.

Le repli est mesuré à part : si la voix du navigateur échoue, la page le dit et
appelle le serveur — vérifié en interceptant la route, donc sans synthèse réelle.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import open_app

pytestmark = pytest.mark.e2e

ANSWER = "Première phrase de la réponse. Deuxième phrase, un peu plus longue. Et la troisième."

FAKE_SYNTH = """
() => {
  window.__spoken = [];
  window.__cancelled = 0;
  const listeners = [];
  // Trois voix, dont une française **en ligne** (localService false) et une locale :
  // la locale doit gagner, même si l'en ligne est déclarée plus jolie.
  const voices = [
    { name: "Voix anglaise", lang: "en-US", localService: true },
    { name: "Voix française (en ligne)", lang: "fr-FR", localService: false },
    { name: "Voix française locale", lang: "fr-FR", localService: true },
  ];
  let current = [];
  window.SpeechSynthesisUtterance = function (text) {
    this.text = text;
    this.lang = "";
    this.voice = null;
  };
  const fake = {
    getVoices: () => current,
    addEventListener: (type, callback) => {
      if (type === "voiceschanged") listeners.push(callback);
    },
    removeEventListener: () => {},
    speak: (line) => {
      window.__spoken.push({
        text: line.text,
        lang: line.lang,
        voice: line.voice && line.voice.name,
      });
      setTimeout(() => line.onend && line.onend(), 0);
    },
    cancel: () => {
      window.__cancelled += 1;
    },
  };
  Object.defineProperty(window, "speechSynthesis", { configurable: true, get: () => fake });
  // La liste est vide au premier appel, comme dans un vrai navigateur : elle arrive
  // ensuite par `voiceschanged` (MDN, Web Speech API).
  setTimeout(() => {
    current = voices;
    listeners.forEach((callback) => callback());
  }, 50);
}
"""

REMOTE_ONLY_SYNTH = """
() => {
  window.__spoken = [];
  window.__cancelled = 0;
  const listeners = [];
  const voices = [{ name: "Voix française (en ligne)", lang: "fr-FR", localService: false }];
  let current = [];
  window.SpeechSynthesisUtterance = function (text) {
    this.text = text;
    this.lang = "";
    this.voice = null;
  };
  const fake = {
    getVoices: () => current,
    addEventListener: (type, callback) => {
      if (type === "voiceschanged") listeners.push(callback);
    },
    removeEventListener: () => {},
    speak: (line) => {
      window.__spoken.push({ text: line.text, voice: line.voice && line.voice.name });
      setTimeout(() => line.onend && line.onend(), 0);
    },
    cancel: () => {},
  };
  Object.defineProperty(window, "speechSynthesis", { configurable: true, get: () => fake });
  setTimeout(() => {
    current = voices;
    listeners.forEach((callback) => callback());
  }, 50);
}
"""

FAILING_SYNTH = """
() => {
  window.SpeechSynthesisUtterance = function (text) {
    this.text = text;
  };
  const fake = {
    getVoices: () => [{ name: "Voix française locale", lang: "fr-FR", localService: true }],
    addEventListener: () => {},
    removeEventListener: () => {},
    speak: (line) => setTimeout(() => line.onerror && line.onerror({ error: "synthesis-failed" }), 0),
    cancel: () => {},
  };
  Object.defineProperty(window, "speechSynthesis", { configurable: true, get: () => fake });
}
"""


def seed_answer(server) -> str:
    """Un tour dans le store du serveur, comme un vrai échange le laisse."""
    from uh.store import Store

    store = Store(server.home / "uh.db")
    try:
        session_id = store.create_session(title="voix", model="stub:1b", provider="stub")
        store.append_messages(
            session_id,
            [
                {"role": "user", "content": "lis-moi cette réponse"},
                {"role": "assistant", "content": ANSWER},
            ],
        )
        return session_id
    finally:
        store.close()


def test_the_browser_voice_reads_sentence_by_sentence(browser, server) -> None:
    seed_answer(server)
    context, page = open_app(browser, server)
    try:
        page.evaluate(FAKE_SYNTH)
        button = page.locator("#messages .answer-audio button").first
        expect(button).to_be_visible()

        button.click()
        expect(button).to_have_text("Arrêter")
        # La séquence avance à chaque `onend` : attendre le second morceau, pas le clic.
        page.wait_for_function("() => window.__spoken.length >= 2", timeout=10000)
        spoken = page.evaluate("() => window.__spoken")
        assert len(spoken) == 3, f"trois phrases, trois prises de parole : {spoken}"
        assert spoken[0]["text"].startswith("Première phrase"), spoken[0]
        assert spoken[1]["text"].startswith("Deuxième phrase"), spoken[1]
        assert spoken[0]["voice"] == "Voix française locale", (
            "une voix française LOCALE gagne sur une voix en ligne"
        )
        assert spoken[0]["lang"].startswith("fr"), spoken[0]

        # À la fin, le bouton revient à « Écouter » tout seul.
        expect(button).to_have_text("Écouter", timeout=10000)
    finally:
        context.close()


def test_a_second_click_stops_the_reading(browser, server) -> None:
    seed_answer(server)
    context, page = open_app(browser, server)
    try:
        page.evaluate(FAKE_SYNTH)
        # Aucun `onend` : la lecture reste en cours, comme une vraie voix qui parle.
        page.evaluate("() => { window.speechSynthesis.speak = () => {}; }")
        button = page.locator("#messages .answer-audio button").first

        button.click()
        expect(button).to_have_text("Arrêter")
        button.click()

        expect(button).to_have_text("Écouter")
        assert page.evaluate("() => window.__cancelled") == 1
    finally:
        context.close()


def test_a_browser_failure_falls_back_to_the_server_and_says_so(browser, server) -> None:
    seed_answer(server)
    context, page = open_app(browser, server)
    try:
        calls: list[str] = []
        page.on("request", lambda request: calls.append(request.url))
        page.route(
            "**/api/audio/speak",
            lambda route: route.fulfill(
                status=503,
                json={"detail": "aucun moteur de voix disponible : test"},
            ),
        )
        page.evaluate(FAILING_SYNTH)
        button = page.locator("#messages .answer-audio button").first
        button.click()

        expect(page.locator("#messages .answer-audio .tool-artifact-status").first).to_contain_text(
            "voix impossible", timeout=15000
        )
        assert any("/api/audio/speak" in url for url in calls), "le repli serveur est appelé"
        expect(button).to_have_text("Écouter")
    finally:
        context.close()


def test_a_remote_voice_is_used_but_announced(browser, server) -> None:
    seed_answer(server)
    context, page = open_app(browser, server)
    try:
        page.evaluate(REMOTE_ONLY_SYNTH)
        button = page.locator("#messages .answer-audio button").first
        button.click()

        status = page.locator("#messages .answer-audio .tool-artifact-status").first
        expect(status).to_contain_text("voix en ligne", timeout=10000)
        expect(status).to_contain_text("fournisseur du navigateur")
        page.wait_for_function("() => (window.__spoken || []).length >= 1", timeout=10000)
        spoken = page.evaluate("() => window.__spoken")
        assert spoken[0]["voice"] == "Voix française (en ligne)"
    finally:
        context.close()
