"""Un proxy de miniatures ne récupère que ce qu'il a décidé de récupérer.

Pourquoi un garde séparé de :mod:`uh.urlguard` : celui-ci décide sur le nom
**littéral** écrit dans l'URL, choix assumé pour une adresse MCP collée à la main
par l'opérateur. Un proxy qui va chercher des adresses *venues du web* ne peut
pas se contenter de lire le nom : il doit

1. n'accepter que ``https``, le port 443, et une liste d'hôtes connus ;
2. **résoudre** le nom, et refuser si une seule des adresses obtenues n'est pas
   publique (privée, loopback, lien-local, métadonnées cloud, réservée) ;
3. se connecter à **l'adresse résolue**, en gardant le nom pour TLS et pour
   l'en-tête ``Host`` — sinon le DNS de l'attaquant choisit la cible
   (*DNS rebinding*) ;
4. ne suivre aucune redirection : une adresse approuvée ne doit pas devenir une
   autre après un 302 ;
5. borner le corps (octets lus, coupés net au-delà) et décider du type par les
   **octets** (magic bytes), jamais par l'en-tête ``Content-Type`` ni par
   l'extension. Le SVG n'est pas reconnu : il est donc refusé.

La CSP de la page ne change pas : les octets passent par cette route, jamais par
une URL distante.

Ce que ce module ne fait pas (encore) : cache, limite de débit par session,
redirections revalidées. Ce sont des ajouts de la route, pas des permissions.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional
from urllib.parse import urlsplit

import httpx

from .images import MAX_BYTES, sniff_mime

#: Mesurés le 24/11 : ce sont les hôtes que le proxy a une raison de joindre.
ALLOWED_HOSTS = frozenset(
    {
        "api.openverse.org",  # vignettes Openverse (servies par Openverse elle-même)
        "thumb.wikimedia.org",  # vignettes Commons (`iiurlwidth`)
        "upload.wikimedia.org",  # fichiers Commons d'origine
        "i.ytimg.com",  # miniatures YouTube (oEmbed)
    }
)

#: Un proxy de vignettes n'a aucune raison d'accepter du clair.
ALLOWED_SCHEMES = ("https",)
ALLOWED_PORT = 443
TIMEOUT = 6.0
USER_AGENT = "UH-Universal-Harness (+https://github.com/Antoine-Thomas/UH-Universal-Harness)"

#: Réseaux refusés même si `ipaddress.is_global` les laissait passer un jour.
BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # dont 169.254.169.254 (métadonnées)
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)

Resolver = Callable[..., list]


class MediaRefused(RuntimeError):
    """L'image n'est pas récupérée. Le message est montrable à l'opérateur."""


@dataclass(frozen=True)
class Pinned:
    """Une URL acceptée, avec l'adresse sur laquelle la connexion doit partir."""

    host: str
    ip: str
    url: str  # https://<ip>/<chemin>?<requête> — le nom reste dans `Host`


@dataclass(frozen=True)
class Thumb:
    """Les octets, et le type que les octets déclarent."""

    mime: str
    data: bytes


def is_public(address: str) -> bool:
    """Vraie adresse publique : ni privée, ni locale, ni réservée, ni multicast.

    ``ipaddress`` seul ne suffit pas : mesuré en 3.10, ``224.0.0.1`` (multicast)
    répond ``is_global=True``. Chaque famille est donc refusée nommément avant
    de demander ``is_global``.
    """
    try:
        parsed = ipaddress.ip_address(address.strip("[]"))
    except ValueError:
        return False
    if any(parsed in network for network in BLOCKED_NETWORKS):
        return False
    if (
        parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_private
    ):
        return False
    return parsed.is_global


def resolve(host: str, *, resolver: Optional[Resolver] = None) -> List[str]:
    """Les adresses du nom, ou :class:`MediaRefused`. Aucun mélange public/privé.

    Refuser dès qu'**une** adresse n'est pas publique est le seul choix sûr : un
    resolver qui répond ``[93.184.215.14, 127.0.0.1]`` sert les deux, et la
    seconde réponse suffirait à viser la machine locale.
    """
    lookup = resolver or socket.getaddrinfo
    try:
        infos = lookup(host, ALLOWED_PORT, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise MediaRefused(f"nom introuvable : {host} ({exc})") from exc
    addresses: List[str] = []
    for info in infos or []:
        try:
            address = str(info[4][0])
        except (IndexError, TypeError):
            continue
        if address and address not in addresses:
            addresses.append(address)
    if not addresses:
        raise MediaRefused(f"nom sans adresse : {host}")
    refused = [address for address in addresses if not is_public(address)]
    if refused:
        raise MediaRefused(f"adresse non publique refusée : {host} -> {', '.join(refused[:3])}")
    return addresses


def pin(
    raw: str, *, hosts: Iterable[str] = ALLOWED_HOSTS, resolver: Optional[Resolver] = None
) -> Pinned:
    """Valide l'URL et choisit l'adresse de connexion. Lève :class:`MediaRefused`."""
    value = (raw or "").strip()
    if not value:
        raise MediaRefused("adresse vide")
    parts = urlsplit(value)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise MediaRefused(f"schéma refusé : {scheme or 'aucun'} (attendu https)")
    if parts.username or parts.password:
        raise MediaRefused("adresse refusée : elle contient un identifiant")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise MediaRefused("adresse refusée : aucun nom d'hôte")
    allowed = {item.lower() for item in hosts}
    if host not in allowed:
        raise MediaRefused(f"hôte hors liste : {host}")
    try:
        port = parts.port or ALLOWED_PORT
    except ValueError as exc:
        raise MediaRefused(f"port invalide : {exc}") from exc
    if port != ALLOWED_PORT:
        raise MediaRefused(f"port refusé : {port} (attendu {ALLOWED_PORT})")
    path = parts.path or "/"
    if not path.startswith("/"):
        raise MediaRefused("chemin invalide")
    addresses = resolve(host, resolver=resolver)
    query = f"?{parts.query}" if parts.query else ""
    # Une IPv6 se met entre crochets dans une URL ; sans cela httpx lit « ::… »
    # comme un port et refuse l'URL (mesuré sur 2606:4700:…).
    literal = f"[{addresses[0]}]" if ":" in addresses[0] else addresses[0]
    return Pinned(host=host, ip=addresses[0], url=f"https://{literal}{path}{query}")


def fetch(
    raw: str,
    *,
    hosts: Iterable[str] = ALLOWED_HOSTS,
    resolver: Optional[Resolver] = None,
    transport: Optional[httpx.BaseTransport] = None,
    max_bytes: int = MAX_BYTES,
    timeout: float = TIMEOUT,
) -> Thumb:
    """Récupère la vignette, ou lève :class:`MediaRefused`.

    ``transport`` et ``resolver`` existent pour les tests : le chemin par défaut
    est la connexion réseau réelle, épinglée sur l'adresse résolue.
    """
    pinned = pin(raw, hosts=hosts, resolver=resolver)
    request = httpx.Request(
        "GET",
        pinned.url,
        headers={
            # `Host` porte le nom : c'est ce que le serveur distant attend.
            "Host": pinned.host,
            "User-Agent": USER_AGENT,
            "Accept": "image/png,image/jpeg,image/webp,image/gif",
            "Accept-Encoding": "identity",
            # Un client par appel, et pas de connexion réutilisée : une poignée
            # gardée en vie contournerait la vérification de l'adresse.
            "Connection": "close",
        },
        # TLS vérifie le certificat contre CE nom, pas contre l'IP.
        extensions={"sni_hostname": pinned.host},
    )
    try:
        with httpx.Client(
            transport=transport or httpx.HTTPTransport(retries=0),
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            reply = client.send(request, stream=True)
            try:
                if reply.is_redirect:
                    location = reply.headers.get("location", "?")
                    raise MediaRefused(
                        f"redirection refusée : {reply.status_code} -> {location[:80]}"
                    )
                if reply.status_code != 200:
                    raise MediaRefused(f"réponse HTTP {reply.status_code}")
                declared = int(reply.headers.get("content-length") or 0)
                if declared > max_bytes:
                    raise MediaRefused(f"image trop lourde annoncée : {declared} octets")
                chunks: List[bytes] = []
                total = 0
                for chunk in reply.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise MediaRefused(f"image trop lourde : plus de {max_bytes} octets")
                    chunks.append(chunk)
            finally:
                reply.close()
    except httpx.HTTPError as exc:
        raise MediaRefused(f"hôte injoignable : {type(exc).__name__}: {exc}") from exc
    data = b"".join(chunks)
    mime = sniff_mime(data)
    if mime is None:
        # Le type se décide sur les octets : un SVG servi en image/png tombe ici.
        raise MediaRefused("format non reconnu (JPEG, PNG, WebP ou GIF attendu)")
    return Thumb(mime=mime, data=data)


__all__ = [
    "ALLOWED_HOSTS",
    "ALLOWED_PORT",
    "ALLOWED_SCHEMES",
    "BLOCKED_NETWORKS",
    "MediaRefused",
    "Pinned",
    "Thumb",
    "fetch",
    "is_public",
    "pin",
    "resolve",
]
