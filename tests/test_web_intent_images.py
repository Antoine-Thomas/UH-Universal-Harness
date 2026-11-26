"""G11 : `/web donne moi la photo d'un chat` doit chercher des images.

Les trois briques existaient — `uh/image_search.py`, `/web image <q>`,
`web_image_search` — mais seule la forme explicite atteignait la recherche d'images :
`/web <texte libre>` partait toujours vers la recherche web textuelle, d'ou les liens
Shutterstock/Unsplash au lieu de photos. Le test porte sur le routage, pas sur l'API :
`_web_images` et `_web_search` sont remplaces par des espions.
"""

from __future__ import annotations

import pytest

from uh import commands


@pytest.fixture()
def routes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def images(session: object, query: str) -> str:
        calls.append(("images", query))
        return "images"

    def search(session: object, query: str) -> str:
        calls.append(("search", query))
        return "search"

    monkeypatch.setattr(commands, "_web_images", images)
    monkeypatch.setattr(commands, "_web_search", search)
    return calls


@pytest.mark.parametrize(
    ("asked", "subject"),
    [
        ("donne moi la photo d'un chat", "chat"),
        ("donne-moi une photo de chat", "chat"),
        ("photo de chien", "chien"),
        ("une vraie image de montagne", "montagne"),
        ("montre-moi une image de plage", "plage"),
        ("cherche des photos de velo", "velo"),
        ("photo of a cat", "cat"),
        ("picture of a dog", "dog"),
    ],
)
def test_a_photo_request_reaches_the_image_search(routes, asked: str, subject: str) -> None:
    assert commands._cmd_web(None, asked) == "images"
    assert routes == [("images", subject)], routes


@pytest.mark.parametrize(
    "asked",
    ["actualites python", "quelle heure est-il", "documentation fastapi"],
)
def test_a_plain_web_query_stays_a_web_search(routes, asked: str) -> None:
    assert commands._cmd_web(None, asked) == "search"
    assert routes == [("search", asked)], routes


def test_the_explicit_form_still_works(routes) -> None:
    commands._cmd_web(None, "image un chat")
    assert routes == [("images", "un chat")], routes
