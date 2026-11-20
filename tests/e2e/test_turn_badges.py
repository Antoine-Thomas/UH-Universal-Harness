"""Le numéro de tour dans le fil, prouvé à l'écran (P3-16c lot 2).

Le fil est amorcé par la base elle-même (deux échanges), puis la page est ouverte : le
badge doit apparaître sur les messages de l'utilisateur, et il doit compter la même chose
que la Télémétrie - un tour par message utilisateur.

    pytest -o addopts="" -m e2e tests/e2e/test_turn_badges.py -q
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402


def seed_two_turns(server) -> str:
    """Écrit deux tours dans le store du serveur, comme un vrai échange les laisse.

    Le store est celui du serveur de test (UH_HOME jetable) : rien n'est simulé dans
    l'interface, seul l'état de départ est posé.
    """
    from uh.store import Store

    store = Store(server.home / "uh.db")
    try:
        session_id = store.create_session(title="preuve des tours", model="stub:1b", provider="stub")
        for question, answer in [
            ("premier tour", "premiere reponse"),
            ("deuxieme tour", "seconde reponse"),
        ]:
            store.append_message(session_id, {"role": "user", "content": question})
            store.append_message(session_id, {"role": "assistant", "content": answer})
        return session_id
    finally:
        store.close()


def test_the_badges_number_each_turn_and_match_the_api(browser, server) -> None:
    session_id = seed_two_turns(server)
    context, page = open_app(browser, server)
    try:
        page.wait_for_selector("#messages .turn-badge", timeout=20000)
        badges = page.eval_on_selector_all(
            "#messages .turn-badge", "els => els.map(e => e.textContent.trim())"
        )
        assert badges == ["Tour #1", "Tour #2"], f"badges rendus : {badges}"

        # Un badge par message de l'utilisateur, pas un par bulle.
        users = page.eval_on_selector_all("#messages .msg.user", "els => els.length")
        assert users == 2 and len(badges) == users

        # Le badge est dans la bulle utilisateur, avant son texte.
        first = page.evaluate(
            """() => {
                const box = document.querySelector('#messages .msg.user');
                const badge = box.querySelector('.turn-badge');
                return { inside: Boolean(badge), text: box.textContent, first: box.firstElementChild === badge };
            }"""
        )
        assert first["inside"] and first["first"], first
        assert "premier tour" in first["text"]

        # Et l'API compte le même nombre de tours.
        api = httpx.get(
            server.base + f"/api/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {server.token()}"},
            timeout=20,
        ).json()
        assert api["turns"] == 2
        assert [m["turn"] for m in api["messages"]] == [1, 1, 2, 2]
    finally:
        context.close()
