"""Testing a candidate MCP server before saving it (the popup's promise).

The popup keeps "Enregistrer" disabled until the probe answers ``connected``, so
the route has to be honest: it must not write anything, it must cap the exchange
at five seconds, and it must refuse an address the URL guard rejects.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402
from fake_mcp_http import FakeMCP  # noqa: E402

from uh import mcp_http  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-probe"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

MINI_SERVER = '''
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    if message.get("id") is None:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {},
                  "serverInfo": {"name": "probe-mini", "version": "1.0"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "ping", "inputSchema": {"type": "object"}}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
'''


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UH_HOME", str(home))
    return create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=tmp_path,
        provider_factory=lambda kind, model: None,
        kind="stub",
        model="stub:1b",
    )


async def call(app, path: str, body: dict):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
        return await http.post(path, headers=AUTH, json=body)


@pytest.fixture(autouse=True)
def clean_registry():
    mcp_http.shutdown()
    yield
    mcp_http.shutdown()


def test_a_working_http_server_probes_connected_and_saves_nothing(
    tmp_path, monkeypatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    with FakeMCP() as fake:
        response = asyncio.run(
            call(app, "/api/mcp/probe", {"name": "positron", "transport": "http", "url": fake.url})
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "connected", body
    assert body["tools"] == ["read", "write", "list"]
    assert body["duration_ms"] >= 0
    assert not (tmp_path / "home" / "config.toml").exists(), "a probe writes nothing"


def test_a_refused_token_is_a_state_not_a_crash(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    with FakeMCP(token="the-real-one") as fake:
        response = asyncio.run(
            call(
                app,
                "/api/mcp/probe",
                {"name": "positron", "transport": "http", "url": fake.url, "token": "wrong"},
            )
        )
    assert response.status_code == 200
    assert response.json()["state"] == "auth"
    assert "the-real-one" not in response.text


def test_an_address_the_guard_refuses_is_a_400(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    response = asyncio.run(
        call(
            app,
            "/api/mcp/probe",
            {"name": "remote", "transport": "http", "url": "http://mcp.example.com/sse"},
        )
    )
    assert response.status_code == 400
    assert "https" in response.json()["detail"]


def test_a_missing_command_is_a_400(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    response = asyncio.run(call(app, "/api/mcp/probe", {"name": "x", "transport": "stdio"}))
    assert response.status_code == 400


def test_a_stdio_candidate_that_speaks_mcp_connects(tmp_path, monkeypatch) -> None:
    script = tmp_path / "mini_server.py"
    script.write_text(MINI_SERVER, encoding="utf-8")
    app = make_app(tmp_path, monkeypatch)

    response = asyncio.run(
        call(
            app,
            "/api/mcp/probe",
            {
                "name": "mini",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(script)],
            },
        )
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "connected", body
    assert body["tools"] == ["ping"]
    assert body["server"] == "probe-mini"


def test_a_stdio_command_that_does_not_exist_is_reported(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    response = asyncio.run(
        call(
            app,
            "/api/mcp/probe",
            {"name": "ghost", "transport": "stdio", "command": "definitely-not-a-real-binary-42"},
        )
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "invalid"
    assert "PATH" in body["advice"]


def test_a_stdio_server_that_never_answers_times_out_in_five_seconds(
    tmp_path, monkeypatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    response = asyncio.run(
        call(
            app,
            "/api/mcp/probe",
            {
                "name": "sleepy",
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-c", "import time; time.sleep(30)"],
            },
        )
    )
    body = response.json()
    assert body["state"] == "timeout", body
    assert body["duration_ms"] < 15_000, "the probe must not wait for the child"
    assert "5 s" in body["advice"]


def test_the_import_route_parses_a_file(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    payload = {
        "filename": "claude.json",
        "text": json.dumps({"mcpServers": {"atelier": {"command": "npx", "args": ["-y", "pkg"]}}}),
    }
    response = asyncio.run(call(app, "/api/mcp/import", payload))
    assert response.status_code == 200, response.text
    (server,) = response.json()["servers"]
    assert server["name"] == "atelier"
    assert server["transport"] == "stdio"


def test_the_import_route_refuses_garbage(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    response = asyncio.run(call(app, "/api/mcp/import", {"filename": "x.json", "text": "nope{"}))
    assert response.status_code == 400
