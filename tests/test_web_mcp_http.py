"""P14.1 through the cockpit API: save, test, state, approval.

The transport is proven in ``test_mcp_http.py``; this file proves that the routes
the panel calls reach it, that a save answers with a real connection state, and
that a remote tool cannot run without an explicit click.
"""

from __future__ import annotations

import asyncio
import json
import threading
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
        return await call(app, "POST", "/api/mcp/http", json_body={"name": "positron", "url": url})

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
        target=lambda: gate.ask("positron", "write", {"path": "x"}, notify=seen.append, timeout=5)
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
        return listed, answered

    listed, answered = run(scenario())
    # The worker has finished its whole cycle here, so the ticket is gone from
    # ``pending`` for good: the second POST below cannot race with a ``finally``.
    worker.join(timeout=5)

    async def again():
        return await call(
            app, "POST", f"/api/mcp/approvals/{ticket['id']}", json_body={"allow": True}
        )

    gone = run(again())

    assert listed.json()["pending"][0]["tool"] == "write"
    assert answered.status_code == 200 and answered.json()["resolved"] is True
    assert gone.status_code == 404, "a ticket is answered once"


def test_a_second_answer_is_refused_while_the_worker_has_not_returned(
    tmp_path, monkeypatch
) -> None:
    """The race the loaded runner caught: answer, then answer again immediately.

    The first POST sets ``done``; the worker has not been scheduled back yet, so the
    ticket is still in ``pending``. Without the ``done`` check the route resolved it
    a second time and answered 200 - the CI cell ubuntu/py3.10 failed on exactly
    this, while a fast laptop always passed.
    """
    app = make_app(tmp_path, monkeypatch)

    gate = mcp_http.GATE
    seen = []
    worker = threading.Thread(
        target=lambda: gate.ask("positron", "write", {"path": "x"}, notify=seen.append, timeout=5)
    )
    worker.start()
    for _ in range(100):
        if gate.pending():
            break
        asyncio.run(asyncio.sleep(0.02))
    (ticket,) = gate.pending()

    async def twice():
        first = await call(
            app, "POST", f"/api/mcp/approvals/{ticket['id']}", json_body={"allow": True}
        )
        second = await call(
            app, "POST", f"/api/mcp/approvals/{ticket['id']}", json_body={"allow": True}
        )
        return first, second

    first, second = run(twice())
    worker.join(timeout=5)

    assert first.status_code == 200, "the first answer runs the call"
    assert second.status_code == 404, "the same ticket cannot be answered twice"


def test_resolve_refuses_a_ticket_whose_answer_is_already_decided() -> None:
    """Deterministic version of the race: the state between ``done.set()`` and the
    worker's ``finally``.

    ``_pending`` is touched directly because that is exactly what the route reads in
    that window; the point is that a second answer must be refused *and* must not
    overwrite the decision that was already taken.
    """
    gate = mcp_http.ApprovalGate()
    ticket = mcp_http.Ticket("positron", "write", {"path": "x"})
    gate._pending[ticket.id] = ticket  # noqa: SLF001 - the window under test
    ticket.done.set()

    assert gate.resolve(ticket.id, True) is False
    assert ticket.answer is None, "a refused answer must not overwrite the decision"


def test_no_tool_runs_without_an_approver(tmp_path, monkeypatch) -> None:
    """The console (and any caller without a UI) gets no remote tools at all."""
    with FakeMCP() as fake:
        fake_cfg = tmp_path / "home"
        fake_cfg.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("UH_HOME", str(fake_cfg))
        (fake_cfg / "config.toml").write_text(
            f'[[mcp.servers]]\nname = "positron"\ntransport = "http"\nurl = "{fake.url}"\n',
            encoding="utf-8",
        )
        assert mcp_http.build_toolsets() == {}
        assert json.dumps(mcp_http.status()).count("positron") >= 1


def test_the_tools_panel_shows_a_configured_http_server(tmp_path, monkeypatch) -> None:
    """Positron was configured, reachable, offered to the model - and invisible.

    /api/tools/status listed only the stdio toolsets, so the panel could never
    answer "does Positron work?". It probes once and reports the tools it got.
    """
    app = make_app(tmp_path, monkeypatch)
    with FakeMCP() as fake:

        async def scenario():
            await call(
                app, "POST", "/api/mcp/http", json_body={"name": "positron", "url": fake.url}
            )
            return await call(app, "GET", "/api/tools/status")

        response = run(scenario())

    assert response.status_code == 200, response.text
    rows = response.json()["mcp"]["http"]
    positron = next(row for row in rows if row["name"] == "positron")
    assert positron["connected"] is True, positron
    assert positron["transport"] == "http"
    assert positron["tools"] == ["read", "write", "list"]
    assert positron["error"] == ""


def test_the_probe_button_runs_a_read_tool_and_refuses_a_write(tmp_path, monkeypatch) -> None:
    """One timed read-only call is the proof; a write is not reachable this way."""
    app = make_app(tmp_path, monkeypatch)
    with FakeMCP() as fake:

        async def scenario():
            await call(
                app, "POST", "/api/mcp/http", json_body={"name": "positron", "url": fake.url}
            )
            ok = await call(app, "POST", "/api/mcp/positron/call", json_body={"tool": "list"})
            refused = await call(app, "POST", "/api/mcp/positron/call", json_body={"tool": "write"})
            return ok, refused

        ok, refused = run(scenario())

    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["ok"] is True, body
    assert body["tool"] == "list"
    assert body["latency_ms"] >= 0
    assert refused.status_code == 400
    assert "lecture seule" in refused.json()["detail"]


def test_never_ask_again_is_a_session_decision(tmp_path, monkeypatch) -> None:
    """« Toujours autoriser » lives in memory: no file, and a restart asks again."""
    import threading
    import time

    gate = mcp_http.ApprovalGate()
    asked: list[dict] = []

    def notify(event):
        asked.append(event)

    def answer_soon():
        for _ in range(300):
            pending = gate.pending()
            if pending:
                gate.resolve(pending[0]["id"], True, always=True)
                return
            time.sleep(0.01)

    threading.Thread(target=answer_soon, daemon=True).start()
    assert gate.ask("positron", "execute_code", {}, notify=notify, timeout=5.0) is True
    assert gate.trusted() == ["positron"]

    # Second call: the same server, no question at all.
    asked.clear()
    assert gate.ask("positron", "execute_code", {}, notify=notify, timeout=1.0) is True
    assert asked == [], "a trusted server must not ask again"

    # Another server is still gated.
    assert gate.ask("atelier", "write_file", {}, notify=notify, timeout=0.1) is False
    assert len(asked) == 1, "an untrusted server still asks"
