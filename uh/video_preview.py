"""Une adresse YouTube devient une carte : titre, chaîne, miniature. Pas d'iframe.

Mesuré le 24/11 sur ``youtube.com/oembed`` : réponse en 0,06 s, sans clé, avec
``title``, ``author_name``, ``author_url``, ``thumbnail_url`` (sur
``i.ytimg.com``), ``provider_name`` et — inutile ici — un champ ``html`` qui
contient justement l'iframe que la page n'affichera **pas** (la CSP reste
``default-src 'none'``, le clic ouvre YouTube dans un onglet).

**Ni durée, ni description** : la description complète demande l'API YouTube
Data avec une clé. La carte ne l'invente pas et ne la promet pas ; elle dit ce
qu'elle a.

Ce module ne récupère que du JSON : il valide d'abord que l'adresse est bien une
adresse YouTube (hôte exact), parce que l'API oEmbed prend une URL en paramètre —
sans cette validation, on lui ferait récupérer n'importe quoi.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlsplit

import httpx

OEMBED_ENDPOINT = "https://www.youtube.com/oembed"
TIMEOUT = 8.0
USER_AGENT = "UH-Universal-Harness (+https://github.com/Antoine-Thomas/UH-Universal-Harness)"

#: Hôtes acceptés comme « une adresse YouTube » : correspondance exacte, jamais
#: un suffixe (`youtube.com.evil.example` ne passe pas).
VIDEO_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtube-nocookie.com"}
)
SHORT_HOSTS = frozenset({"youtu.be"})
WATCH_PREFIXES = ("/watch", "/shorts/", "/embed/", "/live/")


@dataclass(frozen=True)
class VideoCard:
    """Ce que la carte peut afficher, et ce qu'elle ne peut pas."""

    title: str
    channel: str
    channel_url: str
    thumb: str
    page: str
    description: str = ""
    #: Vrai quand une clé YouTube Data est configurée : la carte pourra alors
    #: ajouter la description. Sans clé, la carte l'annonce au lieu de se taire.
    has_description: bool = False


def is_video_url(raw: str) -> bool:
    """Vraie adresse YouTube (hôte exact, http(s), chemin reconnu)."""
    try:
        parts = urlsplit((raw or "").strip())
    except ValueError:
        return False
    if (parts.scheme or "").lower() not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if host not in VIDEO_HOSTS:
        return False
    if parts.username or parts.password:
        return False
    path = parts.path or "/"
    if host in SHORT_HOSTS:
        return len(path.strip("/")) > 0
    return path == "/watch" or path.startswith(WATCH_PREFIXES)


def page_url(raw: str) -> str:
    """L'adresse à ouvrir dans l'onglet, sans paramètres de suivi inutiles."""
    parts = urlsplit((raw or "").strip())
    host = (parts.hostname or "").lower()
    query = parts.query
    if host not in SHORT_HOSTS and query:
        kept = [pair for pair in query.split("&") if pair.split("=")[0] in ("v", "list", "t")]
        query = "&".join(kept)
    return parts._replace(scheme="https", query=query, fragment="").geturl()


def preview(
    raw: str, *, client: Optional[httpx.Client] = None, timeout: float = TIMEOUT
) -> tuple[Optional[VideoCard], str]:
    """La carte, ou ``(None, raison)``. Aucune exception réseau ne remonte."""
    if not is_video_url(raw):
        return None, "adresse refusée : ce n'est pas une adresse YouTube"
    target = page_url(raw)
    owned = client is None
    http = client or httpx.Client(
        timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=False
    )
    try:
        reply = http.get(OEMBED_ENDPOINT, params={"url": target, "format": "json"})
        if reply.status_code == 404:
            return None, "cette vidéo n'existe pas ou n'est pas publique"
        if reply.status_code != 200:
            return None, f"HTTP {reply.status_code}"
        body = reply.json()
    except (httpx.HTTPError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        if owned:
            http.close()

    if not isinstance(body, dict):
        return None, "réponse inattendue"
    title = str(body.get("title") or "").strip()
    if not title:
        return None, "réponse sans titre"
    return (
        VideoCard(
            title=title,
            channel=str(body.get("author_name") or "").strip(),
            channel_url=str(body.get("author_url") or "").strip(),
            thumb=str(body.get("thumbnail_url") or "").strip(),
            page=target,
            # La description viendra d'un autre appel, avec une clé : elle n'est
            # pas dans la réponse oEmbed, donc elle reste vide ici.
            description="",
            has_description=False,
        ),
        "",
    )


def oembed_call(raw: str) -> str:
    """L'URL interrogée, pour la journaliser (aucun secret : pas de clé)."""
    return f"{OEMBED_ENDPOINT}?url={quote(page_url(raw), safe='')}&format=json"


__all__ = [
    "OEMBED_ENDPOINT",
    "SHORT_HOSTS",
    "VIDEO_HOSTS",
    "VideoCard",
    "is_video_url",
    "oembed_call",
    "page_url",
    "preview",
]
