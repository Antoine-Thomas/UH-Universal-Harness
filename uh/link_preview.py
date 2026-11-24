"""Un aperçu de lien ne lit que l'en-tête d'une page, et jamais son corps.

Pourquoi un garde de plus, à côté de :mod:`uh.urlguard` et de
:mod:`uh.media_proxy` : une URL d'aperçu peut venir de **n'importe où** — un
résultat de recherche, un message, un site. Les règles sont donc différentes de
celles des vignettes :

* ``http`` et ``https``, ports **80 et 443** seulement (un aperçu doit marcher
  sur un site ancien, une vignette n'en a pas besoin) ;
* aucune liste d'hôtes : le web entier, mais **résolu** — et refusé si une seule
  adresse obtenue n'est pas publique (privée, loopback, lien-local, métadonnées
  cloud, réservée) ;
* connexion sur l'**adresse épinglée**, le nom restant dans ``Host`` et dans le
  SNI : sans cela, un DNS malin choisit la cible (*DNS rebinding*) ;
* redirections **suivies mais revalidées** une par une, bornées en nombre : un
  302 vers ``127.0.0.1`` est refusé, un 301 vers ``https://`` ne l'est pas ;
* corps plafonné à 512 Ko, ``robots.txt`` respecté, aucun cookie, aucun
  ``Referer``, un User-Agent identifiable ;
* extraction **strictement bornée** : ``<title>``, ``og:title``,
  ``og:description``, ``og:image``, ``link rel=icon``. Le corps de la page n'est
  jamais renvoyé, et rien de ce qui sort d'ici n'est du HTML : ce sont des
  chaînes, que la page posera en ``textContent``.

Le module ne décide pas *quand* récupérer : c'est la règle de la page (aperçu
automatique pour une URL d'outil ou collée par l'opérateur, bouton « Aperçu »
pour une URL écrite par le modèle).
"""

from __future__ import annotations

import html
import re
import socket
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import httpx

from ._compat import tomllib
from .config import uh_home
from .images import sniff_mime
from .media_proxy import MediaRefused, is_public

#: Un aperçu lit une page publique, en clair ou chiffré. Rien d'autre.
ALLOWED_SCHEMES = ("http", "https")
DEFAULT_PORTS = {"http": 80, "https": 443}

#: 512 Ko : assez pour un ``<head>`` de site réel, trop peu pour un film.
MAX_BYTES = 512 * 1024
#: Un 301 http->https, un slash final, un www : trois sauts couvrent le réel.
MAX_REDIRECTS = 3
#: Par requête : délai de connexion, puis délai de lecture.
TIMEOUT = 6.0
#: Pour l'aperçu entier — robots.txt, redirections et corps compris. Un serveur
#: qui répond un octet toutes les cinq secondes ne dépasse aucun des délais
#: ci-dessus ; il ne doit pas pour autant tenir la page ouverte indéfiniment.
TOTAL_DEADLINE = 15.0
USER_AGENT = "UH-Universal-Harness (+https://github.com/Antoine-Thomas/UH-Universal-Harness)"

TITLE_MAX = 200
DESCRIPTION_MAX = 400
URL_MAX = 2000

#: ``robots.txt`` ne change pas toutes les minutes ; une heure suffit et évite
#: une requête par aperçu.
ROBOTS_TTL = 3600.0

Resolver = Callable[..., list]


class PreviewRefused(MediaRefused):
    """L'aperçu n'est pas récupéré. Le message est montrable à l'opérateur."""


@dataclass(frozen=True)
class Pinned:
    """Une URL acceptée, avec l'adresse sur laquelle la connexion doit partir."""

    host: str
    ip: str
    scheme: str
    port: int
    url: str  # <scheme>://<ip>[:port]/<chemin>?<requête>
    host_header: str  # le nom, éventuellement avec un port non standard


@dataclass(frozen=True)
class Page:
    """Ce qu'un aperçu a le droit de rapporter."""

    url: str
    final_url: str
    host: str
    title: str = ""
    description: str = ""
    image: str = ""
    icon: str = ""
    redirects: int = 0
    bytes_read: int = 0
    robots: str = "absent"  # absent | allowed | disallowed
    notes: Tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# adresses
# --------------------------------------------------------------------------- #
def resolve(host: str, port: int, *, resolver: Optional[Resolver] = None) -> List[str]:
    """Les adresses publiques du nom, ou :class:`PreviewRefused`.

    Refuser dès qu'**une** réponse n'est pas publique : un resolver qui rend
    ``[93.184.215.14, 127.0.0.1]`` sert les deux, et la seconde suffirait.
    """
    lookup = resolver or socket.getaddrinfo
    try:
        infos = lookup(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise PreviewRefused(f"nom introuvable : {host} ({exc})") from exc
    addresses: List[str] = []
    for info in infos or []:
        try:
            address = str(info[4][0])
        except (IndexError, TypeError):
            continue
        if address and address not in addresses:
            addresses.append(address)
    if not addresses:
        raise PreviewRefused(f"nom sans adresse : {host}")
    refused = [address for address in addresses if not is_public(address)]
    if refused:
        raise PreviewRefused(f"adresse non publique refusée : {host} -> {', '.join(refused[:3])}")
    return addresses


def _literal(address: str) -> str:
    """Une IPv6 se met entre crochets dans une URL (sinon httpx lit « :: » comme un port)."""
    return f"[{address}]" if ":" in address else address


def pin(raw: str, *, resolver: Optional[Resolver] = None) -> Pinned:
    """Valide l'URL et choisit l'adresse de connexion. Lève :class:`PreviewRefused`."""
    value = (raw or "").strip()
    if not value:
        raise PreviewRefused("adresse vide")
    parts = urlsplit(value)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise PreviewRefused(f"schéma refusé : {scheme or 'aucun'} (attendu http ou https)")
    if parts.username or parts.password:
        raise PreviewRefused("adresse refusée : elle contient un identifiant")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise PreviewRefused("adresse refusée : aucun nom d'hôte")
    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise PreviewRefused(f"port invalide : {exc}") from exc
    if port != DEFAULT_PORTS[scheme]:
        raise PreviewRefused(
            f"port refusé : {port} (attendu {DEFAULT_PORTS[scheme]} pour {scheme})"
        )
    path = parts.path or "/"
    if not path.startswith("/"):
        raise PreviewRefused("chemin invalide")
    addresses = resolve(host, port, resolver=resolver)
    query = f"?{parts.query}" if parts.query else ""
    default = DEFAULT_PORTS[scheme]
    suffix = "" if port == default else f":{port}"
    return Pinned(
        host=host,
        ip=addresses[0],
        scheme=scheme,
        port=port,
        url=f"{scheme}://{_literal(addresses[0])}{suffix}{path}{query}",
        host_header=f"{host}{suffix}",
    )


# --------------------------------------------------------------------------- #
# une requête, bornée
# --------------------------------------------------------------------------- #
def _request_once(
    pinned: Pinned,
    *,
    transport: Optional[httpx.BaseTransport],
    timeout: float,
    stop: float,
) -> httpx.Response:
    request = httpx.Request(
        "GET",
        pinned.url,
        headers={
            "Host": pinned.host_header,
            "User-Agent": USER_AGENT,
            # Un aperçu ne demande que la tête : le corps ne sert à rien.
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "identity",
            # Ni cookie ni Referer : l'aperçu ne raconte à personne d'où il vient.
            "Connection": "close",
        },
        extensions={"sni_hostname": pinned.host},
    )
    # Le budget total rogne le délai de cette requête : sans cela, trois
    # redirections à six secondes chacune dépasseraient déjà le plafond.
    remaining = stop - time.monotonic()
    if remaining <= 0:
        raise PreviewRefused("délai total dépassé avant la requête")
    client = httpx.Client(
        transport=transport or httpx.HTTPTransport(retries=0),
        timeout=min(timeout, remaining),
        follow_redirects=False,
    )
    try:
        return client.send(request, stream=True)
    except httpx.HTTPError as exc:
        client.close()
        raise PreviewRefused(f"hôte injoignable : {type(exc).__name__}: {exc}") from exc


def _read_capped(reply: httpx.Response, max_bytes: int, *, stop: float) -> bytes:
    """Les octets, bornés — **après** décompression, et dans le délai total.

    ``iter_bytes`` rend le corps décodé (gzip inclus), là où ``iter_raw``
    rendrait les octets du fil : le plafond porte donc bien sur ce qu'un serveur
    peut réellement faire gonfler. On demande ``identity``, mais un serveur peut
    l'ignorer, et c'est ce cas que compte la mesure.

    Un serveur qui répond un octet à la fois n'atteint jamais un délai par
    requête : le budget total, vérifié à chaque morceau, est ce qui l'arrête.
    """
    declared = int(reply.headers.get("content-length") or 0)
    if declared > max_bytes:
        raise PreviewRefused(f"page trop lourde annoncée : {declared} octets")
    chunks: List[bytes] = []
    total = 0
    for chunk in reply.iter_bytes():
        if time.monotonic() > stop:
            raise PreviewRefused("délai total dépassé pendant la lecture")
        total += len(chunk)
        if total > max_bytes:
            raise PreviewRefused(f"page trop lourde : plus de {max_bytes} octets")
        chunks.append(chunk)
    return b"".join(chunks)


def _fetch(
    raw: str,
    *,
    resolver: Optional[Resolver],
    transport: Optional[httpx.BaseTransport],
    timeout: float,
    max_bytes: int,
    stop: float,
    guard: Optional[Callable[[Pinned, str], None]] = None,
) -> Tuple[Pinned, str, str, bytes, int]:
    """Récupère la page en suivant les redirections **une par une, revalidées**.

    ``guard`` est appelé pour **chaque** saut, avant la requête : c'est ce qui
    fait relire ``robots.txt`` chez l'hôte de destination. Contrôler seulement
    l'URL de départ laisserait un site interdit se faire lire par un 302.

    Rend (épinglage final, url finale, type déclaré, octets, nombre de sauts).
    """
    pinned = pin(raw, resolver=resolver)
    current = pinned
    final = raw
    for hop in range(MAX_REDIRECTS + 1):
        if guard is not None:
            guard(current, urlsplit(final).path or "/")
        reply = _request_once(current, transport=transport, timeout=timeout, stop=stop)
        try:
            if reply.is_redirect:
                location = reply.headers.get("location")
                if not location:
                    raise PreviewRefused(f"redirection {reply.status_code} sans adresse")
                if hop >= MAX_REDIRECTS:
                    raise PreviewRefused(f"trop de redirections (>{MAX_REDIRECTS})")
                # La cible est revalidée comme une adresse neuve : schéma, port,
                # résolution, adresse publique. C'est ici qu'un 302 vers
                # 127.0.0.1 tombe.
                target = urljoin(final, location)
                revalidated = pin(target, resolver=resolver)
                if current.scheme == "https" and revalidated.scheme == "http":
                    # Un site chiffré qui renvoie vers du clair ne fait pas
                    # « un saut de plus » : il rend la page lisible en chemin.
                    raise PreviewRefused("redirection refusée : https -> http")
                current = revalidated
                final = target
                continue
            if reply.status_code != 200:
                raise PreviewRefused(f"réponse HTTP {reply.status_code}")
            body = _read_capped(reply, max_bytes, stop=stop)
            return current, final, reply.headers.get("content-type", ""), body, hop
        finally:
            reply.close()
    raise PreviewRefused(f"trop de redirections (>{MAX_REDIRECTS})")


# --------------------------------------------------------------------------- #
# robots.txt
# --------------------------------------------------------------------------- #
_ROBOTS: Dict[str, Tuple[float, List[str], str]] = {}


def _robots_rules(text: str) -> Tuple[List[str], bool]:
    """Les ``Disallow`` qui nous concernent, et si un groupe ``*`` existe.

    Un robots.txt se lit par groupes : ``User-agent: *`` puis ses règles. On
    retient le groupe ``*`` et celui qui nous nomme ; les autres ne nous
    concernent pas. Un fichier absent ou vide autorise tout.
    """
    rules: List[str] = []
    applies = False
    wildcard = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            applies = value == "*" or "uh-universal-harness" in value.lower()
            wildcard = wildcard or value == "*"
        elif key == "disallow" and applies and value:
            rules.append(value)
    return rules, wildcard


def _robots_allows(
    scheme: str,
    pinned: Pinned,
    path: str,
    *,
    resolver: Optional[Resolver],
    transport: Optional[httpx.BaseTransport],
    timeout: float,
    stop: float,
) -> Tuple[bool, str]:
    """``(autorisé, état)`` — état : ``allowed``, ``absent``, ``forbidden``,
    ``disallowed``.

    Suit la RFC 9309 (§2.3.1.4, vérifiée le 24/11) : « if the robots.txt file is
    unreachable due to server or network errors, this means the robots.txt file
    is undefined and the crawler MUST assume complete disallow ». Donc :

    * **4xx** — le fichier n'existe pas : accès autorisé (état ``absent``) ;
    * **5xx, ou injoignable** — disallow complet, et l'aperçu est refusé ;
    * autre statut (3xx non suivi, 1xx…) — traité comme indisponible, donc
      refusé : en cas de doute sur un fichier qui *restreint*, on ne devine pas.

    La version précédente autorisait sur 5xx et sur erreur réseau : c'était un
    écart avec la norme, dans le sens permissif.
    """
    key = f"{scheme}://{pinned.host_header}"
    cached = _ROBOTS.get(key)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        rules, state = cached[1], cached[2]
    else:
        robots_url = f"{scheme}://{pinned.host}/robots.txt"
        try:
            final_pin = pin(robots_url, resolver=resolver)
            reply = _request_once(final_pin, transport=transport, timeout=timeout, stop=stop)
        except PreviewRefused as exc:
            _ROBOTS[key] = (now + ROBOTS_TTL, [], "forbidden")
            raise PreviewRefused(
                f"robots.txt injoignable, disallow complet (RFC 9309) : {exc}"
            ) from exc
        try:
            status = reply.status_code
            if 400 <= status < 500:
                _ROBOTS[key] = (now + ROBOTS_TTL, [], "absent")
                return True, "absent"
            if status != 200:
                _ROBOTS[key] = (now + ROBOTS_TTL, [], "forbidden")
                raise PreviewRefused(
                    f"robots.txt indisponible (HTTP {status}), disallow complet (RFC 9309)"
                )
            text = _read_capped(reply, 64 * 1024, stop=stop).decode("utf-8", "replace")
        except PreviewRefused:
            _ROBOTS[key] = (now + ROBOTS_TTL, [], "forbidden")
            raise
        finally:
            reply.close()
        rules, _wildcard = _robots_rules(text)
        state = "disallowed" if rules else "allowed"
        _ROBOTS[key] = (now + ROBOTS_TTL, rules, state)

    for rule in rules:
        if path.startswith(rule):
            return False, "disallowed"
    return True, state


def clear_robots_cache() -> None:
    """Les tests changent de serveur d'un cas à l'autre."""
    _ROBOTS.clear()


# --------------------------------------------------------------------------- #
# extraction : cinq champs, jamais le corps
# --------------------------------------------------------------------------- #
class _Stop(Exception):
    """Le ``<head>`` est fini : inutile de lire la suite."""


_META_KEYS = {"og:title", "og:description", "og:image", "description"}


class _Head(HTMLParser):
    """Lit le ``<head>`` et s'arrête à ``<body>``. Aucune balise n'est recopiée."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: Dict[str, str] = {}
        self.icon = ""
        self._in_title = False
        self._title_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        name = tag.lower()
        if name == "body":
            raise _Stop
        if name == "title":
            self._in_title = True
            return
        table = {str(key).lower(): (value or "") for key, value in attrs}
        if name == "meta":
            prop = (table.get("property") or table.get("name") or "").strip().lower()
            content = table.get("content", "").strip()
            if prop in _META_KEYS and content and prop not in self.meta:
                self.meta[prop] = content
        elif name == "link":
            rel = table.get("rel", "").lower()
            href = table.get("href", "").strip()
            if href and "icon" in rel.split() and not self.icon:
                self.icon = href

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)


def _clean(text: str, limit: int) -> str:
    """Une valeur affichable : espaces écrasés, caractères de contrôle retirés."""
    value = html.unescape(str(text or ""))
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def _absolute(base: str, candidate: str) -> str:
    if not candidate:
        return ""
    if candidate.lower().startswith(("http://", "https://")):
        return candidate[:URL_MAX]
    try:
        joined = urljoin(base, candidate)
    except ValueError:
        return ""
    return joined[:URL_MAX] if joined.lower().startswith(("http://", "https://")) else ""


def extract(body: bytes, base_url: str) -> Dict[str, str]:
    """Les cinq champs autorisés, à partir des octets de la page."""
    text = body.decode("utf-8", "replace")
    parser = _Head()
    try:
        parser.feed(text)
        parser.close()
    except _Stop:
        pass  # le corps ne nous intéresse pas
    title = parser.meta.get("og:title") or "".join(parser._title_parts)  # noqa: SLF001
    return {
        "title": _clean(title, TITLE_MAX),
        "description": _clean(
            parser.meta.get("og:description") or parser.meta.get("description") or "",
            DESCRIPTION_MAX,
        ),
        "image": _absolute(base_url, parser.meta.get("og:image", "")),
        "icon": _absolute(base_url, parser.icon),
    }


# --------------------------------------------------------------------------- #
# l'aperçu complet
# --------------------------------------------------------------------------- #
def preview(
    raw: str,
    *,
    resolver: Optional[Resolver] = None,
    transport: Optional[httpx.BaseTransport] = None,
    timeout: float = TIMEOUT,
    max_bytes: int = MAX_BYTES,
    check_robots: bool = True,
    deadline: float = TOTAL_DEADLINE,
) -> Page:
    """Un aperçu de ``raw``, ou :class:`PreviewRefused`.

    ``resolver`` et ``transport`` existent pour les tests : le chemin par défaut
    résout vraiment et se connecte vraiment, sur l'adresse épinglée.
    ``deadline`` borne l'aperçu **entier** : robots.txt, redirections et lecture
    du corps comptent dans le même budget.
    """
    stop = time.monotonic() + deadline
    pin(raw, resolver=resolver)  # refus immédiat d'une adresse non publique
    notes: List[str] = []
    states: List[str] = []

    def guard(current: Pinned, path: str) -> None:
        """robots.txt, chez **cet** hôte, pour **ce** chemin."""
        if not check_robots:
            return
        allowed, checked = _robots_allows(
            current.scheme,
            current,
            path,
            resolver=resolver,
            transport=transport,
            timeout=timeout,
            stop=stop,
        )
        if not states:
            states.append(checked)
        if not allowed:
            raise PreviewRefused(f"robots.txt interdit cette page : {current.host}{path}")

    final_pin, final_url, content_type, body, hops = _fetch(
        raw,
        resolver=resolver,
        transport=transport,
        timeout=timeout,
        max_bytes=max_bytes,
        stop=stop,
        guard=guard,
    )
    state = states[0] if states else "non-demandé"
    if state == "absent":
        notes.append("robots.txt absent (4xx) : accès autorisé")
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared and "html" not in declared and "xml" not in declared:
        raise PreviewRefused(f"type refusé : {declared} (HTML attendu)")
    if not body.strip():
        raise PreviewRefused("page vide")

    fields = extract(body, final_url)
    return Page(
        url=raw,
        final_url=final_url,
        host=final_pin.host,
        title=fields["title"],
        description=fields["description"],
        image=fields["image"],
        icon=fields["icon"],
        redirects=hops,
        bytes_read=len(body),
        robots=state,
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- #
# l'image d'un article
# --------------------------------------------------------------------------- #
#: Une vignette d'aperçu, pas un film : 2 Mo, et le type par les octets.
IMAGE_MAX_BYTES = 2 * 1024 * 1024
#: Ce qu'une page a le droit d'afficher. Le SVG n'y est pas : c'est un document
#: qui peut porter du script, pas une image à poser dans un `<img>`.
IMAGE_MIME = ("image/png", "image/jpeg", "image/gif", "image/webp")


@dataclass(frozen=True)
class ImageData:
    """Les octets d'une image d'aperçu, et le type que les octets déclarent."""

    mime: str
    data: bytes
    final_url: str


def fetch_image(
    raw: str,
    *,
    resolver: Optional[Resolver] = None,
    transport: Optional[httpx.BaseTransport] = None,
    timeout: float = TIMEOUT,
    deadline: float = TOTAL_DEADLINE,
    max_bytes: int = IMAGE_MAX_BYTES,
) -> ImageData:
    """L'image d'un article, récupérée avec les mêmes gardes que la page.

    Même résolution, mêmes refus d'adresses, même épinglage, mêmes redirections
    revalidées : seul le plafond change (2 Mo) et le fait que le type est décidé
    par les octets. ``robots.txt`` n'est pas consulté ici — il régit l'accès aux
    pages, et l'image est demandée par la page elle-même.
    """
    stop = time.monotonic() + deadline
    pin(raw, resolver=resolver)  # refus immédiat d'une adresse non publique
    _final_pin, final_url, _declared, body, _hops = _fetch(
        raw,
        resolver=resolver,
        transport=transport,
        timeout=timeout,
        max_bytes=max_bytes,
        stop=stop,
    )
    mime = sniff_mime(body)
    if mime is None or mime not in IMAGE_MIME:
        raise PreviewRefused(
            "format refusé : JPEG, PNG, WebP ou GIF attendu (un SVG n'est pas une image affichable)"
        )
    return ImageData(mime=mime, data=body, final_url=final_url)


# --------------------------------------------------------------------------- #
# provenance : la règle du serveur, pas seulement celle de la page
# --------------------------------------------------------------------------- #
ORIGINS = ("tool", "operator", "click")
#: Rôles de message qui prouvent une provenance.
_PROOF_ROLE = {"tool": "tool", "operator": "user"}


def provenance_ok(
    url: str, origin: str, messages: Optional[List[Dict[str, Any]]]
) -> Tuple[bool, str]:
    """``(autorisé, raison)`` — le serveur ne croit pas l'origine sur parole.

    * ``click`` — l'opérateur a cliqué : c'est le consentement, rien à vérifier ;
    * ``tool`` — l'adresse doit figurer dans un **résultat d'outil de cette
      session**. Un modèle qui écrit une URL ne peut donc pas la faire récupérer
      en prétendant qu'elle vient d'un outil ;
    * ``operator`` — l'adresse doit figurer dans un **message de l'opérateur**.

    Ce contrôle est un renfort : seul le navigateur appelle ces routes, et la CSP
    l'empêche d'exécuter du script étranger. Il ferme le cas où une page
    parviendrait quand même à déclencher la requête.
    """
    label = (origin or "").strip().lower()
    if label not in ORIGINS:
        return False, "origine inconnue : « tool », « operator » ou « click » attendu"
    if label == "click":
        return True, ""
    needle = (url or "").strip()
    if not needle:
        return False, "adresse vide"
    role = _PROOF_ROLE[label]
    for message in messages or []:
        if str(message.get("role") or "") != role:
            continue
        if needle in str(message.get("content") or ""):
            return True, ""
    if label == "tool":
        return False, (
            "cette adresse ne figure dans aucun résultat d'outil de cette session : "
            "cliquez sur « Aperçu » pour la demander"
        )
    return False, (
        "cette adresse ne figure dans aucun de vos messages : "
        "cliquez sur « Aperçu » pour la demander"
    )


# --------------------------------------------------------------------------- #
# l'interrupteur
# --------------------------------------------------------------------------- #
SETTINGS_FILE = "link-previews.toml"
#: Ce que la page affiche à la première activation, mot pour mot.
SENTENCE = "UH contactera ces sites depuis votre machine ; ils verront votre adresse IP."


def settings_path() -> Path:
    return uh_home() / SETTINGS_FILE


def load_enabled() -> bool:
    """Désactivé par défaut : un fichier absent vaut ``False``."""
    path = settings_path()
    if not path.is_file():
        return False
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return data.get("enabled") is True


def save_enabled(enabled: bool) -> Path:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = "true" if enabled else "false"
    body = "# Aperçus de liens : désactivés par défaut, activés par l'opérateur.\n"
    path.write_text(body + f"enabled = {value}\n", encoding="utf-8")
    return path


__all__ = [
    "ALLOWED_SCHEMES",
    "IMAGE_MAX_BYTES",
    "ImageData",
    "ORIGINS",
    "SENTENCE",
    "TOTAL_DEADLINE",
    "fetch_image",
    "load_enabled",
    "provenance_ok",
    "save_enabled",
    "settings_path",
    "DEFAULT_PORTS",
    "MAX_BYTES",
    "MAX_REDIRECTS",
    "Page",
    "Pinned",
    "PreviewRefused",
    "clear_robots_cache",
    "extract",
    "pin",
    "preview",
    "resolve",
]
