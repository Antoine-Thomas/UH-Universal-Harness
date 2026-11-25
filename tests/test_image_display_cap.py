"""G10 — une image au-delà du plafond d'affichage doit s'afficher réduite.

Aujourd'hui `uh/images.py` refuse sèchement (`image trop volumineuse`). Le
comportement attendu : l'interface reçoit une version qui tient sous `MAX_BYTES`,
et l'octet d'origine est conservé intact dans le dossier de sorties d'UH — celui que
l'utilisateur ouvre.

Les deux chemins sont couverts, parce que le refus existe aux deux endroits :
`_one()` pour les `data:` URL, `fetch_image()` pour le HTTP. Corriger un seul des
deux laisserait la moitié du défaut.

Le PNG de test est fabriqué à la main (zlib + scanlines) : aucun Pillow requis pour
*écrire* le cas, ce qui permet de tester aussi le repli quand Pillow est absent.
"""

from __future__ import annotations

import base64
import struct
import sys
import zlib

import httpx
import pytest

from uh import images


def _big_png(width: int = 900, height: int = 900) -> bytes:
    """Un PNG valide et volumineux : du bruit, donc incompressible."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    rows = bytearray()
    seed = 0x9E3779B9
    for _ in range(height):
        rows.append(0)  # filtre None
        for _ in range(width * 3):
            seed = (1103515245 * seed + 12345) & 0xFFFFFFFF
            rows.append((seed >> 16) & 0xFF)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )


@pytest.fixture()
def big_png() -> bytes:
    data = _big_png()
    assert len(data) > images.MAX_BYTES, f"le cas de test doit dépasser le plafond ({len(data)})"
    return data


def _saved_original(note: str) -> "object":
    """Le chemin cité dans la note doit exister et contenir l'original."""
    from pathlib import Path

    for token in note.replace("(", " ").replace(")", " ").replace(",", " ").split():
        candidate = Path(token)
        if candidate.is_file():
            return candidate
    raise AssertionError(f"aucun chemin de fichier dans la note : {note!r}")


def test_a_data_url_over_the_cap_is_shown_reduced(big_png: bytes, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    url = "data:image/png;base64," + base64.b64encode(big_png).decode()

    artifact, reason = images._one(url, client=None, fetch=False)

    assert artifact is not None, f"l'image est refusée au lieu d'être réduite : {reason}"
    assert len(artifact.data) <= images.MAX_BYTES, len(artifact.data)
    saved = _saved_original(artifact.note)
    assert saved.read_bytes() == big_png, "l'original doit être conservé octet pour octet"


def test_a_remote_image_over_the_cap_is_shown_reduced(
    big_png: bytes, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "image/png", "content-length": str(len(big_png))},
            content=big_png,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        artifact, reason = images.fetch_image("https://example.com/big.png", client=client)
    finally:
        client.close()

    assert seen, "le faux serveur n'a jamais été appelé"
    assert artifact is not None, f"l'image distante est refusée au lieu d'être réduite : {reason}"
    assert len(artifact.data) <= images.MAX_BYTES, len(artifact.data)
    assert _saved_original(artifact.note).read_bytes() == big_png


def test_without_pillow_the_refusal_says_what_to_install(big_png: bytes, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.setitem(sys.modules, "PIL.Image", None)
    url = "data:image/png;base64," + base64.b64encode(big_png).decode()

    artifact, reason = images._one(url, client=None, fetch=False)

    if artifact is None:
        assert "pillow" in reason.lower(), f"le repli doit nommer Pillow : {reason!r}"
    else:  # Pillow reste importable malgré le patch : la réduction doit avoir eu lieu
        assert len(artifact.data) <= images.MAX_BYTES


def test_an_svg_is_still_refused(big_png: bytes) -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'
    url = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()

    artifact, reason = images._one(url, client=None, fetch=False)

    assert artifact is None
    assert "format" in reason.lower(), reason
