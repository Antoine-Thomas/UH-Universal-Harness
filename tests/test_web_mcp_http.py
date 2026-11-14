"""P14.1 through the cockpit API: save, test, state, approval.

The transport is proven in ``test_mcp_http.py``; this file proves that the routes
the panel calls reach it, that a save answers with a real connection state, and
that a remote tool cannot run without an explicit click.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402
from fake_mcp_http import FakeMCP  # noqa: E402

from uh import mcp_http  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-p141"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MCP_TOKEN = "positron-secret-token-a1b2"


def run(coro):
    return asyncio.run(coro)


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=tmp_path,
        provider_factory=lambda kind, model: _Stub(),
        kind="stub",
        model="stub:1b",
    )


class _Stub:
    model = "stub:1b"
    streaming = True


async def call(app, method: str, path: str, json_body=None, headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
        return await http.request(method, path, headers=headers or AUTH, json=json_body)


@pytest.fixture(autouse=True)
def clean_registry():
    mcp_http.shutdown()
    yield
    mcp_http.shutdown()


def test_saving_a_server_tests_it_in_the_same_gesture(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    with FakeMCP() as fake:

        async def scenario():
            return await call(
                app,
                "POST",
                "/api/mcp/http",
                json_body={"name": "positron", "url": fake.url, "token": MCP_TOKEN},
            )

        response = run(scenario())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "connected", body
    assert body["connection"]["tools"] == ["read", "write", "list"]
    assert body["connection"]["server"] == "fake-positron"
    assert body["connection"]["duration_ms"] >= 0
    assert body["token"] == "••••a1b2"
    assert MCP_TOKEN not in response.text
    config = (tmp_path / "home" / "config.toml").read_text(encoding="utf-8")
    assert MCP_TOKEN not in config


def test_the_test_button_reaches_the_http_transport(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    with FakeMCP() as fake:

        async def scenario():
            await call(
                app,
                "POST",
                "/api/mcp/http",
                json_body={"name": "positron", "url": fake.url},
            )
            return await call(app, "POST", "/api/mcp/positron/test")

        response = run(scenario())

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "connected"


def test_a_refused_token_is_a_state_not_a_500(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    with FakeMCP(token="the-real-one") as fake:

        async def scenario():
            return await call(
                app,
                "POST",
                "/api/mcp/http",
                json_body={"name": "positron", "url": fake.url, "token": "wrong"},
            )

        response = run(scenario())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "auth"
    assert "jeton" in body["connection"]["advice"]


def test_the_catalog_shows_the_live_state_and_the_masked_token(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    with FakeMCP() as fake:

        async def scenario():
            await call(
                app,
                "POST",
                "/api/mcp/http",
                json_body={"name": "positron", "url": fake.url, "token": MCP_TOKEN},
            )
            return await call(app, "GET", "/api/mcp/cloud")

        response = run(scenario())

    assert response.status_code == 200
    (row,) = response.json()["http_servers"]
    assert row["state"] == "connected"
    assert row["tools"] == ["read", "write", "list"]
    assert row["token"] == "••••a1b2"
    assert MCP_TOKEN not in response.text


def test_tools_status_counts_http_entries(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    with FakeMCP() as fake:

        async def scenario():
            await call(
                app,
                "POST",
                "/api/mcp/http",
                json_body={"name": "positron", "url": fake.url},
            )
            return await call(app, "GET", "/api/tools/status")

        response = run(scenario())

    assert response.status_code == 200
    mcp = response.json()["mcp"]
    assert mcp["configured"] == 1
    assert mcp["http"][0]["state"] == "connected"
    assert "1/1" in mcp["http_reason"]


def test_an_unreachable_server_stays_a_state_after_a_save(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    with FakeMCP() as fake:
        url = fake.url

    async def scenario():
        return await call(
            app, "POST", "/api/mcp/http", json_body={"name": "positron", "url": url}
        )

    body = run(scenario()).json()
    assert body["state"] == "unreachable"
    assert "démarré" in body["connection"]["advice"]


# --------------------------------------------------------------------------- #
# approval: the remote tool is gated, and the route sees the ticket
# --------------------------------------------------------------------------- #
def test_an_approval_ticket_can_be_read_and_answered(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    import threading

    gate = mcp_http.GATE
    seen = []
    worker = threading.Thread(
        target=lambda: gate.ask(
            "positron", "write", {"path": "x"}, notify=seen.append, timeout=5
        )
    )
    worker.start()
    for _ in range(100):
        if gate.pending():
            break
        asyncio.run(asyncio.sleep(0.02))
    (ticket,) = gate.pending()

    async def scenario():
        listed = await call(app, "GET", "/api/mcp/approvals")
        answered = await call(
            app, "POST", f"/api/mcp/approvals/{ticket['id']}", json_body={"allow": True}
        )
        gone = await call(
            app, "POST", f"/api/mcp/approvals/{ticket['id']}", json_body={"allow": True}
        )
        return listed, answered, gone

    listed, answered, gone = run(scenario())
    worker.join(timeout=5)

    assert listed.json()["pending"][0]["tool"] == "write"
    assert answered.status_code == 200 and answered.json()["resolved"] is True
    assert gone.status_code == 404, "a ticket is answered once"


def test_no_tool_runs_without_an_approver(tmp_path, monkeypatch) -> None:
    """The console (and any caller without a UI) gets no remote tools at all."""
    with FakeMCP() as fake:
        fake_cfg = tmp_path / "home"
        fake_cfg.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("UH_HOME", str(fake_cfg))
        (fake_cfg / "config.toml").write_text(
            '[[mcp.servers]]\nname = "positron"\ntransport = "http"\n'
            f'url = "{fake.url}"\n',
            encoding="utf-8",
        )
        assert mcp_http.build_toolsets() == {}
        assert json.dumps(mcp_http.status()) .count("positron") >= 1
