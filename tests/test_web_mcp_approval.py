"""The approval of a remote tool, proven through a real chat turn.

The transport tests prove the gate; the route tests prove the ticket. What was
missing is the whole chain: a model answer that asks for a remote tool, the
``approval`` frame on the turn's stream, the click, and the call actually
reaching the server - or not, when the user refuses or says nothing.

No model is needed: the provider below is scripted (it returns one tool call,
then a final answer), which is exactly what the harness has to handle.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fake_mcp_http import FakeMCP  # noqa: E402

from uh import mcp_cloud, mcp_http  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-approval"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
REMOTE_TOOL = "mcp__positron__read"


class ScriptedProvider:
    """One remote tool call, then a final answer. No network, no model."""

    model = "scripted:1b"
    streaming = True

    def __init__(self, *, tool_calls: int = 1) -> None:
        self.tool_calls = tool_calls
        self.calls = 0

    def chat(self, messages: List[dict], tools: List[dict] | None = None, **_: Any) -> dict:
        self.calls += 1
        if self.calls <= self.tool_calls:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": REMOTE_TOOL, "arguments": {"path": "notes.txt"}}}
                ],
            }
        last = ""
        for message in reversed(messages):
            if message.get("role") == "tool":
                last = str(message.get("content") or "")[:120]
                break
        return {"role": "assistant", "content": f"fini ({last})"}


def make_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, tool_calls: int = 1, port: int = 8765
):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UH_HOME", str(home))
    provider = ScriptedProvider(tool_calls=tool_calls)
    app = create_app(
        token=TOKEN,
        port=port,
        host="127.0.0.1",
        workspace=tmp_path,
        provider_factory=lambda kind, model: provider,
        kind="scripted",
        model="scripted:1b",
    )
    return app, provider


def connect(tmp_path: Path, url: str) -> None:
    """Record the fake server exactly as the panel does."""
    created = mcp_cloud.connect_http("positron", url)
    assert created["state"] == "configured-not-started"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def serve(app, port: int) -> Iterator[str]:
    """A real uvicorn on a real port: the SSE stream must stream.

    httpx's ASGI transport buffers the whole body, so a turn that waits for an
    approval would deadlock in the test client, not in the product. This goes
    over loopback like the browser does - and the app is built with the same
    port, because the Host header is checked against it.
    """
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    try:
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def run_turn(
    base: str,
    *,
    answer=None,
    stop_after: int | None = None,
) -> Dict[str, Any]:
    """POST a chat, answer (or not) every approval frame, collect the events."""
    events: List[Dict[str, Any]] = []
    seen: List[str] = []
    with httpx.Client(base_url=base, headers=AUTH, timeout=60.0) as browser, httpx.Client(
        base_url=base, headers=AUTH, timeout=60.0
    ) as answering:
        session = browser.post("/api/sessions", json={}).json()["session"]
        with browser.stream(
            "POST",
            f"/api/sessions/{session['id']}/chat",
            json={"message": "lis le fichier"},
        ) as response:
            event = ""
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = json.loads(line[5:].strip() or "{}")
                events.append({"event": event, **data})
                if event == "approval":
                    seen.append(str(data["id"]))
                    wanted = len(seen) <= (stop_after or 0)
                    if answer is not None and wanted:
                        answering.post(
                            f"/api/mcp/approvals/{data['id']}", json={"allow": answer}
                        )
    return {"events": events, "session": session, "tickets": seen}


@pytest.fixture(autouse=True)
def clean_registry():
    mcp_http.shutdown()
    yield
    mcp_http.shutdown()


def test_an_approved_remote_call_runs_and_the_answer_says_so(tmp_path, monkeypatch) -> None:
    port = free_port()
    app, _provider = make_app(tmp_path, monkeypatch, port=port)
    with FakeMCP() as fake:
        connect(tmp_path, fake.url)
        with serve(app, port) as base:
            result = run_turn(base, answer=True, stop_after=1)

    approvals = [event for event in result["events"] if event["event"] == "approval"]
    assert len(approvals) == 1, result["events"]
    assert approvals[0]["server"] == "positron"
    assert approvals[0]["tool"] == "read"
    assert approvals[0]["arguments"] == {"path": "notes.txt"}
    assert approvals[0]["timeout"] > 0

    tool_results = [event for event in result["events"] if event["event"] == "tool_result"]
    assert any("ok:read" in str(event.get("content")) for event in tool_results), tool_results
    assert fake.calls == ["read"], "the approved call must reach the server"
    assert result["events"][-1]["event"] == "done"


def test_a_refused_call_never_reaches_the_server(tmp_path, monkeypatch) -> None:
    port = free_port()
    app, _provider = make_app(tmp_path, monkeypatch, port=port)
    with FakeMCP() as fake:
        connect(tmp_path, fake.url)
        with serve(app, port) as base:
            result = run_turn(base, answer=False, stop_after=1)

    tool_results = [event for event in result["events"] if event["event"] == "tool_result"]
    assert any("Rien n'a été exécuté" in str(event.get("content")) for event in tool_results)
    assert fake.calls == []
    assert result["events"][-1]["event"] == "done"


def test_no_answer_is_a_refusal_after_the_timeout(tmp_path, monkeypatch) -> None:
    port = free_port()
    app, _provider = make_app(tmp_path, monkeypatch, port=port)
    original = mcp_http.APPROVAL_TIMEOUT
    mcp_http.APPROVAL_TIMEOUT = 0.5
    try:
        with FakeMCP() as fake:
            connect(tmp_path, fake.url)
            with serve(app, port) as base:
                # Nobody answers the frame: the turn must not hang, nothing runs.
                result = run_turn(base, answer=None)
    finally:
        mcp_http.APPROVAL_TIMEOUT = original

    tool_results = [event for event in result["events"] if event["event"] == "tool_result"]
    assert any("n'a pas été autorisé" in str(event.get("content")) for event in tool_results)
    assert fake.calls == []
    assert result["events"][-1]["event"] == "done"


def test_each_call_asks_again(tmp_path, monkeypatch) -> None:
    """Approving one call must not authorise the next one."""
    port = free_port()
    app, _provider = make_app(tmp_path, monkeypatch, tool_calls=2, port=port)
    original = mcp_http.APPROVAL_TIMEOUT
    mcp_http.APPROVAL_TIMEOUT = 0.5
    try:
        with FakeMCP() as fake:
            connect(tmp_path, fake.url)
            with serve(app, port) as base:
                result = run_turn(base, answer=True, stop_after=1)
    finally:
        mcp_http.APPROVAL_TIMEOUT = original

    tickets = result["tickets"]
    assert len(tickets) == 2, "every call asks, even after an approval"
    assert tickets[0] != tickets[1], "a ticket is single use"
    assert fake.calls == ["read"], "only the approved call ran"
    assert result["events"][-1]["event"] == "done"
