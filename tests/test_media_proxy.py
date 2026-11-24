"""Le proxy de miniatures : tout ce qu'il refuse, et ce qu'il fait quand il accepte.

Le réseau est simulé des deux côtés : `resolver` décide des adresses, `transport`
(``httpx.MockTransport``) décide des réponses. Aucun test ne sort de la machine —
c'est le seul moyen de vérifier les refus sans dépendre d'un DNS réel.
"""

from __future__ import annotations

import ipaddress
from typing import Any

import httpx
import pytest

from uh import media_proxy

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

PUBLIC = "93.184.215.14"
HOST = "api.openverse.org"
URL = f"https://{HOST}/v1/images/abc/thumb/"


def resolver_for(*addresses: str):
    def resolve(_host: str, _port: int, **_kwargs: Any) -> list:
        return [(2, 1, 6, "", (address, 443)) for address in addresses]

    return resolve


def transport_for(*, content: bytes = PNG, status: int = 200, headers: dict | None = None):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, content=content, headers=headers or {})

    return httpx.MockTransport(handler), calls


# --------------------------------------------------------------------------- #
# ce qui ne passe pas la porte
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        f"http://{HOST}/v1/images/abc/thumb/",  # clair
        f"ftp://{HOST}/x.png",
        "file:///C:/Windows/win.ini",
        "data:image/png;base64,AAAA",
        f"https://{HOST}:8443/v1/images/abc/thumb/",  # port non standard
        f"https://user:pass@{HOST}/v1/images/abc/thumb/",  # identifiants
        "https://127.0.0.1/x.png",  # IP littérale, hors liste
        "https://169.254.169.254/latest/meta-data/",
        "https://exemple.fr/x.png",  # hôte inconnu
        "https://api.openverse.org.evil.example/x.png",  # suffixe trompeur
        "",
        "   ",
    ],
)
def test_urls_that_are_never_fetched(url: str) -> None:
    with pytest.raises(media_proxy.MediaRefused):
        media_proxy.pin(url, resolver=resolver_for(PUBLIC))


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "169.254.169.254", "192.168.1.10", "10.0.0.1", "::1", "fe80::1", "0.0.0.0"],
)
def test_a_name_resolving_to_a_local_address_is_refused(address: str) -> None:
    with pytest.raises(media_proxy.MediaRefused, match="non publique"):
        media_proxy.pin(URL, resolver=resolver_for(address))


def test_one_private_answer_is_enough_to_refuse() -> None:
    """Un resolver qui répond deux adresses ne doit pas en cacher une privée."""
    with pytest.raises(media_proxy.MediaRefused, match="non publique"):
        media_proxy.pin(URL, resolver=resolver_for(PUBLIC, "127.0.0.1"))


def test_a_name_that_does_not_resolve_is_refused() -> None:
    def broken(_host: str, _port: int, **_kwargs: Any) -> list:
        raise OSError("getaddrinfo failed")

    with pytest.raises(media_proxy.MediaRefused, match="introuvable"):
        media_proxy.pin(URL, resolver=broken)


def test_is_public_agrees_with_ipaddress() -> None:
    assert media_proxy.is_public(PUBLIC)
    assert media_proxy.is_public("2606:4700:10::6814:1101")
    for address in (
        "127.0.0.1",
        "::1",
        "169.254.169.254",
        "192.168.0.1",
        "224.0.0.1",
        "pas-une-ip",
    ):
        assert not media_proxy.is_public(address), address


# --------------------------------------------------------------------------- #
# ce qui passe, et comment la connexion part
# --------------------------------------------------------------------------- #
def test_the_connection_goes_to_the_resolved_address_not_to_the_name() -> None:
    """Le nom reste dans `Host` et dans TLS ; le socket part sur l'IP résolue."""
    transport, calls = transport_for()

    thumb = media_proxy.fetch(
        URL, resolver=resolver_for(PUBLIC), transport=transport, max_bytes=1024
    )

    assert thumb.mime == "image/png"
    assert thumb.data == PNG
    assert len(calls) == 1
    request = calls[0]
    assert request.url.host == PUBLIC, "le socket doit partir sur l'adresse résolue"
    assert request.headers["host"] == HOST, "le serveur distant doit voir son nom"
    assert request.extensions.get("sni_hostname") == HOST, "TLS vérifie le nom, pas l'IP"
    assert request.headers["connection"].lower() == "close", "aucune poignée réutilisable"


@pytest.mark.parametrize(
    ("body", "mime"),
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp")],
)
def test_the_four_accepted_formats_are_decided_by_magic_bytes(body: bytes, mime: str) -> None:
    transport, _ = transport_for(content=body)

    thumb = media_proxy.fetch(URL, resolver=resolver_for(PUBLIC), transport=transport)

    assert thumb.mime == mime


def test_an_svg_served_as_an_image_is_refused() -> None:
    """Le type se lit dans les octets : un SVG déguisé ne passe pas."""
    transport, _ = transport_for(content=SVG, headers={"content-type": "image/png"})

    with pytest.raises(media_proxy.MediaRefused, match="format non reconnu"):
        media_proxy.fetch(URL, resolver=resolver_for(PUBLIC), transport=transport)


def test_a_body_over_the_cap_is_refused_not_truncated() -> None:
    transport, _ = transport_for(content=PNG + b"\x00" * 4096)

    with pytest.raises(media_proxy.MediaRefused, match="trop lourde"):
        media_proxy.fetch(URL, resolver=resolver_for(PUBLIC), transport=transport, max_bytes=1024)


def test_a_declared_length_over_the_cap_is_refused_before_reading() -> None:
    transport, _ = transport_for(
        content=PNG, headers={"content-length": str(media_proxy.MAX_BYTES + 1)}
    )

    with pytest.raises(media_proxy.MediaRefused, match="trop lourde"):
        media_proxy.fetch(URL, resolver=resolver_for(PUBLIC), transport=transport)


def test_a_redirect_is_refused_and_never_followed() -> None:
    transport, calls = transport_for(
        status=302, headers={"location": "https://169.254.169.254/latest/meta-data/"}
    )

    with pytest.raises(media_proxy.MediaRefused, match="redirection refusée"):
        media_proxy.fetch(URL, resolver=resolver_for(PUBLIC), transport=transport)

    assert len(calls) == 1, "une seule requête : la cible de la redirection n'est pas contactée"


def test_a_non_200_answer_is_refused() -> None:
    transport, _ = transport_for(status=404, content=b"nope")

    with pytest.raises(media_proxy.MediaRefused, match="HTTP 404"):
        media_proxy.fetch(URL, resolver=resolver_for(PUBLIC), transport=transport)


def test_an_engine_can_declare_its_own_hosts() -> None:
    """Brave, plus tard : le proxy accepte ce que l'appelant nomme, rien de plus."""
    transport, calls = transport_for()
    brave = "https://imgs.search.brave.com/x.png"

    with pytest.raises(media_proxy.MediaRefused, match="hôte hors liste"):
        media_proxy.fetch(brave, resolver=resolver_for(PUBLIC), transport=transport)

    thumb = media_proxy.fetch(
        brave,
        hosts=(*media_proxy.ALLOWED_HOSTS, "imgs.search.brave.com"),
        resolver=resolver_for(PUBLIC),
        transport=transport,
    )
    assert thumb.mime == "image/png"
    assert calls[-1].headers["host"] == "imgs.search.brave.com"


def test_an_ipv6_answer_is_bracketed_in_the_pinned_url() -> None:
    """Sans crochets, httpx lit « …:4700:… » comme un port et refuse l'URL."""
    transport, calls = transport_for()
    ipv6 = "2606:4700:10::6814:1101"

    thumb = media_proxy.fetch(URL, resolver=resolver_for(ipv6), transport=transport)

    assert thumb.mime == "image/png"
    assert calls[0].url.host == ipv6


def test_the_default_allow_list_is_the_measured_one() -> None:
    assert set(media_proxy.ALLOWED_HOSTS) == {
        "api.openverse.org",
        "thumb.wikimedia.org",
        "upload.wikimedia.org",
        "i.ytimg.com",
    }
    for host in media_proxy.ALLOWED_HOSTS:
        assert not host.startswith(".") and "*" not in host


def test_the_blocked_networks_still_cover_metadata() -> None:
    metadata = ipaddress.ip_address("169.254.169.254")
    assert any(metadata in network for network in media_proxy.BLOCKED_NETWORKS)
