"""La carte YouTube : ce que oEmbed donne, ce qu'il ne donne pas, et les refus.

Charge utile = celle mesurée le 24/11 (``Me at the zoo``), réduite aux champs
utilisés. Aucun test ne sort de la machine.
"""

from __future__ import annotations

import httpx
import pytest

from uh import video_preview

WATCH = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
BODY = {
    "author_name": "jawed",
    "author_url": "https://www.youtube.com/@jawed",
    "height": 113,
    "html": '<iframe width="200" height="113" src="https://www.youtube.com/embed/jNQXAC9IVRw"></iframe>',
    "provider_name": "YouTube",
    "provider_url": "https://www.youtube.com/",
    "thumbnail_height": 360,
    "thumbnail_url": "https://i.ytimg.com/vi/jNQXAC9IVRw/hqdefault.jpg",
    "thumbnail_width": 480,
    "title": "Me at the zoo",
    "type": "video",
    "version": "1.0",
    "width": 200,
}


def client_for(body: dict, *, status: int = 200, calls: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


# --------------------------------------------------------------------------- #
# quelles adresses sont « une vidéo »
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "https://youtube.com/watch?v=abc",
        "https://m.youtube.com/watch?v=abc",
        "https://youtu.be/jNQXAC9IVRw",
        "https://www.youtube.com/shorts/abc123",
        "https://www.youtube.com/live/abc123",
        "https://www.youtube-nocookie.com/embed/abc123",
    ],
)
def test_youtube_addresses_are_recognised(url: str) -> None:
    assert video_preview.is_video_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com.evil.example/watch?v=abc",  # suffixe trompeur
        "https://exemple.fr/watch?v=abc",
        "javascript:alert(1)",
        "file:///C:/x",
        "https://www.youtube.com/",  # pas une vidéo
        "https://www.youtube.com/channel/UC123",  # une chaîne, pas une vidéo
        "https://youtu.be/",  # rien après l'hôte
        "https://user:pass@www.youtube.com/watch?v=abc",
        "",
    ],
)
def test_anything_else_is_refused(url: str) -> None:
    assert not video_preview.is_video_url(url)


# --------------------------------------------------------------------------- #
# la carte
# --------------------------------------------------------------------------- #
def test_the_card_carries_title_channel_and_thumbnail() -> None:
    with client_for(BODY) as client:
        card, reason = video_preview.preview(WATCH, client=client)

    assert reason == ""
    assert card is not None
    assert card.title == "Me at the zoo"
    assert card.channel == "jawed"
    assert card.channel_url == "https://www.youtube.com/@jawed"
    assert card.thumb == "https://i.ytimg.com/vi/jNQXAC9IVRw/hqdefault.jpg"
    assert card.page == WATCH


def test_the_iframe_html_is_never_returned() -> None:
    """oEmbed livre un champ `html` : la carte n'en veut pas, la CSP non plus."""
    with client_for(BODY) as client:
        card, _ = video_preview.preview(WATCH, client=client)

    assert card is not None
    assert not hasattr(card, "html")
    assert "<iframe" not in repr(card)


def test_the_description_is_empty_and_says_why() -> None:
    with client_for(BODY) as client:
        card, _ = video_preview.preview(WATCH, client=client)

    assert card is not None
    assert card.description == ""
    assert card.has_description is False, "sans clé YouTube Data, il n'y a pas de description"


def test_the_oembed_call_only_carries_the_video_url() -> None:
    calls: list[httpx.Request] = []
    with client_for(BODY, calls=calls) as client:
        video_preview.preview("https://youtu.be/jNQXAC9IVRw?t=42", client=client)

    assert calls[0].url.host == "www.youtube.com"
    assert calls[0].url.path == "/oembed"
    assert calls[0].url.params["url"] == "https://youtu.be/jNQXAC9IVRw?t=42"
    assert calls[0].url.params["format"] == "json"


def test_tracking_parameters_are_dropped_from_the_page_url() -> None:
    assert (
        video_preview.page_url("https://www.youtube.com/watch?v=abc&utm_source=x&feature=share")
        == "https://www.youtube.com/watch?v=abc"
    )


def test_a_private_video_is_reported_in_a_phrase() -> None:
    with client_for({}, status=404) as client:
        card, reason = video_preview.preview(WATCH, client=client)

    assert card is None
    assert "n'existe pas ou n'est pas publique" in reason


def test_an_http_error_is_reported_not_raised() -> None:
    with client_for({}, status=500) as client:
        card, reason = video_preview.preview(WATCH, client=client)

    assert card is None and reason == "HTTP 500"


def test_a_body_without_a_title_is_refused() -> None:
    with client_for({"author_name": "x"}) as client:
        card, reason = video_preview.preview(WATCH, client=client)

    assert card is None and "sans titre" in reason


def test_a_non_youtube_address_never_reaches_the_network() -> None:
    calls: list[httpx.Request] = []
    with client_for(BODY, calls=calls) as client:
        card, reason = video_preview.preview("https://exemple.fr/watch?v=abc", client=client)

    assert card is None
    assert calls == [], "aucune requête : l'adresse est refusée avant"
    assert "pas une adresse YouTube" in reason
