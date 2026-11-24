"""Images sous licence, sans clé : Openverse et Wikimedia Commons.

Tout ce qui est lu ici a été **mesuré** le 24/11 sur les vraies réponses :

* **Openverse** — ``GET https://api.openverse.org/v1/images/?q=…&language=fr`` :
  ``result_count``, puis par résultat ``title``, ``license`` (``by``,
  ``by-nc-nd``), ``license_version`` (``2.0``), ``creator``,
  ``foreign_landing_url``, ``provider`` (``flickr``) et ``thumbnail``, servie par
  Openverse elle-même (``api.openverse.org/v1/images/<id>/thumb/``). Répond en
  français avec ``language=fr`` (« cochon » → « le cochon »).
* **Wikimedia Commons** — ``action=query&generator=search…&prop=imageinfo`` :
  ``iiurlwidth`` donne ``thumburl`` (**sur ``thumb.wikimedia.org``**, pas
  ``upload``), plus ``descriptionurl``, ``mime`` et ``extmetadata`` :
  ``LicenseShortName`` (« CC BY-SA 3.0 »), ``Artist`` et ``Credit`` — ces deux
  derniers en **HTML**, à réduire en texte.

Aucune clé n'est demandée. Ce que la réponse ne donne pas n'est pas inventé :
sans licence, ``licence`` reste vide et l'interface écrit « licence inconnue ».
Le mot « libre de droits » n'est jamais produit ici — il n'est vrai que si
l'API le dit.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote_plus, urlsplit

import httpx

OPENVERSE_ENDPOINT = "https://api.openverse.org/v1/images/"
COMMONS_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
TIMEOUT = 12.0
USER_AGENT = "UH-Universal-Harness (+https://github.com/Antoine-Thomas/UH-Universal-Harness)"
MAX_LIMIT = 20
#: Formats que la page peut dessiner via le proxy ; le reste (SVG, TIFF, PDF) est
#: écarté à la source plutôt que refusé plus loin.
KEPT_MIME = ("image/jpeg", "image/png", "image/webp", "image/gif")

TAG = re.compile(r"<[^>]+>")
SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Hit:
    """Une image proposée : de quoi dessiner, créditer et ouvrir la source."""

    title: str
    thumb: str
    page: str
    domain: str
    licence: str
    author: str
    provider: str


@dataclass
class Result:
    """Ce qui est revenu, et ce qui a échoué — jamais l'un sans l'autre."""

    hits: List[Hit] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)
    engines: List[str] = field(default_factory=list)


def plain(value: object) -> str:
    """Du texte pour l'écran, jamais du HTML : balises retirées, entités lues."""
    raw = TAG.sub(" ", str(value or ""))
    return SPACES.sub(" ", html.unescape(raw)).strip()


def _domain(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _licence(licence: object, version: object) -> str:
    name = str(licence or "").strip()
    if not name:
        return ""
    suffix = str(version or "").strip()
    return f"{name} {suffix}".strip()


def openverse(
    query: str,
    *,
    limit: int = 8,
    language: str = "fr",
    client: Optional[httpx.Client] = None,
) -> Tuple[List[Hit], str]:
    """Les résultats Openverse, ou ``([], raison)``. Jamais d'exception réseau."""
    params: Dict[str, str | int] = {"q": query, "page_size": min(max(limit, 1), MAX_LIMIT)}
    if language:
        params["language"] = language
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        reply = http.get(OPENVERSE_ENDPOINT, params=params)
        if reply.status_code != 200:
            return [], f"HTTP {reply.status_code}"
        body = reply.json()
    except (httpx.HTTPError, ValueError) as exc:
        return [], f"{type(exc).__name__}: {exc}"
    finally:
        if owned:
            http.close()

    hits: List[Hit] = []
    for item in body.get("results") or []:
        if not isinstance(item, dict):
            continue
        thumb = str(item.get("thumbnail") or "").strip()
        page = str(item.get("foreign_landing_url") or "").strip()
        if not thumb or not page:
            continue
        hits.append(
            Hit(
                title=plain(item.get("title")) or "sans titre",
                thumb=thumb,
                page=page,
                domain=_domain(page),
                licence=_licence(item.get("license"), item.get("license_version")),
                author=plain(item.get("creator")),
                provider=plain(item.get("provider")) or "openverse",
            )
        )
    return hits[:limit], ""


def wikimedia(
    query: str,
    *,
    limit: int = 8,
    client: Optional[httpx.Client] = None,
) -> Tuple[List[Hit], str]:
    """Les fichiers de Commons qui sont des images, ou ``([], raison)``."""
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": "6",
        "gsrlimit": str(min(max(limit, 1), MAX_LIMIT)),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime",
        "iiurlwidth": "320",
        "format": "json",
        "formatversion": "2",
    }
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        reply = http.get(COMMONS_ENDPOINT, params=params)
        if reply.status_code != 200:
            return [], f"HTTP {reply.status_code}"
        body = reply.json()
    except (httpx.HTTPError, ValueError) as exc:
        return [], f"{type(exc).__name__}: {exc}"
    finally:
        if owned:
            http.close()

    hits: List[Hit] = []
    pages = (body.get("query") or {}).get("pages") or []
    for page in pages:
        if not isinstance(page, dict):
            continue
        infos = page.get("imageinfo") or []
        info = infos[0] if infos and isinstance(infos[0], dict) else {}
        if str(info.get("mime") or "") not in KEPT_MIME:
            continue
        thumb = str(info.get("thumburl") or "").strip()
        target = str(info.get("descriptionurl") or "").strip()
        if not thumb or not target:
            continue
        meta = info.get("extmetadata") or {}
        licence = plain((meta.get("LicenseShortName") or {}).get("value"))
        author = plain((meta.get("Artist") or {}).get("value")) or plain(
            (meta.get("Credit") or {}).get("value")
        )
        title = str(page.get("title") or "").removeprefix("File:").strip()
        hits.append(
            Hit(
                title=plain(title) or "sans titre",
                thumb=thumb,
                page=target,
                domain=_domain(target),
                licence=licence,
                author=author,
                provider="commons",
            )
        )
    return hits[:limit], ""


#: Les moteurs sans clé, dans l'ordre où ils sont interrogés.
ENGINES: Sequence[Tuple[str, Callable[..., Tuple[List[Hit], str]]]] = (
    ("openverse", openverse),
    ("wikimedia", wikimedia),
)


def search_images(
    query: str,
    *,
    limit: int = 8,
    language: str = "fr",
    client: Optional[httpx.Client] = None,
    engines: Optional[Sequence[str]] = None,
) -> Result:
    """Interroge les moteurs sans clé, s'arrête au premier qui remplit la demande."""
    wanted = set(engines) if engines else {name for name, _ in ENGINES}
    out = Result()
    term = (query or "").strip()
    if not term:
        out.failed["query"] = "requête vide"
        return out
    for name, function in ENGINES:
        if name not in wanted or len(out.hits) >= limit:
            continue
        try:
            if name == "openverse":
                hits, reason = function(term, limit=limit, language=language, client=client)
            else:
                hits, reason = function(term, limit=limit, client=client)
        except Exception as exc:  # noqa: BLE001 - un moteur ne casse pas la recherche
            hits, reason = [], f"{type(exc).__name__}: {exc}"
        if reason:
            out.failed[name] = reason
            continue
        out.engines.append(name)
        out.hits.extend(hits)
    return Result(hits=out.hits[:limit], failed=out.failed, engines=out.engines)


def openverse_url(query: str, *, limit: int = 8, language: str = "fr") -> str:
    """L'URL interrogée, pour la journaliser sans secret (aucune clé ici)."""
    params = f"?q={quote_plus(query)}&page_size={min(max(limit, 1), MAX_LIMIT)}"
    return OPENVERSE_ENDPOINT + params + (f"&language={language}" if language else "")


__all__ = [
    "COMMONS_ENDPOINT",
    "ENGINES",
    "KEPT_MIME",
    "OPENVERSE_ENDPOINT",
    "Hit",
    "Result",
    "openverse",
    "openverse_url",
    "plain",
    "search_images",
    "wikimedia",
]
