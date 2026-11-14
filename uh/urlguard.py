"""What a user-typed URL may be before UH sends a request to it.

The Connexions panel lets a person paste the address of an MCP server. That
address is then fetched by UH itself, which makes it a classic SSRF surface: a
link pasted "just to see" can aim at a router admin page, a cloud metadata
endpoint, or a host on the LAN. The rules below keep the decision explicit and
readable instead of relying on the caller.

* ``http`` and ``https`` only - no ``file:``, ``ftp:`` or ``javascript:``.
* no credentials in the URL (``user:pass@host``): they end up in a log, and the
  token is meant for ``mcp-secrets.toml``.
* a plain ``http`` URL to a host that is not loopback is refused unless the
  caller passes ``allow_insecure=True``: the bearer token would cross the
  network in clear text. When it is allowed, the caller gets a warning string it
  must show, not swallow.
* loopback is decided on the literal host (``localhost``, an IP that
  :mod:`ipaddress` says is loopback), never on a DNS answer: resolving a name to
  decide whether it is "local" would make the check depend on the attacker's
  nameserver.
* redirects are never followed (``uh.mcp_http`` builds its client with
  ``follow_redirects=False``): an approved address must not become an arbitrary
  one after a 302.

The function returns a :class:`Check`; a refusal raises :class:`URLRejected`
with a sentence a person can act on.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

ALLOWED_SCHEMES = ("http", "https")
DEFAULT_PORTS = {"http": 80, "https": 443}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class URLRejected(RuntimeError):
    """The URL is not something UH will fetch. The message is the fix."""


@dataclass(frozen=True)
class Check:
    """A URL that passed, plus what the caller must tell the user about it."""

    url: str
    scheme: str
    host: str
    port: int
    loopback: bool
    insecure: bool
    warning: str = ""


def _clean(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise URLRejected("adresse vide : colle l'URL du serveur MCP (http://127.0.0.1:8787)")
    if any(ord(char) < 32 or char == " " for char in value):
        raise URLRejected("adresse invalide : elle contient un espace ou un caractère de contrôle")
    return value


def is_loopback_host(host: str) -> bool:
    """Literal-only loopback test: no DNS, no suffix matching."""
    name = (host or "").strip().strip("[]").lower()
    if not name:
        return False
    if name in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def check(raw: str, *, allow_insecure: bool = False) -> Check:
    """Validate a server URL, or raise :class:`URLRejected` with the reason."""
    value = _clean(raw)
    parts = urlsplit(value)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise URLRejected(
            "adresse refusée : seules les URL http:// et https:// sont acceptées "
            f"(reçu {scheme or 'aucun schéma'})"
        )
    if parts.username or parts.password:
        raise URLRejected(
            "adresse refusée : elle contient un identifiant. Le jeton va dans le champ "
            "« Jeton » (fichier protégé), pas dans l'URL"
        )
    if parts.fragment:
        raise URLRejected("adresse refusée : retire le fragment (#...) de l'URL")
    host = (parts.hostname or "").strip()
    if not host:
        raise URLRejected("adresse refusée : aucun nom d'hôte (ex. http://127.0.0.1:8787)")
    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise URLRejected(f"adresse refusée : le port n'est pas un nombre ({exc})") from exc
    if not 1 <= port <= 65535:
        raise URLRejected("adresse refusée : le port doit être entre 1 et 65535")

    loopback = is_loopback_host(host)
    insecure = scheme == "http" and not loopback
    warning = ""
    if insecure:
        if not allow_insecure:
            raise URLRejected(
                "adresse refusée : http non chiffré vers une machine distante "
                f"({host}). Utilise https://, ou confirme « autoriser le HTTP non chiffré » "
                "si le réseau est de confiance"
            )
        warning = (
            f"HTTP non chiffré vers {host} : le jeton circule en clair sur le réseau. "
            "Préfère https:// dès que le serveur le propose."
        )

    path = parts.path or ""
    normalised = urlunsplit((scheme, parts.netloc, path, parts.query, ""))
    return Check(
        url=normalised,
        scheme=scheme,
        host=host,
        port=port,
        loopback=loopback,
        insecure=insecure,
        warning=warning,
    )


def check_optional(raw: str, *, allow_insecure: bool = False) -> Optional[Check]:
    """``None`` for an empty string, a :class:`Check` otherwise."""
    if not (raw or "").strip():
        return None
    return check(raw, allow_insecure=allow_insecure)


__all__ = [
    "ALLOWED_SCHEMES",
    "Check",
    "LOOPBACK_HOSTS",
    "URLRejected",
    "check",
    "check_optional",
    "is_loopback_host",
]
