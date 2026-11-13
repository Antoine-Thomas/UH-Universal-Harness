"""Web interface: ASGI tests, no real model, no network.

The app is exercised with ``httpx.ASGITransport``; each test drives the async
client with ``asyncio.run`` so no async pytest plugin is required.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import keys as keys_mod  # noqa: E402
from uh.store import display_id  # noqa: E402
from uh.web.app import create_app  # noqa: E402
from uh.web.sse import parse_frames  # noqa: E402

TOKEN = "test-token-0123456789"
BASE = "http://127.0.0.1:8765"


class ScriptedProvider:
    """Streams two tokens, optionally emits one tool call first."""

    model = "stub:1b"

    def __init__(self, tool: Optional[str] = None) -> None:
        self.tool = tool
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if self.tool and self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": self.tool, "arguments": {"command": "echo hi"}}}],
            }
        for token in ("bon", "jour"):
            if on_token is not None:
                on_token(token)
        return {"role": "assistant", "content": "bonjour", "tool_calls": []}


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider=None, **kwargs):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    provider = provider or ScriptedProvider()
    return create_app(
        token=TOKEN,
        port=8765,
        workspace=tmp_path,
        provider_factory=lambda kind, model: provider,
        kind="stub",
        model="stub:1b",
        **kwargs,
    )


def run(coro):
    return asyncio.run(coro)


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


AUTH = {"Authorization": f"Bearer {TOKEN}"}


# --------------------------------------------------------------------------- #
# authentication and browser guards
# --------------------------------------------------------------------------- #
def test_api_requires_a_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            for path in ("/api/status", "/api/sessions", "/api/models", "/api/search?q=x"):
                response = await http.get(path)
                assert response.status_code == 401, path
            chat = await http.post("/api/sessions/abc/chat", json={"message": "hi"})
            assert chat.status_code == 401

    run(scenario())


def test_bad_token_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.get("/api/status", headers={"Authorization": "Bearer wrong"})
            assert response.status_code == 401

    run(scenario())


def test_index_sets_the_cookie_and_survives_a_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            denied = await http.get("/")
            assert denied.status_code == 401

            page = await http.get(f"/?token={TOKEN}")
            assert page.status_code == 200
            assert "text/html" in page.headers["content-type"]
            assert "uh_token" in page.cookies or "uh_token" in str(page.headers.get("set-cookie"))

            again = await http.get("/")  # cookie only
            assert again.status_code == 200

    run(scenario())


def test_host_and_origin_are_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            bad_host = await http.get("/api/status", headers={**AUTH, "Host": "evil.example:8765"})
            assert bad_host.status_code == 400

            bad_origin = await http.get(
                "/api/status", headers={**AUTH, "Origin": "http://evil.example:8765"}
            )
            assert bad_origin.status_code == 400

            good = await http.get("/api/status", headers={**AUTH, "Origin": BASE})
            assert good.status_code == 200

    run(scenario())


def test_security_headers_and_no_inline_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.get(f"/?token={TOKEN}")
            csp = response.headers["content-security-policy"]
            assert "default-src 'none'" in csp
            assert "script-src 'self'" in csp
            assert "unsafe-inline" not in csp
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["x-content-type-options"] == "nosniff"

            html = response.text
            assert "<script type=\"module\" src=\"/static/app.js\">" in html
            assert html.count("<script") == 1  # no inline block
            assert "onclick=" not in html and "style=" not in html

    run(scenario())


def test_static_assets_are_served_with_security_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            js = await http.get("/static/app.js")
            assert js.status_code == 200
            assert "javascript" in js.headers["content-type"]
            assert "default-src 'none'" in js.headers["content-security-policy"]

            css = await http.get("/static/style.css")
            assert css.status_code == 200
            assert "text/css" in css.headers["content-type"]

            page = await http.get(f"/?token={TOKEN}")
            assert "/static/app.js" in page.text and "/static/style.css" in page.text

    run(scenario())


def test_static_files_are_in_the_package() -> None:
    from uh.web import app as web_app

    for name in ("index.html", "app.js", "style.css"):
        assert (web_app.STATIC_DIR / name).is_file(), name


# --------------------------------------------------------------------------- #
# sessions CRUD, messages, search
# --------------------------------------------------------------------------- #
def test_sessions_crud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "hello"}, headers=AUTH)).json()
            session_id = created["session"]["id"]
            assert created["session"]["short_id"] == display_id(session_id)

            listing = (await http.get("/api/sessions", headers=AUTH)).json()
            assert [s["id"] for s in listing["sessions"]] == [session_id]

            renamed = (
                await http.patch(f"/api/sessions/{session_id}", json={"title": "renamed"}, headers=AUTH)
            ).json()
            assert renamed["title"] == "renamed"

            messages = (await http.get(f"/api/sessions/{session_id}/messages", headers=AUTH)).json()
            assert messages["messages"] == []
            assert messages["model"] == "stub:1b"

            deleted = (await http.delete(f"/api/sessions/{session_id}", headers=AUTH)).json()
            assert deleted["deleted"] is True
            assert (await http.get("/api/sessions", headers=AUTH)).json()["sessions"] == []

    run(scenario())


def test_unknown_session_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            assert (await http.get("/api/sessions/nope/messages", headers=AUTH)).status_code == 404
            assert (await http.post("/api/sessions/nope/stop", headers=AUTH)).status_code == 404

    run(scenario())


# --------------------------------------------------------------------------- #
# SSE chat
# --------------------------------------------------------------------------- #
def test_chat_streams_tokens_and_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session_id = (
                await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)
            ).json()["session"]["id"]

            response = await http.post(
                f"/api/sessions/{session_id}/chat", json={"message": "bonjour"}, headers=AUTH
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")

            frames = parse_frames(response.text)
            names = [name for name, _ in frames]
            assert names[0] == "open"
            assert names.count("token") == 2
            assert "answer" in names
            assert names[-1] == "done"
            assert "".join(data.get("text", "") for name, data in frames if name == "token") == "bonjour"

            messages = (await http.get(f"/api/sessions/{session_id}/messages", headers=AUTH)).json()
            roles = [m["role"] for m in messages["messages"]]
            assert roles == ["user", "assistant"]

    run(scenario())


def test_tool_events_and_terminal_disabled_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider(tool="terminal")
    app = make_app(tmp_path, monkeypatch, provider=provider)

    async def scenario():
        async with client(app) as http:
            status = (await http.get("/api/status", headers=AUTH)).json()
            assert status["terminal"] is False

            session_id = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]["id"]
            response = await http.post(
                f"/api/sessions/{session_id}/chat", json={"message": "ls"}, headers=AUTH
            )
            frames = dict(parse_frames(response.text))
            assert "tool_call" in frames
            assert frames["tool_call"]["name"] == "terminal"
            result = frames["tool_result"]
            assert result["name"] == "terminal"
            assert "unknown tool" in result["content"]

    run(scenario())


def test_terminal_can_be_enabled_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider(tool="terminal")
    app = make_app(tmp_path, monkeypatch, provider=provider, allow_terminal=True)

    async def scenario():
        async with client(app) as http:
            status = (await http.get("/api/status", headers=AUTH)).json()
            assert status["terminal"] is True
            session_id = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]["id"]
            response = await http.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "echo hi"},
                headers=AUTH,
            )
            frames = dict(parse_frames(response.text))
            assert "unknown tool" not in frames["tool_result"]["content"]

    run(scenario())


def test_stop_without_a_running_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session_id = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]["id"]
            payload = (await http.post(f"/api/sessions/{session_id}/stop", headers=AUTH)).json()
            assert payload["stopped"] is False

    run(scenario())


def test_search_returns_snippets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session_id = (await http.post("/api/sessions", json={"title": "find me"}, headers=AUTH)).json()[
                "session"
            ]["id"]
            await http.post(
                f"/api/sessions/{session_id}/chat", json={"message": "le clavier est bleu"}, headers=AUTH
            )
            hits = (await http.get("/api/search?q=clavier", headers=AUTH)).json()["hits"]
            assert hits and "clavier" in hits[0]["snippet"]
            assert hits[0]["short_id"] == display_id(session_id)

    run(scenario())


# --------------------------------------------------------------------------- #
# models, commands, secrets
# --------------------------------------------------------------------------- #
def test_models_and_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            models = (await http.get("/api/models", headers=AUTH)).json()
            assert models["model"] == "stub:1b"
            assert models["provider"] == "stub"  # the provider itself is injected
            assert {"ollama", "omnirouter"} <= set(models["providers"])

            commands = (await http.get("/api/commands", headers=AUTH)).json()["commands"]
            names = {command["name"] for command in commands}
            assert {"help", "model", "sessions", "exit"} <= names

            switched = (
                await http.post("/api/model", json={"model": "autre:7b"}, headers=AUTH)
            ).json()
            assert switched["model"] == "autre:7b"
            assert (await http.get("/api/status", headers=AUTH)).json()["model"] == "autre:7b"

    run(scenario())


def test_no_provider_key_is_ever_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    secret = "test-key-not-a-real-secret"
    keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "llama", secret)

    async def scenario():
        async with client(app) as http:
            for path in ("/api/status", "/api/models", "/api/commands"):
                body = (await http.get(path, headers=AUTH)).text
                assert secret not in body, path

    run(scenario())


def test_chat_without_message_is_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session_id = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]["id"]
            response = await http.post(
                f"/api/sessions/{session_id}/chat", json={"message": "  "}, headers=AUTH
            )
            assert response.status_code == 400

    run(scenario())


def test_session_model_override_is_per_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            first = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]["id"]
            second = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]["id"]

            await http.post(
                "/api/model",
                json={"model": "perso:3b", "session_id": first},
                headers=AUTH,
            )
            a = (await http.get(f"/api/sessions/{first}/messages", headers=AUTH)).json()
            b = (await http.get(f"/api/sessions/{second}/messages", headers=AUTH)).json()
            assert a["model"] == "perso:3b"
            assert b["model"] == "stub:1b"

    run(scenario())


def test_folders_are_metadata_on_the_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]

            filed = (
                await http.patch(
                    f"/api/sessions/{session_id}", json={"folder": "Projets"}, headers=AUTH
                )
            ).json()
            assert filed["folder"] == "Projets"
            assert filed["title"] == "t", "the old shape still answers id/title"
            assert filed["session"]["metadata"]["folder"] == "Projets"

            listing = (await http.get("/api/sessions", headers=AUTH)).json()
            assert listing["sessions"][0]["metadata"]["folder"] == "Projets"

            unfiled = (
                await http.patch(
                    f"/api/sessions/{session_id}", json={"folder": ""}, headers=AUTH
                )
            ).json()
            assert unfiled["folder"] == ""
            assert "folder" not in unfiled["session"]["metadata"]


def test_patch_refuses_an_empty_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]

            idle = await http.patch(f"/api/sessions/{session_id}", json={}, headers=AUTH)
            assert idle.status_code == 400, "a typo in the client must not be a silent no-op"
            unknown = await http.patch("/api/sessions/nope", json={"folder": "x"}, headers=AUTH)
            assert unknown.status_code == 404


def test_a_deleted_session_takes_its_messages_with_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What the trash icon in the sidebar relies on: no orphan rows to reload."""
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]
            await http.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "bonjour"},
                headers=AUTH,
            )
            assert (await http.get(f"/api/sessions/{session_id}/messages", headers=AUTH)).json()[
                "messages"
            ]

            assert (
                await http.delete(f"/api/sessions/{session_id}", headers=AUTH)
            ).json()["deleted"] is True

            assert (
                await http.get(f"/api/sessions/{session_id}/messages", headers=AUTH)
            ).status_code == 404
            assert (await http.get("/api/sessions", headers=AUTH)).json()["sessions"] == []

    run(scenario())


def test_folders_can_be_listed_renamed_and_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            made = []
            for title in ("a", "b", "c"):
                created = (await http.post("/api/sessions", json={"title": title}, headers=AUTH)).json()
                made.append(created["session"]["id"])
            await http.patch(f"/api/sessions/{made[0]}", json={"folder": "Projets"}, headers=AUTH)
            await http.patch(f"/api/sessions/{made[1]}", json={"folder": "Projets"}, headers=AUTH)
            await http.patch(f"/api/sessions/{made[2]}", json={"folder": "Autre"}, headers=AUTH)

            listed = (await http.get("/api/folders", headers=AUTH)).json()
            assert listed["folders"] == [
                {"name": "Autre", "count": 1},
                {"name": "Projets", "count": 2},
            ]

            renamed = (
                await http.patch("/api/folders/Projets", json={"name": "Maison"}, headers=AUTH)
            ).json()
            assert renamed == {"name": "Maison", "previous": "Projets", "moved": 2}
            sessions = (await http.get("/api/sessions", headers=AUTH)).json()["sessions"]
            folders = sorted((s["metadata"].get("folder") or "") for s in sessions)
            assert folders == ["Autre", "Maison", "Maison"]

            cleared = (await http.delete("/api/folders/Autre", headers=AUTH)).json()
            assert cleared == {"deleted": "Autre", "unfiled": 1}
            assert [f["name"] for f in (await http.get("/api/folders", headers=AUTH)).json()["folders"]] == [
                "Maison"
            ]
            after = (await http.get("/api/sessions", headers=AUTH)).json()["sessions"]
            assert sorted((s["metadata"].get("folder") or "") for s in after) == ["", "Maison", "Maison"]
            assert len(after) == 3, "deleting a folder must never delete a session"

    run(scenario())


def test_folder_routes_refuse_what_they_cannot_represent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]
            await http.patch(f"/api/sessions/{session_id}", json={"folder": "Projets"}, headers=AUTH)

            empty = await http.patch("/api/folders/Projets", json={"name": "  "}, headers=AUTH)
            assert empty.status_code == 400
            same = await http.patch("/api/folders/Projets", json={"name": "Projets"}, headers=AUTH)
            assert same.status_code == 400
            slash = await http.patch("/api/folders/Projets", json={"name": "a/b"}, headers=AUTH)
            assert slash.status_code == 400, "a name with a slash could never be reached by URL"
            unknown = await http.patch("/api/folders/Nope", json={"name": "x"}, headers=AUTH)
            assert unknown.status_code == 404
            missing = await http.delete("/api/folders/Nope", headers=AUTH)
            assert missing.status_code == 404

    run(scenario())


def test_delete_one_message_through_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]
            await http.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "bonjour"},
                headers=AUTH,
            )
            messages = (
                await http.get(f"/api/sessions/{session_id}/messages", headers=AUTH)
            ).json()["messages"]
            assert len(messages) >= 2
            doomed = messages[-1]["id"]

            answer = await http.delete(
                f"/api/sessions/{session_id}/messages/{doomed}", headers=AUTH
            )
            assert answer.status_code == 200
            body = answer.json()
            assert body["deleted"] == doomed
            assert body["remaining"] == len(messages) - 1

            after = (
                await http.get(f"/api/sessions/{session_id}/messages", headers=AUTH)
            ).json()["messages"]
            assert [m["id"] for m in after] == [m["id"] for m in messages[:-1]]

            again = await http.delete(
                f"/api/sessions/{session_id}/messages/{doomed}", headers=AUTH
            )
            assert again.status_code == 404, "deleting twice is a not-found, not a crash"
            unknown_session = await http.delete(
                f"/api/sessions/nope/messages/{doomed}", headers=AUTH
            )
            assert unknown_session.status_code == 404

    run(scenario())


def test_upload_stores_the_file_and_reports_its_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]

            small = await http.post(
                f"/api/sessions/{session_id}/upload?name=notes.md",
                content=b"# Titre\nune note courte",
                headers={**AUTH, "Content-Type": "application/octet-stream"},
            )
            assert small.status_code == 200
            attachment = small.json()["attachment"]
            assert attachment["status"] == "inlined"
            assert attachment["kind"] == "text"
            assert Path(attachment["path"]).read_bytes() == b"# Titre\nune note courte"

            big = await http.post(
                f"/api/sessions/{session_id}/upload?name=journal.txt",
                content=b"x" * 9000,
                headers={**AUTH, "Content-Type": "application/octet-stream"},
            )
            assert big.json()["attachment"]["status"] == "referenced"

            image = await http.post(
                f"/api/sessions/{session_id}/upload?name=photo.png",
                content=b"\x89PNG" + b"0" * 40,
                headers={**AUTH, "Content-Type": "application/octet-stream"},
            )
            assert image.json()["attachment"]["kind"] == "image"
            assert "cannot see it" in image.json()["attachment"]["note"]

    run(scenario())


def test_upload_refuses_a_traversal_name_and_a_huge_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh import attachments

    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]

            escaped = await http.post(
                f"/api/sessions/{session_id}/upload?name=../../evil.txt",
                content=b"x",
                headers={**AUTH, "Content-Type": "application/octet-stream"},
            )
            assert escaped.status_code == 200
            stored = Path(escaped.json()["attachment"]["path"])
            assert stored.parent == attachments.session_dir(session_id), "the name is stripped"
            assert stored.name == "evil.txt"

            monkeypatch.setattr(attachments, "MAX_BYTES", 10)
            too_big = await http.post(
                f"/api/sessions/{session_id}/upload?name=big.bin",
                content=b"y" * 11,
                headers={**AUTH, "Content-Type": "application/octet-stream"},
            )
            assert too_big.status_code == 413

            unknown = await http.post(
                "/api/sessions/nope/upload?name=a.txt",
                content=b"x",
                headers={**AUTH, "Content-Type": "application/octet-stream"},
            )
            assert unknown.status_code == 404

    run(scenario())


def test_chat_injects_a_small_attachment_and_references_a_big_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider()
    app = make_app(tmp_path, monkeypatch, provider=provider)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]
            small = (
                await http.post(
                    f"/api/sessions/{session_id}/upload?name=notes.md",
                    content=b"CONTENU-INJECTE",
                    headers={**AUTH, "Content-Type": "application/octet-stream"},
                )
            ).json()["attachment"]
            big = (
                await http.post(
                    f"/api/sessions/{session_id}/upload?name=gros.txt",
                    content=b"z" * 9000,
                    headers={**AUTH, "Content-Type": "application/octet-stream"},
                )
            ).json()["attachment"]

            reply = await http.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "resume", "attachments": [small["path"], big["path"]]},
                headers=AUTH,
            )
            assert reply.status_code == 200

            stored = (
                await http.get(f"/api/sessions/{session_id}/messages", headers=AUTH)
            ).json()["messages"]
            user = next(m for m in stored if m["role"] == "user")
            assert "CONTENU-INJECTE" in user["content"]
            assert "[inlined]" in user["content"]
            assert "[referenced]" in user["content"]
            assert big["path"] in user["content"]
            assert "z" * 9000 not in user["content"]

    run(scenario())


def test_chat_refuses_an_attachment_from_another_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]
            outside = tmp_path / "secret.txt"
            outside.write_text("secret", encoding="utf-8")

            answer = await http.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "lis", "attachments": [str(outside)]},
                headers=AUTH,
            )
            assert answer.status_code == 400
            assert "not an attachment" in answer.json()["detail"]

    run(scenario())


def test_deleting_a_session_removes_its_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh import attachments

    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]
            uploaded = (
                await http.post(
                    f"/api/sessions/{session_id}/upload?name=a.txt",
                    content=b"bonjour",
                    headers={**AUTH, "Content-Type": "application/octet-stream"},
                )
            ).json()["attachment"]
            path = Path(uploaded["path"])
            assert path.is_file()

            answer = (await http.delete(f"/api/sessions/{session_id}", headers=AUTH)).json()

            assert answer["deleted"] is True
            assert answer["files_removed"] == 1
            assert not path.exists(), "a deleted conversation must not leave its files"
            assert not attachments.session_dir(session_id).exists()

    run(scenario())


def test_capabilities_reports_the_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh import hardware

    found = hardware.HardwareProfile(
        ram_gb=6.0, cpu_cores=4, gpu=None, os_name="Windows", arch="AMD64", tier="medium"
    )
    monkeypatch.setattr(hardware, "profile", lambda: found)
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            data = (await http.get("/api/system/capabilities", headers=AUTH)).json()

            assert data["tier"] == "medium"
            assert data["recommended_model"] == "qwen2.5:1.5b"
            assert data["recommended_budget"] == 6000
            assert data["local_possible"] is True
            assert "medium" in data["sentence"]
            assert data["ram_gb"] == 6.0

    run(scenario())


def test_a_model_pull_runs_in_the_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    import uh.providers.ollama as ollama_mod

    release = threading.Event()
    calls: list[str] = []

    def fake_pull(name, on_event, **kwargs):
        calls.append(name)
        release.wait(5.0)

    monkeypatch.setattr(ollama_mod, "pull_model", fake_pull)
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            empty = await http.post("/api/models/pull", json={}, headers=AUTH)
            assert empty.status_code == 400

            started = await http.post(
                "/api/models/pull", json={"name": "qwen2.5:1.5b"}, headers=AUTH
            )
            assert started.status_code == 200
            assert started.json()["started"] is True

            deadline = time.monotonic() + 3.0
            listed: list[str] = []
            while time.monotonic() < deadline:
                listed = (await http.get("/api/models", headers=AUTH)).json()["pulling"]
                if listed:
                    break
                time.sleep(0.05)
            assert listed == ["qwen2.5:1.5b"], "the UI polls this to show progress"

            release.set()

    run(scenario())
    assert calls == ["qwen2.5:1.5b"]


def test_uhb_status_reports_absence_without_a_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh.toolsets import uhb as uhb_mod

    monkeypatch.setattr(uhb_mod, "resolve_url", lambda: None)
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            data = (await http.get("/api/uhb/status", headers=AUTH)).json()
            assert data["connected"] is False
            assert "disabled" in data["reason"]

    run(scenario())


def test_uhb_status_is_proxied_through_uh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The browser asks UH; only UH speaks to UHB on loopback."""
    from uh.toolsets import uhb as uhb_mod

    summary = {
        "connected": True,
        "version": "0.4.2",
        "mode": "dev",
        "harness": "uh 1.1.0",
        "providers": ["deepseek", "ollama"],
        "modes": ["chat", "dev"],
    }
    monkeypatch.setattr(uhb_mod, "resolve_url", lambda: "http://127.0.0.1:8101")
    monkeypatch.setattr(uhb_mod.Uhbtoolset, "status", lambda self: dict(summary))
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            data = (await http.get("/api/uhb/status", headers=AUTH)).json()
            assert data["connected"] is True
            assert data["mode"] == "dev"
            assert data["providers"] == ["deepseek", "ollama"]

    run(scenario())


def test_uhb_route_returns_the_plan_or_a_clear_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh.toolsets import uhb as uhb_mod

    plan = {
        "mode": "chat",
        "complexity": "trivial",
        "score": 1,
        "signals": {},
        "allow_paid": False,
        "reason": "short prompt",
        "chosen": "deepseek/deepseek-flash",
        "chain": [{"ref": "deepseek/deepseek-flash", "tier": "eco", "model_id": "deepseek-flash"}],
    }
    monkeypatch.setattr(uhb_mod, "resolve_url", lambda: "http://127.0.0.1:8101")
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            empty = await http.post("/api/uhb/route", json={}, headers=AUTH)
            assert empty.status_code == 400

            monkeypatch.setattr(uhb_mod.Uhbtoolset, "route", lambda self, p, m, c: dict(plan))
            ok = await http.post("/api/uhb/route", json={"prompt": "salut"}, headers=AUTH)
            assert ok.status_code == 200
            assert ok.json()["chosen"] == "deepseek/deepseek-flash"
            assert ok.json()["chain"][0]["tier"] == "eco"

            monkeypatch.setattr(
                uhb_mod.Uhbtoolset,
                "route",
                lambda self, p, m, c: {"error": "uhb unreachable", "kind": "transport"},
            )
            down = await http.post("/api/uhb/route", json={"prompt": "salut"}, headers=AUTH)
            assert down.status_code == 503
            assert "unreachable" in down.json()["detail"]

    run(scenario())


def test_a_slash_command_in_the_web_chat_runs_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chip inserts "/tools"; sending it must not ask the model about the string."""
    provider = ScriptedProvider()
    app = make_app(tmp_path, monkeypatch, provider=provider)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]

            reply = await http.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "/tools"},
                headers=AUTH,
            )
            assert reply.status_code == 200
            assert "active toolsets" in reply.text
            assert provider.calls == 0, "a command is not a prompt"

            stored = (
                await http.get(f"/api/sessions/{session_id}/messages", headers=AUTH)
            ).json()["messages"]
            assert stored == [], "a command does not become a stored turn"

    run(scenario())


def _wiki_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "wiki"
    (root / "concepts").mkdir(parents=True)
    (root / "index.md").write_text("# Index\nles concepts de UH", encoding="utf-8")
    (root / "concepts" / "rag.md").write_text("# RAG\nrecherche hybride", encoding="utf-8")
    monkeypatch.setenv("UH_WIKI_DIR", str(root))
    return root


def test_memory_sources_lists_the_wiki_and_says_what_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wiki_fixture(tmp_path, monkeypatch)
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            data = (await http.get("/api/memory/sources", headers=AUTH)).json()

            assert data["wiki"]["available"] is True
            assert data["wiki"]["notes"] == 2
            assert data["siyuan"]["available"] is False, "UH does not pretend to read SiYuan"
            assert data["rag"]["available"] in (True, False)
            assert data["sessions"]["available"] is True

    run(scenario())


def test_memory_wiki_lists_and_serves_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wiki_fixture(tmp_path, monkeypatch)
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            listing = (await http.get("/api/memory/wiki", headers=AUTH)).json()
            assert listing["available"] is True
            assert [note["path"] for note in listing["notes"]] == [
                "concepts/rag.md",
                "index.md",
            ]

            note = (await http.get("/api/memory/wiki/concepts/rag.md", headers=AUTH)).json()
            assert "recherche hybride" in note["text"]

            escaped = await http.get("/api/memory/wiki/..%2F..%2Fsecret.md", headers=AUTH)
            assert escaped.status_code in (404, 400), "traversal is refused, not served"

            missing = await http.get("/api/memory/wiki/nexiste.md", headers=AUTH)
            assert missing.status_code == 404

    run(scenario())


def test_memory_search_covers_the_wiki_and_the_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wiki_fixture(tmp_path, monkeypatch)
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]
            await http.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "parle moi de la recherche hybride"},
                headers=AUTH,
            )

            data = (
                await http.get("/api/memory/search?q=recherche", headers=AUTH)
            ).json()
            kinds = {hit["kind"] for hit in data["hits"]}
            assert kinds == {"wiki", "session"}, kinds

            empty = await http.get("/api/memory/search?q=", headers=AUTH)
            assert empty.status_code == 400

    run(scenario())


def test_memory_rag_and_siyuan_answer_without_a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh import memory_view

    monkeypatch.setattr(memory_view, "_uhb_checkout", lambda: None)
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            rag = (await http.get("/api/memory/rag", headers=AUTH)).json()
            assert rag["available"] is False
            assert "uhb/services/rag" in rag["reason"]

            siyuan = (await http.get("/api/memory/siyuan", headers=AUTH)).json()
            assert siyuan["available"] is False
            assert siyuan["reads_via"] == "uhb"

    run(scenario())


def test_a_turn_records_a_decision_for_the_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The panel reads a record written at the end of a real turn."""
    provider = ScriptedProvider()
    app = make_app(tmp_path, monkeypatch, provider=provider)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "d"}, headers=AUTH)).json()
            session_id = created["session"]["id"]

            empty = (await http.get(f"/api/sessions/{session_id}/decision", headers=AUTH)).json()
            assert empty["available"] is False, "no turn yet, no invented numbers"

            await http.post(
                f"/api/sessions/{session_id}/chat", json={"message": "bonjour"}, headers=AUTH
            )

            data = (
                await http.get(f"/api/sessions/{session_id}/decision", headers=AUTH)
            ).json()
            assert data["available"] is True
            assert data["model"]["model"]
            assert data["turn"]["duration_ms"] >= 0
            assert data["context"]["budget"] > 0
            assert data["uhb_consulted"] is False, "the stub never called uhb_route"
            assert data["prompt"]["note"]

            missing = await http.get("/api/sessions/inconnu/decision", headers=AUTH)
            assert missing.status_code == 404

    run(scenario())


def _uhb_transport(handler) -> None:
    """Point the memory actions at a fake UHB (no socket, no server)."""
    import httpx

    from uh import memory_actions

    memory_actions.set_client_factory(
        lambda timeout: httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)
    )


@pytest.fixture(autouse=True)
def _restore_memory_factory():
    import httpx

    from uh import memory_actions

    yield
    memory_actions.set_client_factory(lambda timeout: httpx.Client(timeout=timeout))


def test_rag_reload_is_proxied_and_reports_the_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh.toolsets import uhb as uhb_mod

    monkeypatch.setattr(uhb_mod, "resolve_url", lambda: "http://127.0.0.1:8101")
    _uhb_transport(
        lambda request: httpx.Response(200, json={"reloaded": True, "service": {"fragments": 4893}})
    )
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            answer = await http.post("/api/memory/rag/reload", headers=AUTH)
            assert answer.status_code == 200
            body = answer.json()
            assert body["reloaded"] is True
            assert body["service"]["fragments"] == 4893
            assert "at" in body

    run(scenario())


def test_rag_reload_keeps_the_409_and_its_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh.toolsets import uhb as uhb_mod

    monkeypatch.setattr(uhb_mod, "resolve_url", lambda: "http://127.0.0.1:8101")
    _uhb_transport(
        lambda request: httpx.Response(409, json={"error": {"message": "the RAG service is down"}})
    )
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            answer = await http.post("/api/memory/rag/reload", headers=AUTH)
            assert answer.status_code == 409
            body = answer.json()
            assert body["kind"] == "service_offline"
            assert "start it with" in body["hint"]

    run(scenario())


def test_rag_reload_without_uhb_is_a_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh.toolsets import uhb as uhb_mod

    monkeypatch.setattr(uhb_mod, "resolve_url", lambda: None)
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            answer = await http.post("/api/memory/rag/reload", headers=AUTH)
            assert answer.status_code == 502
            assert "UHB" in answer.json()["detail"]

    run(scenario())


def test_siyuan_blocks_are_proxied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from uh.toolsets import uhb as uhb_mod

    seen: dict = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={"count": 1, "path": "veille", "blocks": [
                {"id": "a", "type": "h", "hpath": "/veille", "content": "Titre"}
            ]},
        )

    monkeypatch.setattr(uhb_mod, "resolve_url", lambda: "http://127.0.0.1:8101")
    _uhb_transport(handler)
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            answer = await http.get("/api/memory/siyuan/blocks?path=veille&limit=20", headers=AUTH)
            assert answer.status_code == 200
            assert answer.json()["count"] == 1
            assert "path=veille" in seen["url"]

    run(scenario())


@pytest.mark.parametrize(
    "upstream,expected,kind,fragment",
    [
        (400, 400, "config_missing", "SIYUAN_TOKEN"),
        (401, 401, "auth", "check SIYUAN_TOKEN"),
        (502, 502, "transport", "unreachable"),
    ],
)
def test_siyuan_blocks_map_their_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upstream: int, expected: int, kind: str, fragment: str
) -> None:
    from uh.toolsets import uhb as uhb_mod

    monkeypatch.setattr(uhb_mod, "resolve_url", lambda: "http://127.0.0.1:8101")
    _uhb_transport(
        lambda request: httpx.Response(upstream, json={"error": {"message": "SiYuan is not reachable at http://x"}})
    )
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            answer = await http.get("/api/memory/siyuan/blocks", headers=AUTH)
            assert answer.status_code == expected
            body = answer.json()
            assert body["kind"] == kind
            assert fragment in (body.get("hint") or "") + (body.get("error") or "")

    run(scenario())


def test_the_rag_state_comes_from_uhb_when_it_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The disk scan sees a checkout; UHB sees the service that holds the index."""
    from uh.toolsets import uhb as uhb_mod

    monkeypatch.setattr(uhb_mod, "resolve_url", lambda: "http://127.0.0.1:8101")
    _uhb_transport(
        lambda request: httpx.Response(
            200,
            json={"built": True, "source": "service", "chunks": 4893, "index_bytes": 0,
                  "service": True},
        )
    )
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            data = (await http.get("/api/memory/sources", headers=AUTH)).json()
            assert data["rag"]["chunks"] == 4893
            assert data["rag"]["via"] == "uhb", "the panel must say where the number came from"

    run(scenario())


def test_a_dead_uhb_leaves_the_local_disk_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh.toolsets import uhb as uhb_mod

    monkeypatch.setattr(uhb_mod, "resolve_url", lambda: "http://127.0.0.1:8101")
    _uhb_transport(lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused", request=request)))
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            data = (await http.get("/api/memory/sources", headers=AUTH)).json()
            assert "via" not in data["rag"], "no service answer, no claim about the service"
            assert "available" in data["rag"]

    run(scenario())
