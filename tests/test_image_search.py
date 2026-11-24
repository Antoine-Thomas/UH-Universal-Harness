"""Les moteurs d'images sans clé : ce qu'ils rendent, et ce qu'ils avouent.

Les charges utiles sont celles mesurées le 24/11, réduites aux champs utilisés.
Aucun test ne sort de la machine : ``httpx.MockTransport`` répond à la place des
API.
"""

from __future__ import annotations

import httpx
import pytest

from uh import image_search

OPENVERSE_BODY = {
    "result_count": 240,
    "results": [
        {
            "id": "4a8aa2a9",
            "title": "le cochon",
            "foreign_landing_url": "https://www.flickr.com/photos/30533017@N00/16250817413",
            "license": "by",
            "license_version": "2.0",
            "creator": "pilllpat (agence eureka)",
            "thumbnail": "https://api.openverse.org/v1/images/4a8aa2a9/thumb/",
            "provider": "flickr",
            "source": "flickr",
        },
        {  # sans vignette : rien à dessiner, donc rien à proposer
            "title": "sans image",
            "foreign_landing_url": "https://exemple.fr/x",
            "license": "by",
        },
    ],
}

COMMONS_BODY = {
    "query": {
        "pages": [
            {
                "pageid": 9437797,
                "title": "File:Felis catus-cat on snow.jpg",
                "imageinfo": [
                    {
                        "thumburl": "https://thumb.wikimedia.org/wikipedia/commons/thumb/b/b6/cat.jpg/330px-cat.jpg",
                        "url": "https://upload.wikimedia.org/wikipedia/commons/b/b6/cat.jpg",
                        "mime": "image/jpeg",
                        "descriptionurl": (
                            "https://commons.wikimedia.org/wiki/File:Felis_catus-cat_on_snow.jpg"
                        ),
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY-SA 3.0"},
                            "Artist": {
                                "value": '<a href="//commons.wikimedia.org/wiki/User:Von.grzanka" '
                                'title="User:Von.grzanka">Von.grzanka</a>'
                            },
                            "Credit": {"value": '<span lang="en">Own work</span>'},
                        },
                    }
                ],
            },
            {  # un SVG : écarté à la source, le proxy le refuserait aussi
                "pageid": 42,
                "title": "File:Plan.svg",
                "imageinfo": [
                    {
                        "thumburl": "https://thumb.wikimedia.org/plan.svg.png",
                        "mime": "image/svg+xml",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Plan.svg",
                    }
                ],
            },
        ]
    }
}


def client_for(body: dict, *, status: int = 200, calls: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


# --------------------------------------------------------------------------- #
# Openverse
# --------------------------------------------------------------------------- #
def test_openverse_maps_the_measured_fields() -> None:
    with client_for(OPENVERSE_BODY) as client:
        hits, reason = image_search.openverse("cochon", language="fr", client=client)

    assert reason == ""
    assert len(hits) == 1, "l'entrée sans vignette ne devient pas une carte"
    hit = hits[0]
    assert hit.title == "le cochon"
    assert hit.licence == "by 2.0"
    assert hit.author == "pilllpat (agence eureka)"
    assert hit.provider == "flickr"
    assert hit.domain == "www.flickr.com"
    assert hit.thumb == "https://api.openverse.org/v1/images/4a8aa2a9/thumb/"
    assert hit.page.startswith("https://www.flickr.com/photos/")


def test_openverse_asks_for_the_language_it_was_given() -> None:
    calls: list[httpx.Request] = []
    with client_for(OPENVERSE_BODY, calls=calls) as client:
        image_search.openverse("cochon", language="fr", client=client)

    params = calls[0].url.params
    assert params["q"] == "cochon"
    assert params["language"] == "fr"
    assert params["page_size"] == "8"


def test_openverse_without_language_sends_no_language_parameter() -> None:
    calls: list[httpx.Request] = []
    with client_for(OPENVERSE_BODY, calls=calls) as client:
        image_search.openverse("pig", language="", client=client)

    assert "language" not in calls[0].url.params


def test_an_http_error_is_reported_not_raised() -> None:
    with client_for({}, status=503) as client:
        hits, reason = image_search.openverse("cochon", client=client)

    assert hits == []
    assert reason == "HTTP 503"


def test_a_broken_body_is_reported_not_raised() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>pas du json</html>")

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as client:
        hits, reason = image_search.openverse("cochon", client=client)

    assert hits == [] and reason


# --------------------------------------------------------------------------- #
# Wikimedia Commons
# --------------------------------------------------------------------------- #
def test_commons_maps_the_measured_fields_and_strips_html() -> None:
    with client_for(COMMONS_BODY) as client:
        hits, reason = image_search.wikimedia("cat", client=client)

    assert reason == ""
    assert len(hits) == 1, "le SVG est écarté à la source"
    hit = hits[0]
    assert hit.title == "Felis catus-cat on snow.jpg", "le préfixe File: est retiré"
    assert hit.licence == "CC BY-SA 3.0"
    assert hit.author == "Von.grzanka", "l'auteur arrive en HTML : il est réduit en texte"
    assert "<a" not in hit.author
    assert hit.provider == "commons"
    assert hit.domain == "commons.wikimedia.org"
    assert hit.thumb.startswith("https://thumb.wikimedia.org/")


def test_commons_asks_for_bitmaps_and_a_320_pixel_thumbnail() -> None:
    calls: list[httpx.Request] = []
    with client_for(COMMONS_BODY, calls=calls) as client:
        image_search.wikimedia("cat", client=client)

    params = calls[0].url.params
    assert params["gsrnamespace"] == "6"
    assert params["iiurlwidth"] == "320"
    assert "bitmap" in params["gsrsearch"]
    assert params["prop"] == "imageinfo"


def test_commons_falls_back_to_the_credit_when_the_artist_is_absent() -> None:
    body = {
        "query": {
            "pages": [
                {
                    "title": "File:x.jpg",
                    "imageinfo": [
                        {
                            "thumburl": "https://thumb.wikimedia.org/x.jpg",
                            "mime": "image/jpeg",
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:x.jpg",
                            "extmetadata": {
                                "LicenseShortName": {"value": "CC0"},
                                "Credit": {"value": '<span lang="en">Own work</span>'},
                            },
                        }
                    ],
                }
            ]
        }
    }
    with client_for(body) as client:
        hits, _ = image_search.wikimedia("x", client=client)

    assert hits[0].author == "Own work"


# --------------------------------------------------------------------------- #
# l'agrégat
# --------------------------------------------------------------------------- #
def test_the_first_engine_to_fill_the_quota_stops_the_search() -> None:
    calls: list[httpx.Request] = []
    with client_for(OPENVERSE_BODY, calls=calls) as client:
        result = image_search.search_images("cochon", limit=1, client=client)

    assert len(result.hits) == 1
    assert result.engines == ["openverse"]
    assert result.failed == {}
    assert len(calls) == 1, "wikimedia n'est pas interrogé pour rien"


def test_a_failing_engine_is_named_and_the_other_still_answers() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.host)
        if "openverse" in request.url.host:
            return httpx.Response(500)
        return httpx.Response(200, json=COMMONS_BODY)

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as client:
        result = image_search.search_images("cat", client=client)

    assert result.failed == {"openverse": "HTTP 500"}
    assert result.engines == ["wikimedia"]
    assert len(result.hits) == 1
    assert asked == ["api.openverse.org", "commons.wikimedia.org"]


def test_an_engine_that_raises_does_not_break_the_search() -> None:
    def broken(*_args: object, **_kwargs: object):
        raise RuntimeError("boum")

    original = image_search.ENGINES
    image_search.ENGINES = (("openverse", broken), ("wikimedia", original[1][1]))
    try:
        with client_for(COMMONS_BODY) as client:
            result = image_search.search_images("cat", client=client)
    finally:
        image_search.ENGINES = original

    assert "openverse" in result.failed and "boum" in result.failed["openverse"]
    assert len(result.hits) == 1


def test_an_empty_query_asks_nothing() -> None:
    result = image_search.search_images("   ")
    assert result.hits == []
    assert result.failed["query"] == "requête vide"


def test_only_the_named_engines_are_asked() -> None:
    with client_for(COMMONS_BODY) as client:
        result = image_search.search_images("cat", client=client, engines=["wikimedia"])

    assert result.engines == ["wikimedia"]
    assert all(hit.provider == "commons" for hit in result.hits)


# --------------------------------------------------------------------------- #
# le texte affiché
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('<a href="x">Von.grzanka</a>', "Von.grzanka"),
        ("A &amp; B", "A & B"),
        ("  ligne\n  suivante  ", "ligne suivante"),
        ("<span lang='en'>Own work</span>", "Own work"),
        (None, ""),
    ],
)
def test_plain_never_returns_markup(raw: object, expected: str) -> None:
    assert image_search.plain(raw) == expected
