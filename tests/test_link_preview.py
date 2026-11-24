"""G8 : un aperçu de lien lit cinq champs, et refuse tout le reste.

Ces tests ne sortent pas sur le réseau : la résolution et le transport sont
injectés, comme pour le proxy de vignettes. Chaque refus est donc observé, pas
supposé — et le témoin de chaque refus est le fait qu'aucune requête n'est
partie vers l'adresse visée.

Ce qui est prouvé ici :

* une adresse non publique (privée, loopback, lien-local, métadonnées cloud,
  mélange public/privé) est refusée **avant** toute connexion ;
* une redirection vers une adresse privée est refusée, une redirection publique
  est suivie, et une chaîne trop longue est refusée ;
* une page trop lourde est refusée sur l'en-tête **et** pendant la lecture ;
* ``robots.txt`` interdit une page, et son absence autorise en le disant ;
* la requête ne porte ni cookie ni ``Referer``, et un User-Agent identifiable ;
* l'extraction ne rend que ``title``, ``description``, ``image``, ``icon`` :
  jamais le corps, jamais de HTML, les entités sont décodées une seule fois.
"""

from __future__ import annotations

import gzip
import os
import shutil
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
import pytest

from uh import link_preview

PUBLIC = "93.184.215.14"
OTHER = "93.184.215.15"
HOST = "example.com"

Route = Tuple[int, Dict[str, str], bytes]


def resolver_for(*addresses: str):
    """Un resolver qui répond toujours les mêmes adresses."""

    def lookup(host: str, port: int, **kwargs: object) -> List[tuple]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses
        ]

    return lookup


class Recorder:
    """Le transport factice, et la trace de ce qu'il a vraiment reçu."""

    def __init__(self, routes: Dict[Tuple[str, str], Route], default: Optional[Route] = None):
        self.routes = routes
        self.default = default or (404, {"content-type": "text/html"}, b"<html>404</html>")
        self.seen: List[Tuple[str, str]] = []
        self.headers: List[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", "")
        path = request.url.path
        self.seen.append((host, path))
        self.headers.append(request.headers)
        status, headers, body = self.routes.get((host, path), self.default)
        return httpx.Response(status, headers=headers, content=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def html(head: str, body: str = "<p>corps de la page</p>") -> bytes:
    return f"<!doctype html><html><head>{head}</head><body>{body}</body></html>".encode("utf-8")


def ok(head: str, body: str = "<p>corps de la page</p>") -> Route:
    return (200, {"content-type": "text/html; charset=utf-8"}, html(head, body))


@pytest.fixture(autouse=True)
def fresh_robots() -> None:
    link_preview.clear_robots_cache()


def preview(raw: str, recorder: Recorder, **kwargs: object):
    return link_preview.preview(
        raw,
        resolver=resolver_for(PUBLIC),
        transport=recorder.transport,
        timeout=2.0,
        **kwargs,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# l'adresse d'abord
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "address, expected",
    [
        ("10.0.0.5", "non publique"),
        ("127.0.0.1", "non publique"),
        ("169.254.169.254", "non publique"),  # métadonnées cloud
        ("192.168.1.10", "non publique"),
        ("::1", "non publique"),
    ],
)
def test_a_non_public_address_is_refused_before_any_request(address: str, expected: str) -> None:
    recorder = Recorder({(HOST, "/"): ok("<title>x</title>")})

    with pytest.raises(link_preview.PreviewRefused) as caught:
        link_preview.preview(
            f"http://{HOST}/", resolver=resolver_for(address), transport=recorder.transport
        )

    assert expected in str(caught.value)
    assert recorder.seen == [], "aucune requête ne doit partir vers une adresse privée"


def test_one_private_answer_is_enough_to_refuse() -> None:
    """Un resolver qui rend public **et** privé sert les deux : on refuse."""
    recorder = Recorder({(HOST, "/"): ok("<title>x</title>")})

    with pytest.raises(link_preview.PreviewRefused) as caught:
        link_preview.preview(
            f"http://{HOST}/",
            resolver=resolver_for(PUBLIC, "127.0.0.1"),
            transport=recorder.transport,
        )

    assert "non publique" in str(caught.value)
    assert recorder.seen == []


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/fichier",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://user:motdepasse@example.com/",
        "http://example.com:8080/",
        "https://example.com:8443/",
    ],
)
def test_a_scheme_a_credential_or_an_odd_port_is_refused(url: str) -> None:
    recorder = Recorder({})

    with pytest.raises(link_preview.PreviewRefused):
        link_preview.preview(url, resolver=resolver_for(PUBLIC), transport=recorder.transport)

    assert recorder.seen == []


# --------------------------------------------------------------------------- #
# redirections revalidées
# --------------------------------------------------------------------------- #
def test_a_redirect_towards_a_private_address_is_refused() -> None:
    """Un 302 ne doit pas devenir une permission : la cible est revalidée."""
    recorder = Recorder(
        {
            (HOST, "/"): (
                302,
                {"location": "http://127.0.0.1/secret", "content-type": "text/html"},
                b"",
            ),
        }
    )
    recorder.routes[(HOST, "/robots.txt")] = (404, {}, b"")

    def resolver(host: str, port: int, **kwargs: object) -> List[tuple]:
        address = "127.0.0.1" if host == "127.0.0.1" else PUBLIC
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    with pytest.raises(link_preview.PreviewRefused) as caught:
        link_preview.preview(f"http://{HOST}/", resolver=resolver, transport=recorder.transport)

    assert "non publique" in str(caught.value)
    assert ("127.0.0.1", "/secret") not in recorder.seen


def test_a_public_redirect_is_followed_once() -> None:
    recorder = Recorder(
        {
            (HOST, "/"): (
                301,
                {"location": "https://example.com/arrivee", "content-type": "text/html"},
                b"",
            ),
            (HOST, "/arrivee"): ok("<title>Arrivée</title>"),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    page = preview(f"http://{HOST}/", recorder)

    assert page.final_url == "https://example.com/arrivee"
    assert page.redirects == 1
    assert page.title == "Arrivée"


def test_a_redirect_loop_is_refused() -> None:
    recorder = Recorder(
        {
            (HOST, "/"): (302, {"location": "/b", "content-type": "text/html"}, b""),
            (HOST, "/b"): (302, {"location": "/c", "content-type": "text/html"}, b""),
            (HOST, "/c"): (302, {"location": "/d", "content-type": "text/html"}, b""),
            (HOST, "/d"): (302, {"location": "/e", "content-type": "text/html"}, b""),
            (HOST, "/e"): (302, {"location": "/f", "content-type": "text/html"}, b""),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    with pytest.raises(link_preview.PreviewRefused) as caught:
        preview(f"http://{HOST}/", recorder)

    assert "redirections" in str(caught.value)
    assert len(recorder.seen) <= link_preview.MAX_REDIRECTS + 2


# --------------------------------------------------------------------------- #
# taille et type
# --------------------------------------------------------------------------- #
def test_a_page_that_announces_too_much_is_refused() -> None:
    recorder = Recorder(
        {
            (HOST, "/"): (200, {"content-type": "text/html", "content-length": "50000000"}, b""),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    with pytest.raises(link_preview.PreviewRefused) as caught:
        preview(f"http://{HOST}/", recorder)

    assert "trop lourde" in str(caught.value)


def test_a_page_too_large_while_streaming_is_refused() -> None:
    """Le plafond de lecture doit tenir même quand l'en-tête ment.

    ``content-length`` annonce 10 octets et le corps en pèse 4 000 : sans cette
    ruse, le refus viendrait du seul en-tête et le plafond de lecture ne serait
    jamais exercé — la contre-épreuve l'a montré (le test passait encore une
    fois le garde de lecture retiré).
    """
    body = html("<title>grosse</title>", "<p>" + ("x" * 4000) + "</p>")
    recorder = Recorder(
        {
            (HOST, "/"): (200, {"content-type": "text/html", "content-length": "10"}, body),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    with pytest.raises(link_preview.PreviewRefused) as caught:
        preview(f"http://{HOST}/", recorder, max_bytes=2048)

    assert "trop lourde" in str(caught.value)


def test_a_non_html_answer_is_refused() -> None:
    recorder = Recorder(
        {
            (HOST, "/"): (200, {"content-type": "application/pdf"}, b"%PDF-1.7"),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    with pytest.raises(link_preview.PreviewRefused) as caught:
        preview(f"http://{HOST}/", recorder)

    assert "type refusé" in str(caught.value)


# --------------------------------------------------------------------------- #
# robots.txt
# --------------------------------------------------------------------------- #
def test_robots_txt_can_forbid_a_page() -> None:
    recorder = Recorder(
        {
            (HOST, "/robots.txt"): (
                200,
                {"content-type": "text/plain"},
                b"User-agent: *\nDisallow: /prive\n",
            ),
            (HOST, "/prive/page"): ok("<title>interdit</title>"),
        }
    )

    with pytest.raises(link_preview.PreviewRefused) as caught:
        preview(f"http://{HOST}/prive/page", recorder)

    assert "robots.txt interdit" in str(caught.value)
    assert (HOST, "/prive/page") not in recorder.seen, "la page n'est même pas demandée"


def test_robots_txt_of_another_group_does_not_bind_us() -> None:
    """Un ``Disallow`` sous ``User-agent: Googlebot`` ne nous concerne pas."""
    recorder = Recorder(
        {
            (HOST, "/robots.txt"): (
                200,
                {"content-type": "text/plain"},
                b"User-agent: Googlebot\nDisallow: /\n\nUser-agent: *\nDisallow:\n",
            ),
            (HOST, "/"): ok("<title>autorise</title>"),
        }
    )

    page = preview(f"http://{HOST}/", recorder)

    assert page.title == "autorise"
    assert page.robots == "allowed"


def test_a_missing_robots_txt_allows_and_says_so() -> None:
    recorder = Recorder(
        {(HOST, "/robots.txt"): (404, {}, b""), (HOST, "/"): ok("<title>ok</title>")}
    )

    page = preview(f"http://{HOST}/", recorder)

    assert page.robots == "absent"
    assert any("robots.txt absent (4xx)" in note for note in page.notes)


# --------------------------------------------------------------------------- #
# ce qui part sur le réseau
# --------------------------------------------------------------------------- #
def test_the_request_carries_no_cookie_and_no_referer() -> None:
    recorder = Recorder(
        {(HOST, "/"): ok("<title>ok</title>"), (HOST, "/robots.txt"): (404, {}, b"")}
    )

    preview(f"http://{HOST}/", recorder)

    for headers in recorder.headers:
        assert "cookie" not in {key.lower() for key in headers.keys()}
        assert "referer" not in {key.lower() for key in headers.keys()}
        assert headers.get("user-agent") == link_preview.USER_AGENT
        assert headers.get("host") == HOST


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #
def test_only_the_five_fields_are_returned() -> None:
    recorder = Recorder(
        {
            (HOST, "/"): ok(
                '<meta property="og:title" content="Titre OG">'
                '<meta property="og:description" content="Une description.">'
                '<meta property="og:image" content="/img/apercu.png">'
                '<link rel="icon" href="/favicon.ico">'
                "<title>Titre de la balise</title>"
            ),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    page = preview(f"http://{HOST}/", recorder)

    assert page.title == "Titre OG"
    assert page.description == "Une description."
    assert page.image == "http://example.com/img/apercu.png"
    assert page.icon == "http://example.com/favicon.ico"
    assert set(link_preview.Page.__dataclass_fields__) == {
        "url",
        "final_url",
        "host",
        "title",
        "description",
        "image",
        "icon",
        "redirects",
        "bytes_read",
        "robots",
        "notes",
    }


def test_the_body_never_leaves_the_module() -> None:
    secret = "CECI-EST-LE-CORPS-DE-LA-PAGE"
    recorder = Recorder(
        {
            (HOST, "/"): ok("<title>ok</title>", f"<p>{secret}</p>"),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    page = preview(f"http://{HOST}/", recorder)

    for value in vars(page).values():
        assert secret not in str(value)


def test_a_title_containing_markup_is_returned_as_decoded_text() -> None:
    """Le serveur rend du **texte** ; c'est la page qui le pose en `textContent`.

    Une balise écrite en clair dans un titre est une donnée, pas du HTML : la
    décoder une fois et la garder telle quelle est le comportement juste. La
    garantie « jamais interprété » se vérifie dans le navigateur, et le test e2e
    de la carte l'épingle (`textContent`, aucun élément `<script>` créé).
    """
    recorder = Recorder(
        {
            (HOST, "/"): ok("<title>&lt;script&gt;alert(1)&lt;/script&gt;Salut</title>"),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    page = preview(f"http://{HOST}/", recorder)

    assert page.title == "<script>alert(1)</script>Salut", "décodé une fois, rien d'autre"
    assert "&lt;" not in page.title, "les entités sont décodées, pas recopiées"
    assert page.title.startswith("<script>"), "les chevrons restent du texte, pas une balise"


def test_entities_are_decoded_once() -> None:
    recorder = Recorder(
        {
            (HOST, "/"): ok('<meta property="og:title" content="Tom &amp; Jerry">'),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    page = preview(f"http://{HOST}/", recorder)

    assert page.title == "Tom & Jerry"


def test_a_long_description_is_cut() -> None:
    long_text = "a" * 2000
    recorder = Recorder(
        {
            (HOST, "/"): ok(f'<meta property="og:description" content="{long_text}">'),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    page = preview(f"http://{HOST}/", recorder)

    assert len(page.description) == link_preview.DESCRIPTION_MAX


def test_an_https_page_on_port_443_is_accepted() -> None:
    recorder = Recorder(
        {(HOST, "/"): ok("<title>ok</title>"), (HOST, "/robots.txt"): (404, {}, b"")}
    )

    page = preview(f"https://{HOST}/", recorder)

    assert page.title == "ok"
    assert page.host == HOST


# --------------------------------------------------------------------------- #
# RFC 9309 : 4xx autorise, 5xx et injoignable interdisent
# --------------------------------------------------------------------------- #
def test_a_server_error_on_robots_txt_forbids_everything() -> None:
    """RFC 9309 §2.3.1.4 : « MUST assume complete disallow » sur 5xx."""
    recorder = Recorder(
        {
            (HOST, "/robots.txt"): (503, {"content-type": "text/plain"}, b"maintenance"),
            (HOST, "/"): ok("<title>jamais lu</title>"),
        }
    )

    with pytest.raises(link_preview.PreviewRefused) as caught:
        preview(f"http://{HOST}/", recorder)

    assert "disallow complet" in str(caught.value)
    assert (HOST, "/") not in recorder.seen, "la page n'est pas demandée du tout"


def test_an_unreachable_robots_txt_forbids_everything() -> None:
    """Une erreur réseau n'est pas une absence : elle vaut interdiction."""

    class Failing(Recorder):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                raise httpx.ConnectError("réseau injoignable", request=request)
            return super().__call__(request)

    recorder = Failing({(HOST, "/"): ok("<title>jamais lu</title>")})

    with pytest.raises(link_preview.PreviewRefused) as caught:
        preview(f"http://{HOST}/", recorder)

    assert "robots.txt injoignable" in str(caught.value)
    assert (HOST, "/") not in recorder.seen, "la page n'est pas demandée du tout"


# --------------------------------------------------------------------------- #
# adresses pièges et redirections de port
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "address",
    [
        "::ffff:127.0.0.1",  # IPv4 encapsulée dans IPv6
        "::ffff:169.254.169.254",
        "0.0.0.0",
        "100.64.1.1",  # 100.64.0.0/10, réseau opérateur
        "fc00::1",  # fc00::/7, adresse locale unique
        "fe80::1",  # lien-local
        "224.0.0.1",  # multicast
        "239.1.2.3",
        "::",
    ],
)
def test_every_trap_form_of_address_is_refused(address: str) -> None:
    recorder = Recorder({(HOST, "/"): ok("<title>x</title>")})

    with pytest.raises(link_preview.PreviewRefused) as caught:
        link_preview.preview(
            f"http://{HOST}/", resolver=resolver_for(address), transport=recorder.transport
        )

    assert "non publique" in str(caught.value)
    assert recorder.seen == []


def test_a_redirect_to_another_port_is_refused() -> None:
    recorder = Recorder(
        {
            (HOST, "/"): (
                302,
                {"location": "http://example.com:8080/admin", "content-type": "text/html"},
                b"",
            ),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    with pytest.raises(link_preview.PreviewRefused) as caught:
        preview(f"http://{HOST}/", recorder)

    assert "port refusé" in str(caught.value)
    assert (HOST, "/admin") not in recorder.seen


def test_a_redirect_from_https_to_http_is_refused() -> None:
    """Un site chiffré qui renvoie vers du clair rend la page lisible en chemin."""
    recorder = Recorder(
        {
            (HOST, "/"): (
                301,
                {"location": "http://example.com/clair", "content-type": "text/html"},
                b"",
            ),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    with pytest.raises(link_preview.PreviewRefused) as caught:
        preview(f"https://{HOST}/", recorder)

    assert "https -> http" in str(caught.value)
    assert (HOST, "/clair") not in recorder.seen


# --------------------------------------------------------------------------- #
# taille après décompression
# --------------------------------------------------------------------------- #
def test_a_gzip_body_that_grows_past_the_cap_is_refused() -> None:
    """Le plafond porte sur les octets **décompressés**, pas sur ceux du fil.

    On demande ``identity`` ; un serveur peut l'ignorer. Quelques kilo-octets de
    gzip valent ici plusieurs centaines de kilo-octets une fois décodés, et c'est
    le décodage qui compte : ``iter_bytes`` rend le corps décodé, ``iter_raw``
    l'aurait laissé compressé.
    """
    big = html("<title>gonfle</title>", "<p>" + ("a" * 700_000) + "</p>")
    compressed = gzip.compress(big)
    assert len(compressed) < link_preview.MAX_BYTES, "le corps compressé passe sous le plafond"

    recorder = Recorder(
        {
            (HOST, "/"): (
                200,
                {"content-type": "text/html", "content-encoding": "gzip"},
                compressed,
            ),
            (HOST, "/robots.txt"): (404, {}, b""),
        }
    )

    with pytest.raises(link_preview.PreviewRefused) as caught:
        preview(f"http://{HOST}/", recorder)

    assert "trop lourde" in str(caught.value)


# --------------------------------------------------------------------------- #
# délai total
# --------------------------------------------------------------------------- #
class _Trickle(httpx.SyncByteStream):
    """Un corps qui arrive morceau par morceau, sans jamais dépasser un délai."""

    def __init__(self, chunks: int = 40, pause: float = 0.05) -> None:
        self.chunks = chunks
        self.pause = pause

    def __iter__(self):
        for _ in range(self.chunks):
            time.sleep(self.pause)
            yield b"<p>" + b"x" * 64


def test_the_total_deadline_stops_a_slow_trickle() -> None:
    """50 ms par morceau : aucun délai par requête ne voit passer ce serveur."""

    class SlowTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/html"}, stream=_Trickle())

    with pytest.raises(link_preview.PreviewRefused) as caught:
        link_preview.preview(
            f"http://{HOST}/",
            resolver=resolver_for(PUBLIC),
            transport=SlowTransport(),
            check_robots=False,
            timeout=5.0,
            deadline=0.3,
        )

    assert "delai total depasse" in str(caught.value).replace("é", "e")


# --------------------------------------------------------------------------- #
# TLS : l'épinglage d'IP ne désactive pas la vérification
# --------------------------------------------------------------------------- #
def _make_cert(folder: Path, name: str) -> "tuple[Path, Path]":
    key = folder / f"{name}.key"
    cert = folder / f"{name}.crt"
    command = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key),
        "-out",
        str(cert),
        "-days",
        "1",
        "-subj",
        f"/CN={name}",
        "-addext",
        f"subjectAltName=DNS:{name}",
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=60)  # noqa: S603
    return cert, key


class _TlsPage(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # noqa: A003 - http.server's shape
        return

    def do_GET(self) -> None:  # noqa: N802 - http.server's shape
        body = b"<!doctype html><html><head><title>via TLS</title></head><body></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _ca(cert: Path) -> ssl.SSLContext:
    """Un contexte qui ne fait confiance qu'à ce certificat-là (pas au magasin système)."""
    return ssl.create_default_context(cafile=str(cert))


_SERVERS: Dict[int, ThreadingHTTPServer] = {}


def _start_tls(cert: Path, key: Path) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TlsPage)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = int(server.server_address[1])
    _SERVERS[port] = server
    return port


def _stop_tls(port: int) -> None:
    server = _SERVERS.pop(port, None)
    if server is not None:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl absent : pas de certificat local"
)
def test_the_certificate_is_verified_against_the_name(tmp_path: Path) -> None:
    """Une IP épinglée avec SNI garde la vérification du certificat.

    Le serveur local présente un certificat pour ``example.com`` ; on s'y connecte
    par ``127.0.0.1`` en gardant ``example.com`` en SNI et en ``Host``. Accepté
    avec le bon certificat, refusé avec un certificat pour un autre nom : c'est
    ce qui prouve que la vérification n'a pas été sacrifiée à l'épinglage.
    """
    good_cert, good_key = _make_cert(tmp_path, "example.com")
    bad_cert, bad_key = _make_cert(tmp_path, "autre.invalid")
    port = _start_tls(good_cert, good_key)
    wrong_port = _start_tls(bad_cert, bad_key)
    try:
        pinned = link_preview.Pinned(
            host="example.com",
            ip="127.0.0.1",
            scheme="https",
            port=443,
            url=f"https://127.0.0.1:{port}/",
            host_header="example.com",
        )
        reply = link_preview._request_once(  # noqa: SLF001 - test du niveau TLS
            pinned,
            transport=httpx.HTTPTransport(verify=_ca(good_cert)),
            timeout=5.0,
            stop=time.monotonic() + 10,
        )
        body = link_preview._read_capped(  # noqa: SLF001
            reply, link_preview.MAX_BYTES, stop=time.monotonic() + 10
        )
        reply.close()
        assert b"via TLS" in body, "le bon certificat pour le nom passe"

        wrong = link_preview.Pinned(
            host="example.com",
            ip="127.0.0.1",
            scheme="https",
            port=443,
            url=f"https://127.0.0.1:{wrong_port}/",
            host_header="example.com",
        )
        with pytest.raises(link_preview.PreviewRefused) as caught:
            link_preview._request_once(  # noqa: SLF001
                wrong,
                transport=httpx.HTTPTransport(verify=_ca(bad_cert)),
                timeout=5.0,
                stop=time.monotonic() + 10,
            )
        message = str(caught.value).lower()
        assert "certificate" in message, str(caught.value)
    finally:
        _stop_tls(port)
        _stop_tls(wrong_port)


def test_the_transport_never_disables_certificate_verification() -> None:
    """Le garde-fou de source : épingler une IP ne doit jamais coûter le TLS.

    C'est la tentation classique — « la vérification échoue parce qu'on se
    connecte à une IP, désactivons-la ». Ce test échoue le jour où quelqu'un
    l'écrit, y compris dans une branche que les tests locaux n'atteignent pas.
    """
    source = (Path(__file__).resolve().parents[1] / "uh" / "link_preview.py").read_text(
        encoding="utf-8"
    )

    assert "verify=False" not in source
    assert "CERT_NONE" not in source
    assert "check_hostname = False" not in source


# --------------------------------------------------------------------------- #
# le seul passage qui sort vraiment : à la main, hors CI
# --------------------------------------------------------------------------- #
network = pytest.mark.skipif(
    os.environ.get("UH_NETWORK_TESTS") != "1",
    reason="reseau reel : UH_NETWORK_TESTS=1 pour l'executer",
)


@network
@pytest.mark.network
def test_a_real_site_answers_with_its_name_pinned() -> None:
    """Un vrai site, un vrai certificat, une IP épinglée."""
    page = link_preview.preview("https://example.com/", timeout=10.0, deadline=20.0)

    assert page.host == "example.com"
    assert page.title, "un titre doit arriver"
    assert page.robots in {"allowed", "absent"}


@network
@pytest.mark.network
def test_a_real_wrong_name_never_yields_a_page() -> None:
    """Même site, mauvais nom : la connexion ne rend jamais de page.

    Sur un vrai CDN, le refus arrive souvent **avant** le certificat : le serveur
    ne connaît pas ce nom et coupe la poignée de main (mesuré : « handshake
    failure »). La preuve que c'est bien la *vérification du certificat* qui
    refuse — et non le serveur — est le test local à certificat délibérément
    faux ; celui-ci vérifie que, dans la vraie vie, le mauvais nom ne passe pas
    non plus.
    """
    # L'adresse est résolue maintenant : une IP écrite en dur vieillit, et c'est
    # un délai de connexion qu'on récolterait au lieu d'un refus de certificat.
    address = link_preview.resolve("example.com", 443)[0]
    pinned = link_preview.Pinned(
        host="wrong.invalid",
        ip=address,
        scheme="https",
        port=443,
        url=f"https://{link_preview._literal(address)}/",  # noqa: SLF001
        host_header="wrong.invalid",
    )

    with pytest.raises(link_preview.PreviewRefused) as caught:
        link_preview._request_once(  # noqa: SLF001
            pinned, transport=None, timeout=10.0, stop=time.monotonic() + 20
        )

    message = str(caught.value).lower()
    assert "certificate" in message or "handshake" in message, str(caught.value)
