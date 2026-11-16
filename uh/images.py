"""Images a provider returns in its answer: normalised, fetched, bounded.

The interface draws an image from a data URL and nothing else. Everything here
exists to turn what an API sends into that one shape, or to say why it refused -
never to hand the browser a remote URL:

* a browser that loads `https://…` leaks its IP and the open session's context to
  a third party, and the page runs with a local token in its cookie jar;
* a server that fetches any URL a model produced is a request forgery primitive
  (it can reach 127.0.0.1, the LAN, cloud metadata). So: http(s) only, no literal
  loopback or private address, no redirect that lands on one, a hard size cap and
  a MIME decided by the magic bytes - the declared type is a claim, not a proof.

Refusals are values, not exceptions: a refused image produces one readable line
in the transcript, because an empty frame reads like a bug.
"""

from __future__ import annotations

import base64
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

#: Same list as the tool artefacts, `svg` excluded: an SVG is a document that can
#: carry script, and `img.src` is the only sink allowed here.
ALLOWED_MIME = ("image/png", "image/jpeg", "image/gif", "image/webp")

#: Hard caps. A provider that returns a 20 MB PNG is a bug or an abuse, not a
#: picture: half an image is not an image, so it is refused whole.
MAX_BYTES = 2 * 1024 * 1024
MAX_IMAGES = 4
FETCH_TIMEOUT = 10.0

MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")

_SIGNATURES: Tuple[Tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


@dataclass
class Artifact:
    """One image the interface can draw, in the only shape it accepts."""

    mime: str
    data: bytes
    note: str = ""

    def as_payload(self) -> Dict[str, Any]:
        return {
            "mime": self.mime,
            "bytes": len(self.data),
            "data_url": f"data:{self.mime};base64,{base64.b64encode(self.data).decode()}",
            **({"note": self.note} if self.note else {}),
        }


@dataclass
class Normalised:
    """The answer as text, plus what could not be turned into an image."""

    text: str
    artifacts: List[Artifact] = field(default_factory=list)
    refusals: List[str] = field(default_factory=list)

    def payloads(self) -> List[Dict[str, Any]]:
        return [artifact.as_payload() for artifact in self.artifacts]


def sniff_mime(data: bytes) -> Optional[str]:
    """The MIME the bytes actually are, or ``None``. Never what was declared."""
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    # WEBP: RIFF....WEBP
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_data_url(url: str) -> Optional[Tuple[bytes, str]]:
    """``data:<mime>;base64,<payload>`` -> (bytes, mime), or ``None``."""
    if not url.startswith("data:"):
        return None
    header, _, payload = url[5:].partition(",")
    if ";base64" not in header or not payload:
        return None
    try:
        data = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError):
        return None
    return data, header.split(";", 1)[0]


def _host_is_local(host: str) -> bool:
    """Literal address only: resolving a name here would let DNS pick the target."""
    candidate = (host or "").strip("[]").lower()
    if not candidate:
        return True
    if candidate in {"localhost", "localhost.localdomain"}:
        return True
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False  # a hostname we do not resolve: the transport resolves it
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def check_url(url: str) -> str:
    """``""`` when the URL may be fetched, otherwise the reason it may not."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        return f"URL illisible ({exc})"
    if parts.scheme not in {"http", "https"}:
        return f"schéma refusé ({parts.scheme or 'aucun'}), http(s) seulement"
    if not parts.hostname:
        return "URL sans hôte"
    if _host_is_local(parts.hostname):
        return f"adresse locale refusée ({parts.hostname})"
    if parts.username or parts.password:
        return "identifiants dans l'URL refusés"
    return ""


def fetch_image(
    url: str,
    *,
    client: Optional[httpx.Client] = None,
    max_bytes: int = MAX_BYTES,
    timeout: float = FETCH_TIMEOUT,
) -> Tuple[Optional[Artifact], str]:
    """Download one remote image. Returns ``(artifact, "")`` or ``(None, reason)``."""
    reason = check_url(url)
    if reason:
        return None, reason
    owned = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=False)
    try:
        with http.stream("GET", url) as response:
            # A redirect can land anywhere, including 127.0.0.1: one hop is enough
            # to check, and refusing it is cheaper than re-validating a chain.
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location") or ""
                target = str(httpx.URL(url).join(location))
                redirected = check_url(target)
                return None, f"redirection refusée ({redirected or target})"
            if response.status_code != 200:
                return None, f"HTTP {response.status_code}"
            declared = int(response.headers.get("content-length") or 0)
            if declared and declared > max_bytes:
                return None, f"trop volumineuse ({declared} octets, plafond {max_bytes})"
            chunks: List[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    return None, f"trop volumineuse (> {max_bytes} octets)"
                chunks.append(chunk)
            data = b"".join(chunks)
    except httpx.HTTPError as exc:
        return None, f"téléchargement impossible ({type(exc).__name__})"
    finally:
        if owned:
            http.close()

    mime = sniff_mime(data)
    if mime is None:
        return None, "format non reconnu (png, jpeg, gif, webp seulement)"
    return Artifact(mime=mime, data=data, note=url), ""


def _block_url(block: Dict[str, Any]) -> Optional[str]:
    """The URL an image block points at, whatever its dialect."""
    for key in ("image_url", "url", "source"):
        value = block.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            for inner in ("url", "data"):
                candidate = value.get(inner)
                if isinstance(candidate, str) and candidate:
                    if inner == "data":
                        media = str(value.get("media_type") or "image/png")
                        return f"data:{media};base64,{candidate}"
                    return candidate
    return None


def normalise(
    content: Any,
    *,
    client: Optional[httpx.Client] = None,
    fetch: bool = True,
    max_images: int = MAX_IMAGES,
) -> Normalised:
    """An assistant answer -> text + artefacts + refusals.

    Accepts the two shapes an API uses: a list of content blocks
    (``{"type": "image_url", …}``), and plain text carrying markdown images.
    Everything that is not an image block stays in the text, in order.
    """
    out = Normalised(text="")
    texts: List[str] = []

    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                texts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type") or "")
            if kind in {"text", "input_text"}:
                texts.append(str(block.get("text") or ""))
                continue
            if kind not in {"image_url", "image", "input_image", "image_base64"}:
                continue
            url = _block_url(block)
            if not url:
                out.refusals.append("bloc image sans URL")
                continue
            if len(out.artifacts) >= max_images:
                out.refusals.append(f"plus de {max_images} images : le reste est ignoré")
                break
            artifact, reason = _one(url, client=client, fetch=fetch)
            if artifact is not None:
                out.artifacts.append(artifact)
            elif reason:
                out.refusals.append(reason)
    else:
        texts.append(str(content or ""))

    text = "\n".join(part for part in texts if part).strip()
    if fetch:
        trimmed, found, refusals = _from_markdown(
            text, client=client, room=max_images - len(out.artifacts)
        )
        text = trimmed
        out.artifacts.extend(found)
        out.refusals.extend(refusals)
    out.text = text
    return out


def _from_markdown(
    text: str, *, client: Optional[httpx.Client], room: int
) -> Tuple[str, List[Artifact], List[str]]:
    """Markdown images in a text answer: the reference is kept, bytes added."""
    found: List[Artifact] = []
    refusals: List[str] = []
    if not text or "![" not in text:
        return text, found, refusals
    for match in list(MARKDOWN_IMAGE.finditer(text)):
        if len(found) >= max(0, room):
            refusals.append(f"plus de {MAX_IMAGES} images : le reste est ignoré")
            break
        artifact, reason = _one(match.group(2), client=client, fetch=True)
        if artifact is not None:
            found.append(artifact)
        elif reason:
            refusals.append(reason)
    return text, found, refusals


def _one(
    url: str, *, client: Optional[httpx.Client], fetch: bool
) -> Tuple[Optional[Artifact], str]:
    decoded = decode_data_url(url)
    if decoded is not None:
        data, declared = decoded
        if len(data) > MAX_BYTES:
            return None, f"image trop volumineuse (> {MAX_BYTES} octets)"
        mime = sniff_mime(data)
        if mime is None:
            return None, f"format non reconnu (déclaré {declared or 'inconnu'})"
        return Artifact(mime=mime, data=data), ""
    if not fetch:
        return None, ""
    return fetch_image(url, client=client)


def strip_artifacts(message: Dict[str, Any]) -> Dict[str, Any]:
    """A message as a provider must see it: text only, never image bytes."""
    artifacts = message.get("artifacts")
    if not artifacts:
        return message
    return {key: value for key, value in message.items() if key != "artifacts"}


def note_for(refusals: Iterable[str]) -> str:
    """One transcript line for everything that could not be shown."""
    lines = [line for line in refusals if line]
    if not lines:
        return ""
    return "image non affichée : " + " ; ".join(lines[:3])
