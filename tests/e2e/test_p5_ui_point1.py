"""Lot 2, point 1 : une seule barre de retour, et « Écouter » seulement sur du texte.

Les messages sont semés dans la base du serveur (le vrai chemin de rendu :
`renderHistory` → `appendMessage` → `drawArtifacts`), puis la page est rechargée.
Aucun modèle, aucune clé : ce sont des lignes de store, exactement ce qu'un
rechargement affiche.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.e2e

from .conftest import Server, open_app  # noqa: E402

#: 1×1 PNG : assez pour que `drawArtifacts` peigne une vraie figure.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
LONG_ANSWER = (
    "Voici la courbe demandée : elle est tracée en Agg, puis publiée comme image "
    "dans la bulle. Le fichier reste dans le dossier du run."
)


def seed(server: Server, messages: list[tuple[str, str, list[dict] | None]]) -> str:
    """Insert one session with its messages; returns the session id."""
    database = server.home / "uh.db"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    session_id = f"e2e-lot2-{uuid.uuid4().hex[:8]}"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO sessions (id, created_at, updated_at, title, model, provider, metadata)"
            " VALUES (?, ?, ?, ?, '', '', '{}')",
            (session_id, now, now, "lot 2 — retour"),
        )
        for role, content, artifacts in messages:
            columns = "session_id, role, content, created_at"
            values: list[object] = [session_id, role, content, now]
            if artifacts:
                columns += ", artifacts"
                values.append(json.dumps(artifacts))
            connection.execute(
                f"INSERT INTO messages ({columns}) VALUES ({','.join('?' * len(values))})",
                values,
            )
        connection.commit()
    finally:
        connection.close()
    return session_id


def reload_on_session(page, server: Server) -> None:
    page.goto(server.url, wait_until="domcontentloaded")
    page.wait_for_selector("#input", timeout=30000)
    page.wait_for_selector("#messages .msg", timeout=30000)
    page.wait_for_timeout(400)


def test_an_image_only_answer_gets_one_bar_and_no_listen_button(browser, server: Server) -> None:
    """Le cas du mouton : la barre de l'artefact fait foi, et rien à lire."""
    context, page = open_app(browser, server)
    try:
        seed(
            server,
            [
                ("user", "dessine un mouton", None),
                (
                    "assistant",
                    "![Mouton](data:image/png;base64," + base64.b64encode(PNG).decode() + ")",
                    [
                        {
                            "mime": "image/png",
                            "data_url": "data:image/png;base64," + base64.b64encode(PNG).decode(),
                            "bytes": len(PNG),
                        }
                    ],
                ),
            ],
        )
        reload_on_session(page, server)

        assert page.eval_on_selector_all(".msg.assistant", "els => els.length") == 1
        assert page.eval_on_selector_all(".tool-artifact", "els => els.length") == 1, (
            "l'artefact doit être peint : c'est lui qui porte la barre utile"
        )
        assert page.eval_on_selector_all(".text-feedback", "els => els.length") == 0, (
            "une réponse qui n'est qu'une image n'a pas de barre de texte"
        )
        assert page.eval_on_selector_all(".answer-audio", "els => els.length") == 0, (
            "aucun bouton Écouter sur un message sans prose"
        )
    finally:
        context.close()


def test_a_text_answer_gets_the_bar_and_the_listen_button(browser, server: Server) -> None:
    context, page = open_app(browser, server)
    try:
        seed(server, [("user", "trace la courbe", None), ("assistant", LONG_ANSWER, None)])
        reload_on_session(page, server)

        assert page.eval_on_selector_all(".text-feedback", "els => els.length") == 1
        assert page.eval_on_selector_all(".answer-audio", "els => els.length") == 1
        # Deux boutons-icônes nommés : l'état ne repose pas sur la couleur seule.
        labels = page.eval_on_selector_all(
            ".text-feedback .feedback-vote", "els => els.map((el) => el.getAttribute('aria-label'))"
        )
        assert labels == ["Réponse utile", "Réponse à éviter"]
        # Discrète par défaut, révélée au survol.
        before = page.eval_on_selector(".text-feedback", "el => getComputedStyle(el).opacity")
        page.hover(".msg.assistant")
        page.wait_for_timeout(250)
        after = page.eval_on_selector(".text-feedback", "el => getComputedStyle(el).opacity")
        assert before == "0", f"la barre doit être discrète au repos (opacity={before})"
        assert after == "1", f"la barre doit apparaître au survol (opacity={after})"
    finally:
        context.close()


def test_a_system_notice_has_no_bar(browser, server: Server) -> None:
    context, page = open_app(browser, server)
    try:
        seed(
            server,
            [
                ("user", "aide", None),
                ("system", "Commande inconnue : /inconnue — tape /help pour la liste.", None),
            ],
        )
        reload_on_session(page, server)

        assert page.eval_on_selector_all(".msg.system", "els => els.length") == 1
        assert page.eval_on_selector_all(".text-feedback", "els => els.length") == 0
        assert page.eval_on_selector_all(".answer-audio", "els => els.length") == 0
    finally:
        context.close()


def test_three_failed_listen_clicks_leave_one_message(browser, server: Server) -> None:
    """Un échec de synthèse ne doit pas empiler trois phrases identiques."""
    context, page = open_app(browser, server)
    try:
        seed(server, [("user", "raconte", None), ("assistant", LONG_ANSWER, None)])
        page.route(
            "**/api/audio/speak",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps({"detail": "service vocal indisponible"}),
            ),
        )
        reload_on_session(page, server)
        # Depuis G12, la voix du navigateur passe avant le serveur : on la retire pour
        # éprouver le chemin serveur (503) que ce test mesure depuis toujours.
        page.evaluate(
            "() => Object.defineProperty(window, 'speechSynthesis', "
            "{ configurable: true, get: () => undefined })"
        )

        button = page.locator(".answer-audio button")
        assert button.count() == 1
        for _ in range(3):
            button.click()
            page.wait_for_timeout(250)

        statuses = page.eval_on_selector_all(
            ".answer-audio .tool-artifact-status", "els => els.map((el) => el.textContent)"
        )
        assert len(statuses) == 1, f"un seul nœud de statut attendu, trouvé : {statuses}"
        assert "voix impossible" in statuses[0]
        assert button.is_enabled(), "le bouton doit être réarmé après l'échec"
    finally:
        context.close()
