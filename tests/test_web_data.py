"""The data panel's routes: backup, skills, tools - all behind the token.

The server is exercised over ASGI on a temporary UH_HOME; nothing here touches
the real home, and nothing downloads from the network.
"""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh.web.app import create_app  # noqa: E402

TOKEN = "data-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("UH_UHB_URL", "")  # do not wait on a 1 s probe
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(token=TOKEN, port=8765, workspace=tmp_path, kind="stub", model="stub:1b")


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #
def test_every_data_route_needs_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            assert (await http.get("/api/backup")).status_code == 401
            assert (await http.post("/api/backup/export")).status_code == 401
            assert (await http.post("/api/backup/import", content=b"x")).status_code == 401
            assert (await http.get("/api/skills")).status_code == 401
            assert (await http.post("/api/skills", json={})).status_code == 401
            assert (await http.get("/api/tools/status")).status_code == 401

    run(scenario())


# --------------------------------------------------------------------------- #
# export / download
# --------------------------------------------------------------------------- #
def test_export_writes_an_archive_and_lists_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            await http.post("/api/sessions", json={"title": "avant la sauvegarde"}, headers=AUTH)
            exported = await http.post("/api/backup/export", headers=AUTH)
            assert exported.status_code == 200
            body = exported.json()
            listed = (await http.get("/api/backup", headers=AUTH)).json()
            return body, listed

    body, listed = run(scenario())
    assert Path(body["path"]).is_file()
    assert body["name"].endswith(".zip")
    assert body["files"] >= 1
    assert body["wal_checkpoint"] is True
    assert "keys.json" in body["excludes"]
    assert listed["backups"][0]["name"] == body["name"]
    assert body["name"] in listed["dir"] or listed["dir"].endswith("backups")


def test_the_download_serves_the_same_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            await http.post("/api/sessions", json={}, headers=AUTH)
            name = (await http.post("/api/backup/export", headers=AUTH)).json()["name"]
            response = await http.get(f"/api/backup/download/{name}", headers=AUTH)
            return name, response

    name, response = run(scenario())
    assert response.status_code == 200
    assert "zip" in response.headers["content-type"]
    assert response.content[:2] == b"PK"


def test_a_name_outside_the_folder_is_a_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            missing = await http.get("/api/backup/download/absent.zip", headers=AUTH)
            escape = await http.get("/api/backup/download/..%2Fuh.db", headers=AUTH)
            return missing.status_code, escape.status_code

    assert run(scenario()) == (404, 404)


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def test_import_restores_an_export_and_reports_the_safety_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            # A session first, so the store exists and travels in the archive.
            await http.post("/api/sessions", json={}, headers=AUTH)
            await http.post("/api/skills", json={"title": "Avant", "instructions": "corps"}, headers=AUTH)
            exported = (await http.post("/api/backup/export", headers=AUTH)).json()
            archive = Path(exported["path"]).read_bytes()
            imported = await http.post(
                "/api/backup/import",
                content=archive,
                headers={**AUTH, "Content-Type": "application/zip"},
            )
            skills = (await http.get("/api/skills", headers=AUTH)).json()
            return imported, skills

    imported, skills = run(scenario())
    assert imported.status_code == 200
    body = imported.json()
    assert body["restored"] >= 2
    assert Path(body["safety"]).is_file()
    assert body["safety"].endswith(".zip")
    assert body["restart_required"] is True
    assert [skill["name"] for skill in skills["skills"] if skill["origin"] == "user"] == ["avant"]


def test_importing_garbage_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            not_a_zip = await http.post(
                "/api/backup/import", content=b"definitely not a zip", headers=AUTH
            )
            empty = await http.post("/api/backup/import", content=b"", headers=AUTH)
            return not_a_zip, empty

    not_a_zip, empty = run(scenario())
    assert not_a_zip.status_code == 400
    assert "zip" in not_a_zip.json()["detail"]
    assert empty.status_code == 400


def test_an_import_without_any_uh_data_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid zip holding only a readme is not a UH backup."""
    app = make_app(tmp_path, monkeypatch)
    payload = _zip_bytes({"readme.txt": b"hello"})

    async def scenario():
        async with client(app) as http:
            return await http.post("/api/backup/import", content=payload, headers=AUTH)

    response = run(scenario())
    assert response.status_code == 400
    assert "UH data" in response.json()["detail"]


def _zip_bytes(members: dict) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# skills
# --------------------------------------------------------------------------- #
def test_a_skill_is_created_and_listed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            empty = (await http.get("/api/skills", headers=AUTH)).json()
            created = await http.post(
                "/api/skills",
                json={
                    "title": "Relire un diff",
                    "description": "avant de committer",
                    "instructions": "Lis le diff, cherche les erreurs.",
                },
                headers=AUTH,
            )
            listed = (await http.get("/api/skills", headers=AUTH)).json()
            return empty, created, listed

    empty, created, listed = run(scenario())
    # UH now ships native coding skills: "empty" means no *user* skill yet.
    assert [skill for skill in empty["skills"] if skill["origin"] == "user"] == []
    assert empty["native"] >= 1, "the shipped skills are listed, with a Natif badge"
    assert created.status_code == 200
    body = created.json()
    assert Path(body["path"]).is_file()
    assert body["name"] == "relire-un-diff"
    assert listed["user"] == 1
    assert listed["skills"][0]["origin"] == "user"
    assert listed["skills"][0]["description"] == "avant de committer"


def test_a_duplicate_or_empty_skill_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            await http.post("/api/skills", json={"title": "Doublon", "instructions": "i"}, headers=AUTH)
            duplicate = await http.post(
                "/api/skills", json={"title": "Doublon", "instructions": "j"}, headers=AUTH
            )
            empty = await http.post("/api/skills", json={"title": "Sans corps"}, headers=AUTH)
            unnamed = await http.post("/api/skills", json={"instructions": "corps"}, headers=AUTH)
            return duplicate, empty, unnamed

    duplicate, empty, unnamed = run(scenario())
    assert duplicate.status_code == 400
    assert "already exists" in duplicate.json()["detail"]
    assert empty.status_code == 400
    assert unnamed.status_code == 400


# --------------------------------------------------------------------------- #
# tools
# --------------------------------------------------------------------------- #
def test_the_tools_status_reports_only_what_it_knows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return await http.get("/api/tools/status", headers=AUTH)

    response = run(scenario())
    assert response.status_code == 200
    body = response.json()
    assert {"file", "terminal", "memory", "web_search"} <= set(body["toolsets"])
    assert body["terminal"] is False
    assert body["uhb"]["connected"] is False
    assert "disabled" in body["uhb"]["reason"]
    assert body["mcp"]["supported"] is True, "the client exists now: the dot must say so"
    assert body["mcp"]["configured"] == 0, "nothing declared in this isolated home"
    assert body["mcp"]["servers"] == []
    assert "no [[mcp.servers]]" in body["mcp"]["reason"]
    assert "ollama" in body


def test_testing_an_unknown_mcp_server_is_a_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test button must say "no such server", not raise a 500 from the client."""
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return await http.post("/api/mcp/nobody/test", headers=AUTH)

    response = run(scenario())
    assert response.status_code == 404
    assert "nobody" in response.json()["detail"]


def test_a_dream_pass_runs_in_its_own_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pass takes minutes and spends a model call: never on the event loop."""
    import subprocess

    app = make_app(tmp_path, monkeypatch)
    seen: dict = {}

    class FakeProcess:
        pid = 4321
        returncode = None

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    async def scenario():
        async with client(app) as http:
            first = await http.post("/api/dreams/run", headers=AUTH, json={"budget": 5000})
            second = await http.post("/api/dreams/run", headers=AUTH)
            return first, second

    first, second = run(scenario())

    assert first.status_code == 200
    body = first.json()
    assert body["started"] is True and body["pid"] == 4321 and body["budget"] == 5000
    assert "dream" in seen["command"] and "--now" in seen["command"]
    assert "5000" in seen["command"], "the budget the user asked for is the one passed"
    assert second.status_code == 409, "a second click must not pay twice"


def test_a_bad_dream_budget_is_a_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return await http.post("/api/dreams/run", headers=AUTH, json={"budget": "lots"})

    assert run(scenario()).status_code == 400


# --------------------------------------------------------------------------- #
# the second brain, from the panel
# --------------------------------------------------------------------------- #
def test_the_panel_can_start_uhb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A panel the user can see must be switchable on without a shell command."""
    from uh import uhb_spawn
    from uh.toolsets.uhb import Uhbtoolset

    app = make_app(tmp_path, monkeypatch)
    monkeypatch.setenv("UH_UHB_URL", "http://127.0.0.1:8101")
    monkeypatch.setattr(uhb_spawn, "running", lambda: None)
    calls: list = []
    monkeypatch.setattr(uhb_spawn, "ensure", lambda **kwargs: calls.append(kwargs) or None)
    monkeypatch.setattr(
        Uhbtoolset, "status", lambda self: {"version": "0.1.2", "mode": "chat"}
    )

    async def scenario():
        async with client(app) as http:
            return await http.post("/api/uhb/start", headers=AUTH)

    response = run(scenario())

    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert calls and calls[0]["timeout"] == 25.0, "the panel waits as long as the CLI does"


def test_a_failed_start_answers_503_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh import uhb_spawn

    app = make_app(tmp_path, monkeypatch)
    monkeypatch.setenv("UH_UHB_URL", "http://127.0.0.1:8101")

    def explode(**kwargs):
        raise uhb_spawn.SpawnError("UHB exited during startup (code 1): ImportError")

    monkeypatch.setattr(uhb_spawn, "ensure", explode)

    async def scenario():
        async with client(app) as http:
            return await http.post("/api/uhb/start", headers=AUTH)

    response = run(scenario())

    assert response.status_code == 503
    assert "ImportError" in response.json()["detail"], "the cause reaches the user"


def test_start_is_refused_when_uhb_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conftest isolates UH_UHB_URL="" - that is a disabled brain, not a broken one."""
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return await http.post("/api/uhb/start", headers=AUTH)

    response = run(scenario())

    assert response.status_code == 400
    assert "disabled" in response.json()["detail"]


def test_the_panel_can_stop_uhb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from uh import uhb_spawn

    app = make_app(tmp_path, monkeypatch)
    stopped: list = []
    monkeypatch.setattr(uhb_spawn, "running", lambda: object())
    monkeypatch.setattr(uhb_spawn, "stop_running", lambda: stopped.append(True))

    async def scenario():
        async with client(app) as http:
            return await http.post("/api/uhb/stop", headers=AUTH)

    response = run(scenario())

    assert response.status_code == 200
    assert response.json()["stopped"] is True
    assert stopped == [True], "a stop that does nothing is worse than no button"


# --------------------------------------------------------------------------- #
# two versions, one port
# --------------------------------------------------------------------------- #
def test_the_page_is_stamped_with_its_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Static files come from disk, routes from the process: the stamp shows a mix."""
    from uh import __version__

    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return await http.get(f"/?token={TOKEN}")

    page = run(scenario())

    assert page.status_code == 200
    assert f"?v={__version__}" in page.text, "a cached app.js must not outlive a restart"
    assert f'data-uh-version="{__version__}"' in page.text
    assert "{{BUILD}}" not in page.text, "every placeholder is substituted"


def test_a_busy_port_does_not_become_an_opaque_exit_code() -> None:
    """A leftover `uh web` used to mean exit 3 and a popup with no reason."""
    import socket

    from uh.web.app import _port_busy, first_free_port

    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        assert _port_busy("127.0.0.1", port) is True
        free = first_free_port("127.0.0.1", port + 1)
        assert free is not None and free != port
