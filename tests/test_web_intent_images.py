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
        ("je voudrais la photo d'un chat", "chat"),
        ("je veux une photo de chien", "chien"),
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
    ("asked", "subject"),
    [
        ("chat images", "chat"),
        ("chat image", "chat"),
        ("chat photo", "chat"),
        ("chat photos", "chat"),
        ("un chat en images", "chat"),
    ],
)
def test_the_keyword_last_also_reaches_the_image_search(routes, asked: str, subject: str) -> None:
    """L'ordre des mots ne doit pas changer le resultat : c'est le sens qui compte.

    Constat de l'operateur (26/11) : `/web image chat` rendait la galerie,
    `/web chat images` ne rendait que des liens cliquables. Le mot-cle en fin de
    requete est la meme demande.
    """
    assert commands._cmd_web(None, asked) == "images"
    assert routes == [("images", subject)], routes


@pytest.mark.parametrize(
    "asked",
    ["actualites python", "quelle heure est-il", "documentation fastapi"],
)
def test_a_plain_web_query_stays_a_web_search(routes, asked: str) -> None:
    assert commands._cmd_web(None, asked) == "search"
    assert routes == [("search", asked)], routes


def test_a_generation_request_is_not_answered_with_stock_photos(routes) -> None:
    """« dessine » demande une fabrication : la phototheque n'est pas la reponse.

    Sans cette garde, le mot d'image en fin de phrase suffirait a detourner une
    demande de generation vers une galerie de photos existantes (c'est G13).
    """
    assert commands._cmd_web(None, "dessine un chat images") == "search"
    assert routes == [("search", "dessine un chat images")], routes


def test_the_explicit_form_still_works(routes) -> None:
    commands._cmd_web(None, "image un chat")
    assert routes == [("images", "un chat")], routes


@pytest.mark.parametrize(
    ("asked", "subject"),
    [("image de chat", "chat"), ("images du velo", "velo")],
)
def test_the_explicit_form_drops_the_link_word(routes, asked: str, subject: str) -> None:
    """« image de chat » cherche « chat », pas « de chat ».

    Sans ce nettoyage, Openverse recoit un sujet qui contient le mot de liaison :
    la requete reste lisible mais elle n'est plus la demande.
    """
    commands._cmd_web(None, asked)
    assert routes == [("images", subject)], routes


def test_the_system_prompt_sends_a_photo_request_to_the_photo_tool(tmp_path) -> None:
    """Le chat normal doit savoir quel outil rend une photo, et lequel dessine.

    `web_image_search` n'etait pas offerte au modele : une demande de photo
    realiste partait donc en `execute_code` (illustration) ou en `web_search`
    (liens). Le prompt nomme l'outil, comme il nomme deja `execute_code`.
    """
    from uh.console import system_message

    content = system_message(tmp_path)["content"]
    assert "web_image_search" in content
    assert "execute_code" in content


@pytest.mark.parametrize(
    ("asked", "subject"),
    [
        ("photo d'un dessin animé", "dessin animé"),
        ("montre-moi une image d'un dessin anime", "dessin anime"),
        ("images de dessins animés", "dessins animés"),
    ],
)
def test_a_cartoon_is_a_photo_request_not_a_generation(routes, asked: str, subject: str) -> None:
    """« dessin animé » est un sujet, pas un verbe : la photothèque reste la bonne route.

    La garde de génération bloquait ces demandes parce qu'elle contenait le nom
    « dessin ». Seules les formes verbales (dessine, dessiner, génère, crée…)
    expriment une fabrication — la revue de la PR #78 l'a relevé.
    """
    assert commands._cmd_web(None, asked) == "images"
    assert routes == [("images", subject)], routes


@pytest.mark.parametrize(
    ("asked", "subject"),
    [
        ("trouve moi la photo d'un chat", "chat"),
        ("trouve-moi la photo d'un chat", "chat"),
        ("affiche moi une image de chien", "chien"),
        ("affiche-moi des photos de velo", "velo"),
        ("cherche moi des photos de chat", "chat"),
    ],
)
def test_the_moi_of_a_verb_does_not_hide_the_subject(routes, asked: str, subject: str) -> None:
    """« trouve moi la photo… » est une demande de photo, pas une recherche texte.

    Relevé par un essai réel de l'opérateur : `/web trouve moi la photo d'un chat`
    partait en DuckDuckGo. Les verbes étaient reconnus, mais pas le « moi » qui les
    suit (`trouve moi` ≠ `trouve`) : le préambule s'arrêtait au verbe, et le sujet
    restait collé à « moi la photo… ». Le particule est maintenant optionnelle pour
    tous les verbes, pas seulement pour `donne`/`montre`.
    """
    assert commands._cmd_web(None, asked) == "images"
    assert routes == [("images", subject)], routes
