"""Explorateur de fichiers (P3-16c lot 1) : la borne se prouve, elle ne se lit pas.

Ces tests interrogent les routes par le vrai chemin HTTP, avec la même sandbox que les
outils. Ce qu'ils vérifient est ce que l'outil refuse déjà : `~/.uh` en entier, `..`, un
lien qui sort du workspace. La route n'a pas sa propre règle - elle emprunte celle de
`FileToolset.resolve_read` - donc un écart ici serait un écart de la sandbox elle-même.
"""

from __future__ import annotations

import asyncio
import base64
import struct
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-fs"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
    b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "sous").mkdir(parents=True)
    (root / "notes.txt").write_text("bonjour\n" * 10, encoding="utf-8")
    (root / "image.png").write_bytes(PNG_1x1)
    (root / "sous" / "profond.md").write_text("# titre", encoding="utf-8")
    return root


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    """Le faux ~/.uh : c'est lui qui ne doit jamais sortir."""
    uh_home = tmp_path / "home"
    uh_home.mkdir(parents=True)
    (uh_home / "keys.toml").write_text(
        "[[providers]]\nkey = 'secret-ne-sort-pas'\n", encoding="utf-8"
    )
    (uh_home / "web-token").write_text("jeton", encoding="utf-8")
    return uh_home


@pytest.fixture()
def app(workspace: Path, home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UH_HOME", str(home))
    return create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=workspace,
        provider_factory=lambda kind, model: _Stub(),
        kind="stub",
        model="stub:1b",
    )


class _Stub:
    model = "stub:1b"
    streaming = True

    def chat(self, messages, tools, *, on_token=None):
        return {"role": "assistant", "content": "ok", "tool_calls": []}


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


# --------------------------------------------------------------------------- #
# la borne
# --------------------------------------------------------------------------- #
def test_the_token_is_required(app) -> None:
    async def go():
        async with client(app) as http:
            return await http.get("/api/fs/list")

    assert run(go()).status_code == 401


def test_the_configuration_folder_is_refused(app, home: Path) -> None:
    """`keys.toml` ne sort pas, même quand le workspace le contient (ou le pointe)."""

    async def go():
        async with client(app) as http:
            listed = await http.get("/api/fs/list", params={"path": str(home)}, headers=AUTH)
            preview = await http.get(
                "/api/fs/preview", params={"path": str(home / "keys.toml")}, headers=AUTH
            )
            raw = await http.get(
                "/api/fs/raw", params={"path": str(home / "keys.toml")}, headers=AUTH
            )
            stat = await http.get("/api/fs/stat", params={"path": str(home)}, headers=AUTH)
            return listed, preview, raw, stat

    listed, preview, raw, stat = run(go())
    for response in (listed, preview, raw, stat):
        assert response.status_code == 403, response.text
        assert "refused" in response.json()["detail"]
    assert "secret-ne-sort-pas" not in preview.text
    assert "secret-ne-sort-pas" not in raw.text


def test_a_dotdot_escape_is_resolved_then_refused(app, workspace: Path, home: Path) -> None:
    """`..` n'est pas filtré en texte : il est résolu, puis refusé s'il sort."""

    async def go():
        async with client(app) as http:
            return await http.get(
                "/api/fs/preview", params={"path": "../home/keys.toml"}, headers=AUTH
            )

    response = run(go())
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail.startswith("refused"), detail
    assert "keys.toml" in detail, "le refus nomme le chemin visé"


def test_a_symlink_that_leaves_the_workspace_is_refused(
    app, workspace: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "dehors.txt"
    outside.write_text("hors perimetre", encoding="utf-8")
    link = workspace / "raccourci.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows sans privilege
        pytest.skip("les liens symboliques demandent un privilege sur ce poste")

    async def go():
        async with client(app) as http:
            return await http.get("/api/fs/preview", params={"path": "raccourci.txt"}, headers=AUTH)

    response = run(go())
    assert response.status_code == 403
    assert "hors perimetre" not in response.text


# --------------------------------------------------------------------------- #
# ce qui marche
# --------------------------------------------------------------------------- #
def test_the_workspace_listing_dirs_first(app, workspace: Path) -> None:
    async def go():
        async with client(app) as http:
            return await http.get("/api/fs/list", headers=AUTH)

    response = run(go())
    assert response.status_code == 200, response.text
    body = response.json()
    names = [entry["name"] for entry in body["entries"]]
    assert names[0] == "sous", "les dossiers d'abord"
    assert set(names) == {"sous", "notes.txt", "image.png"}
    assert body["parent"] == "", "le workspace est la racine : rien au-dessus"
    assert body["workspace"] == str(workspace.resolve())
    assert all(entry["kind"] in {"dir", "file"} for entry in body["entries"])


def test_listing_a_subfolder_offers_the_way_back(app, workspace: Path) -> None:
    async def go():
        async with client(app) as http:
            return await http.get(
                "/api/fs/list", params={"path": str(workspace / "sous")}, headers=AUTH
            )

    body = run(go()).json()
    assert body["parent"] == str(workspace.resolve())
    assert [entry["name"] for entry in body["entries"]] == ["profond.md"]


def test_a_listing_is_capped_and_says_so(app, workspace: Path) -> None:
    for index in range(12):
        (workspace / f"f{index:02d}.txt").write_text("x", encoding="utf-8")

    async def go():
        async with client(app) as http:
            return await http.get("/api/fs/list", params={"limit": 5}, headers=AUTH)

    body = run(go()).json()
    assert len(body["entries"]) == 5
    assert body["total"] == 15
    assert body["truncated"] is True


def test_a_text_preview_is_truncated_not_dumped(app, workspace: Path) -> None:
    big = workspace / "gros.txt"
    big.write_text("A" * 5000, encoding="utf-8")

    async def go():
        async with client(app) as http:
            return await http.get(
                "/api/fs/preview", params={"path": "gros.txt", "max_bytes": 100}, headers=AUTH
            )

    body = run(go()).json()
    assert body["kind"] == "text"
    assert len(body["text"]) == 100
    assert body["truncated"] is True
    assert body["bytes"] == 5000


def test_the_workspace_relative_path_works_too(app) -> None:
    async def go():
        async with client(app) as http:
            return await http.get("/api/fs/preview", params={"path": "notes.txt"}, headers=AUTH)

    body = run(go()).json()
    assert body["kind"] == "text"
    assert body["text"].startswith("bonjour")


# --------------------------------------------------------------------------- #
# aperçus
# --------------------------------------------------------------------------- #
def test_an_image_preview_is_a_real_png_data_url(app) -> None:
    async def go():
        async with client(app) as http:
            return await http.get("/api/fs/preview", params={"path": "image.png"}, headers=AUTH)

    body = run(go()).json()
    assert body["kind"] == "image"
    assert body["data_url"].startswith("data:image/png;base64,")
    raw = base64.b64decode(body["data_url"].split(",", 1)[1])
    assert raw == PNG_1x1
    assert struct.unpack(">II", raw[16:24]) == (1, 1)


def test_a_binary_preview_says_it_cannot_and_sends_nothing(app, workspace: Path) -> None:
    blob = workspace / "mystere.bin"
    blob.write_bytes(b"\x00\x01\x02binary")

    async def go():
        async with client(app) as http:
            return await http.get("/api/fs/preview", params={"path": "mystere.bin"}, headers=AUTH)

    body = run(go()).json()
    assert body["kind"] == "binary"
    assert "text" not in body


def test_a_pdf_is_read_when_pypdf_is_there_and_says_why_when_not(app, workspace: Path) -> None:
    """Le cockpit ne prétend jamais avoir lu un PDF qu'il n'a pas pu ouvrir."""
    fake = workspace / "doc.pdf"
    fake.write_bytes(b"%PDF-1.4\n%%EOF\n")

    async def go():
        async with client(app) as http:
            return await http.get("/api/fs/preview", params={"path": "doc.pdf"}, headers=AUTH)

    body = run(go()).json()
    assert body["kind"] == "pdf"
    try:
        import pypdf  # noqa: F401
    except Exception:  # noqa: BLE001
        assert body["readable"] is False
        assert "pypdf" in body["reason"]
    else:
        # pypdf présent : ce faux PDF est peut-être illisible, mais la réponse dit
        # toujours laquelle des deux issues c'est.
        assert body["readable"] in (True, False)
        if body["readable"] is False:
            assert body["reason"]


# --------------------------------------------------------------------------- #
# octets bruts (bouton « Attacher au tour »)
# --------------------------------------------------------------------------- #
def test_raw_bytes_come_back_as_a_download(app) -> None:
    async def go():
        async with client(app) as http:
            return await http.get("/api/fs/raw", params={"path": "image.png"}, headers=AUTH)

    response = run(go())
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream", (
        "jamais un type affichable : un SVG servi ici serait du script depuis notre origine"
    )
    assert "attachment" in response.headers.get("content-disposition", "")
    assert response.content == PNG_1x1


def test_a_file_outside_the_workspace_has_no_preview(app, tmp_path: Path) -> None:
    outside = tmp_path / "ailleurs.txt"
    outside.write_text("hors", encoding="utf-8")

    async def go():
        async with client(app) as http:
            return await http.get("/api/fs/preview", params={"path": str(outside)}, headers=AUTH)

    response = run(go())
    assert response.status_code == 403
    assert "hors" not in response.text
