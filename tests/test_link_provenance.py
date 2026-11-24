"""La provenance des URL d'une réponse : normalisation, extraction, refus par défaut.

Ces tests portent sur les fonctions pures qui décident, avant toute page, quelle URL
d'une réponse vient d'un résultat d'outil, de l'opérateur, ou du modèle. C'est la
règle « aucune requête pour une URL écrite par le modèle sans clic », côté serveur.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from uh import link_preview


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("HTTPS://Example.COM/a", "https://example.com/a"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://user:secret@example.com/a", "https://example.com/a"),
        ("https://example.com/a#fragment", "https://example.com/a"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com/A?Q=1", "https://example.com/A?Q=1"),
        ("ftp://example.com/a", None),
        ("javascript:alert(1)", None),
        ("", None),
    ],
)
def test_normalize_compares_what_designates_the_same_resource(
    raw: str, expected: "str | None"
) -> None:
    assert link_preview.normalize(raw) == expected


def test_a_trailing_punctuation_is_not_part_of_the_url() -> None:
    assert link_preview.urls_in("Voir https://example.com/a, puis la suite.") == [
        "https://example.com/a"
    ]


def test_urls_are_extracted_once() -> None:
    found = link_preview.urls_in("https://a.example/x et HTTPS://A.example/x encore")

    assert found == ["https://a.example/x"]


def messages() -> List[Dict[str, Any]]:
    return [
        {"role": "assistant", "content": "je propose https://modele.example/piste"},
        {"role": "tool", "name": "web", "content": "- https://outil.example/page\n"},
        {"role": "user", "content": "lis https://operateur.example/doc"},
    ]


def test_a_tool_url_is_recognised() -> None:
    found = link_preview.classify(["https://outil.example/page"], messages())

    assert found == [{"url": "https://outil.example/page", "origin": "tool"}]


def test_an_operator_url_is_recognised() -> None:
    found = link_preview.classify(["https://operateur.example/doc"], messages())

    assert found[0]["origin"] == "operator"


def test_a_model_url_stays_model() -> None:
    found = link_preview.classify(["https://modele.example/piste"], messages())

    assert found[0]["origin"] == "model", "un texte du modèle ne prouve rien"


def test_an_unknown_url_is_model_by_default() -> None:
    found = link_preview.classify(["https://inconnu.example/x"], None)

    assert found[0]["origin"] == "model", "provenance absente = refus par défaut"


def test_one_character_of_difference_does_not_inherit_the_provenance() -> None:
    """La comparaison porte sur la forme normalisée, pas sur une ressemblance.

    Sans cela, un modèle obtiendrait la provenance d'un résultat d'outil en
    ajoutant un caractère à une URL qui en venait.
    """
    found = link_preview.classify(
        [
            "https://outil.example/page",  # la vraie
            "https://outil.example/page?from=mail",  # une requête en plus
            "https://outil.example/page/",  # une barre de plus
            "https://outil.example/Page",  # une casse de chemin
        ],
        messages(),
    )

    assert [item["origin"] for item in found] == ["tool", "model", "model", "model"]


def test_normalisation_gives_the_provenance_to_the_disguised_form() -> None:
    """L'inverse est vrai aussi : une copie déguisée de la vraie URL est reconnue."""
    found = link_preview.classify(["HTTPS://Outil.Example:443/page#top"], messages())

    assert found[0]["origin"] == "tool"


def test_a_port_that_is_not_the_scheme_default_is_kept() -> None:
    """Un port n'est retiré que s'il est le défaut *de son propre schéma*.

    `:80` n'est pas le défaut de `https`, et `:443` n'est pas celui de `http` :
    les retirer changerait la ressource désignée, donc la provenance comparée.
    """
    assert link_preview.normalize("https://example.com:80/a") == "https://example.com:80/a"
    assert link_preview.normalize("http://example.com:443/a") == "http://example.com:443/a"
    assert link_preview.normalize("https://example.com:443/a") == "https://example.com/a"
    assert link_preview.normalize("http://example.com:80/a") == "http://example.com/a"


def test_the_scheme_itself_never_changes() -> None:
    """Le schéma est baissé en casse, jamais remplacé : `https` reste `https`."""
    for raw, expected_prefix in (
        ("HTTPS://Example.com/a", "https://"),
        ("https://user:secret@Example.com:80/a", "https://"),
        ("HTTP://Example.com:443/a", "http://"),
    ):
        assert link_preview.normalize(raw).startswith(expected_prefix)  # type: ignore[union-attr]


def test_an_empty_query_is_the_same_resource() -> None:
    """Décision assumée : `?` vide ne désigne pas une autre page.

    Le modèle n'y gagne rien — c'est la même ressource que celle du résultat
    d'outil, donc la même requête, donc le même risque. Le refus par défaut
    continue de valoir pour tout ce qui désigne *autre chose*.
    """
    found = link_preview.classify(["https://outil.example/page?"], messages())

    assert found[0]["origin"] == "tool"
    assert found[0]["url"] == "https://outil.example/page"
