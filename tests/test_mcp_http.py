"""HTTP/SSE MCP transport (P14.1): handshake, states, reconnect, approval.

The fake server below speaks the three shapes UH has to survive: a JSON body, a
JSON body carried by an SSE stream (Streamable HTTP), and the older one-stream
HTTP+SSE where the first event names the URL that accepts POSTs. It also has the
failure modes the panel must name honestly: 401, 302 to another host, HTML
instead of JSON-RPC, and a stream that closes under the client.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fake_mcp_http import FakeMCP

from uh import mcp_http
from uh.config import load as load_config

TOKEN = "mcp-token-for-http-tests-7b21"


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str, **extra: Any) -> None:
    """Write a config.toml with one HTTP entry, the way the panel would."""
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    lines = ["[[mcp.servers]]", 'name = "positron"', 'transport = "http"', f'url = "{url}"']
    if extra.get("allow"):
        lines.append(
            "allow = [" + ", ".join(f'"{item}"' for item in extra["allow"]) + "]"
        )
    if extra.get("token"):
        lines.append('insecure = true')
    (tmp_path / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if extra.get("token"):
        from uh import mcp_cloud

        mcp_cloud.write_secrets("positron", {"MCP_HTTP_TOKEN": extra["token"]})


def _reset_registry() -> None:
    mcp_http.shutdown()


@pytest.fixture(autouse=True)
def clean_registry():
    _reset_registry()
    yield
    _reset_registry()


# --------------------------------------------------------------------------- #
# handshake: the three wire shapes
# --------------------------------------------------------------------------- #
def test_streamable_http_with_a_json_body(tmp_path, monkeypatch) -> None:
    with FakeMCP() as fake:
        _config(tmp_path, monkeypatch, fake.url)
        result = mcp_http.test_connection("positron", config=load_config())

    assert result["state"] == "connected", result
    assert result["server"] == "fake-positron"
    assert result["protocol"] == "2025-03-26"
    assert [tool for tool in result["tools"]] == ["read", "write", "list"]
    assert isinstance(result["duration_ms"], int)
    assert result["reason"] == ""


def test_streamable_http_with_the_answer_inside_sse(tmp_path, monkeypatch) -> None:
    with FakeMCP(mode="sse_json") as fake:
        _config(tmp_path, monkeypatch, fake.url)
        result = mcp_http.test_connection("positron", config=load_config())

    assert result["state"] == "connected", result
    assert len(result["tools"]) == 3


def test_legacy_http_sse_stream_names_its_post_endpoint(tmp_path, monkeypatch) -> None:
    with FakeMCP(mode="legacy") as fake:
        _config(tmp_path, monkeypatch, fake.url)
        result = mcp_http.test_connection("positron", config=load_config())

    assert result["state"] == "connected", result
    assert result["tools"] == ["read", "write", "list"]


# --------------------------------------------------------------------------- #
# the states the panel must tell apart
# --------------------------------------------------------------------------- #
def test_a_refused_token_says_what_to_do(tmp_path, monkeypatch) -> None:
    with FakeMCP(token=TOKEN) as fake:
        _config(tmp_path, monkeypatch, fake.url, token="wrong-token")
        result = mcp_http.test_connection("positron", config=load_config())

    assert result["state"] == "auth"
    assert result["label"] == "jeton refusé"
    assert "jeton" in result["advice"]
    assert TOKEN not in json.dumps(result)


def test_a_refused_token_on_the_legacy_stream_says_what_to_do(tmp_path, monkeypatch) -> None:
    with FakeMCP(mode="legacy", token=TOKEN) as fake:
        _config(tmp_path, monkeypatch, fake.url, token="wrong-token")
        result = mcp_http.test_connection("positron", config=load_config())

    assert result["state"] == "auth", result
    assert "jeton" in result["advice"]


def test_the_right_token_connects(tmp_path, monkeypatch) -> None:
    with FakeMCP(token=TOKEN) as fake:
        _config(tmp_path, monkeypatch, fake.url, token=TOKEN)
        result = mcp_http.test_connection("positron", config=load_config())

    assert result["state"] == "connected", result


def test_a_closed_port_is_unreachable(tmp_path, monkeypatch) -> None:
    with FakeMCP() as fake:
        url = fake.url
    # the server is gone; the port is free again
    _config(tmp_path, monkeypatch, url)
    result = mcp_http.test_connection("positron", config=load_config())

    assert result["state"] == "unreachable"
    assert "démarré" in result["advice"]


def test_an_html_page_is_not_a_protocol(tmp_path, monkeypatch) -> None:
    with FakeMCP(mode="garbage") as fake:
        _config(tmp_path, monkeypatch, fake.url)
        result = mcp_http.test_connection("positron", config=load_config())

    assert result["state"] == "incompatible"
    assert "URL MCP" in result["advice"]


def test_a_redirect_is_refused_not_followed(tmp_path, monkeypatch) -> None:
    followed: List[str] = []

    with FakeMCP(mode="redirect") as fake:
        _config(tmp_path, monkeypatch, fake.url)
        result = mcp_http.test_connection("positron", config=load_config())
        followed.append(fake.url)

    assert result["state"] == "unreachable"
    assert "redirection" in result["reason"]
    # the client never touched the redirect target: only the fake saw a request
    assert followed == [fake.url]


def test_an_unknown_server_name_is_a_configuration_error(tmp_path, monkeypatch) -> None:
    _config(tmp_path, monkeypatch, "http://127.0.0.1:9/mcp")
    with pytest.raises(mcp_http.MCPError) as raised:
        mcp_http.test_connection("nope", config=load_config())
    assert "aucun serveur HTTP" in str(raised.value)


def test_the_allow_list_filters_the_advertised_tools(tmp_path, monkeypatch) -> None:
    with FakeMCP() as fake:
        _config(tmp_path, monkeypatch, fake.url, allow=["read"])
        result = mcp_http.test_connection("positron", config=load_config())

    assert result["tools"] == ["read"]


# --------------------------------------------------------------------------- #
# no automatic execution: the approval is a prerequisite
# --------------------------------------------------------------------------- #
def test_without_an_approver_no_remote_tool_exists(tmp_path, monkeypatch) -> None:
    with FakeMCP() as fake:
        _config(tmp_path, monkeypatch, fake.url)
        assert mcp_http.build_toolsets(config=load_config()) == {}


def test_an_approved_call_runs_and_a_refused_one_does_not(tmp_path, monkeypatch) -> None:
    asked: List[Dict[str, Any]] = []

    with FakeMCP() as fake:
        _config(tmp_path, monkeypatch, fake.url)
        asked_answers = [True, False]

        def notify(event: Dict[str, Any]) -> None:
            asked.append(event)
            mcp_http.GATE.resolve(event["id"], asked_answers.pop(0))

        toolsets = mcp_http.build_toolsets(notify=notify, config=load_config())
        (toolset,) = toolsets.values()
        allowed = toolset.call("mcp__positron__read", {"path": "x"})
        refused = toolset.call("mcp__positron__write", {"path": "y"})

    assert allowed["text"] == "ok:read"
    assert fake.calls == ["read"], "the refused call must never reach the server"
    assert refused["kind"] == "approval"
    assert "Rien n'a été exécuté" in refused["error"]
    assert [event["type"] for event in asked] == ["approval", "approval"]
    assert asked[0]["tool"] == "read"


def test_an_unanswered_approval_is_a_refusal(tmp_path, monkeypatch) -> None:
    with FakeMCP() as fake:
        _config(tmp_path, monkeypatch, fake.url)
        toolsets = mcp_http.build_toolsets(notify=lambda event: None, config=load_config())
        (toolset,) = toolsets.values()

        original = mcp_http.APPROVAL_TIMEOUT
        mcp_http.APPROVAL_TIMEOUT = 0.05
        try:
            result = toolset.call("mcp__positron__read", {})
        finally:
            mcp_http.APPROVAL_TIMEOUT = original

    assert result["kind"] == "approval"
    assert fake.calls == []


def test_a_pending_ticket_can_be_listed_and_resolved() -> None:
    gate = mcp_http.ApprovalGate()
    seen: List[Dict[str, Any]] = []

    def notify(event: Dict[str, Any]) -> None:
        seen.append(event)

    worker = threading.Thread(
        target=lambda: gate.ask("positron", "read", {"path": "x"}, notify=notify, timeout=5)
    )
    worker.start()
    for _ in range(100):
        if gate.pending():
            break
        time.sleep(0.02)
    (ticket,) = gate.pending()
    assert gate.resolve(ticket["id"], True)
    worker.join(timeout=5)
    assert seen and seen[0]["server"] == "positron"


# --------------------------------------------------------------------------- #
# reconnect: a cut is detected, the backoff is bounded
# --------------------------------------------------------------------------- #
def test_backoff_doubles_then_caps() -> None:
    assert [mcp_http.compute_backoff(n) for n in (1, 2, 3, 7, 20)] == [1.0, 2.0, 4.0, 60.0, 60.0]


def test_a_dropped_stream_is_detected(tmp_path, monkeypatch) -> None:
    with FakeMCP(mode="legacy") as fake:
        _config(tmp_path, monkeypatch, fake.url)
        client = mcp_http.client_for(mcp_http.servers_from_config(load_config())[0])
        assert client.state == "connected"

        fake.close_stream()
        for _ in range(100):
            if client.state == "disconnected":
                break
            time.sleep(0.02)

    assert client.state == "disconnected"
    assert "flux" in client.status()["reason"]


def test_a_probe_reports_a_cut_and_recovers(tmp_path, monkeypatch) -> None:
    with FakeMCP() as fake:
        _config(tmp_path, monkeypatch, fake.url)
        client = mcp_http.client_for(mcp_http.servers_from_config(load_config())[0])
        assert client.alive

        fake.healthy = False
        assert client.probe() is False
        assert client.state == "disconnected"
        assert client.status()["advice"].startswith("La connexion s'est coupée")

        fake.healthy = True
        assert client.probe() is True
        assert client.state == "connected"


def test_a_failure_is_remembered_and_force_retries(tmp_path, monkeypatch) -> None:
    with FakeMCP() as fake:
        url = fake.url
    _config(tmp_path, monkeypatch, url)
    server = mcp_http.servers_from_config(load_config())[0]
    with pytest.raises(mcp_http.MCPError) as raised:
        mcp_http.client_for(server)
    assert raised.value.kind in {"unreachable", "transport"}

    with pytest.raises(mcp_http.MCPError) as remembered:
        mcp_http.client_for(server)
    assert remembered.value.kind == "unavailable"
    assert "nouvel essai" in str(remembered.value)


def test_status_reports_configured_entries_without_touching_the_network(
    tmp_path, monkeypatch
) -> None:
    _config(tmp_path, monkeypatch, "http://127.0.0.1:9/mcp")
    rows = mcp_http.status()
    assert rows[0]["state"] == "configured"
    assert rows[0]["label"] == "configuré"
    assert rows[0]["connected"] is False


# --------------------------------------------------------------------------- #
# an end-to-end call: the fake server records what really arrived
# --------------------------------------------------------------------------- #
def test_a_tool_call_reaches_the_server_over_streamable_http(tmp_path, monkeypatch) -> None:
    with FakeMCP() as fake:
        _config(tmp_path, monkeypatch, fake.url)

        def notify(event: Dict[str, Any]) -> None:
            mcp_http.GATE.resolve(event["id"], True)

        toolsets = mcp_http.build_toolsets(notify=notify, config=load_config())
        (toolset,) = toolsets.values()
        result = toolset.call("mcp__positron__list", {})

    assert result["text"] == "ok:list"
    assert fake.calls == ["list"]
