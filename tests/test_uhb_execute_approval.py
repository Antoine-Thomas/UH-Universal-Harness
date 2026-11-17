"""P0-4: `execute_code` asks the operator, and UHB checks the answer.

Two authorities, on purpose:

* the interface carries the operator's decision (this file's ``approver``), because
  the snippet runs as the operator's user and the environment scrub does not protect
  their files;
* UHB enforces it (a nonce it mints, single use, checked server-side), because a
  body claiming ``approved: true`` proves nothing and a client can skip the UI.

These tests exercise the interface half against a real loopback server, and prove the
negative: on a refusal, nothing is sent to ``/tools/execute`` at all.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterator, List

import pytest

from uh.toolsets import uhb as uhb_mod
from uh.toolsets.uhb import Uhbtoolset

EXECUTE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "timeout": {"type": "integer", "minimum": 1, "maximum": 300, "default": 30},
    },
    "required": ["code"],
}

RESULT: Dict[str, Any] = {
    "ok": True,
    "exit_code": 0,
    "stdout": "ran\n",
    "stderr": "",
    "timed_out": False,
    "duration_s": 0.1,
    "timeout_s": 10.0,
    "mode": "dev",
    "run_dir": "/tmp/run",
    "command": ["python"],
    "images": [],
    "notes": [],
}


class _Handler(BaseHTTPRequestHandler):
    """Records every call, so a test can assert that nothing was sent."""

    calls: List[tuple[str, Dict[str, Any]]] = []
    approvals_status: int = 200

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._answer(200, {"tools": [
            {"name": "execute_code", "description": "run python",
             "parameters": EXECUTE_SCHEMA, "execution_type": "subprocess"},
        ]})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        _Handler.calls.append((self.path, body))
        if self.path == "/tools/approvals":
            if _Handler.approvals_status != 200:
                self._answer(_Handler.approvals_status, {"error": {"message": "no"}})
                return
            self._answer(200, {"approval_id": "nonce-from-uhb", "tool": "execute_code",
                               "expires_in": 120})
            return
        self._answer(200, RESULT)

    def _answer(self, status: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionError, OSError):
            pass


@pytest.fixture()
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    _Handler.calls = []
    _Handler.approvals_status = 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("UH_UHB_EXECUTE", "1")

    def build(approver=None) -> Uhbtoolset:
        return Uhbtoolset(url, timeout=5.0, discover=True, approver=approver)

    try:
        yield build
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def executes() -> List[Dict[str, Any]]:
    return [body for path, body in _Handler.calls if path == "/tools/execute"]


def test_without_an_approver_nothing_is_sent(served) -> None:
    """The panel builds a toolset with no stream, so no snippet may run from it."""
    toolset = served()

    result = toolset.call("execute_code", {"code": "print('x')"})

    assert result["type"] == "forbidden"
    assert result["reason"] == "approval_required"
    assert result["status"] == 403
    assert executes() == [], "no request reached /tools/execute"


def test_a_refused_approver_sends_nothing(served) -> None:
    asked: List[tuple[str, Dict[str, Any]]] = []

    def refuse(tool: str, arguments: Dict[str, Any]) -> bool:
        asked.append((tool, arguments))
        return False

    toolset = served(refuse)

    result = toolset.call("execute_code", {"code": "print('x')"})

    assert result["reason"] == "approval_refused"
    assert result["type"] == "forbidden"
    assert asked == [("execute_code", {"code": "print('x')"})], "the question was asked once"
    assert executes() == []


def test_a_broken_approver_is_a_refusal_not_a_run(served) -> None:
    def explode(tool: str, arguments: Dict[str, Any]) -> bool:
        raise RuntimeError("the stream died")

    toolset = served(explode)

    result = toolset.call("execute_code", {"code": "print('x')"})

    assert result["reason"] == "approval_refused"
    assert executes() == []


def test_an_approved_call_carries_uhbs_own_nonce(served) -> None:
    toolset = served(lambda tool, arguments: True)

    result = toolset.call("execute_code", {"code": "print('x')", "timeout": 10})

    assert result["ok"] is True
    sent = executes()
    assert len(sent) == 1
    assert sent[0]["approval_id"] == "nonce-from-uhb", "the nonce is UHB's, not UH's"
    assert sent[0]["code"] == "print('x')"


def test_an_uhb_that_refuses_to_mint_stops_the_run(served) -> None:
    """Fail-closed: no nonce means no call, even with the operator's approval."""
    _Handler.approvals_status = 403
    toolset = served(lambda tool, arguments: True)

    result = toolset.call("execute_code", {"code": "print('x')"})

    assert result["reason"] == "approval_required"
    assert executes() == [], "a missing nonce must not become a call that hopes"


def test_other_uhb_tools_never_ask(served) -> None:
    """Only the tool that runs code asks. A route decision stays free of prompts."""
    toolset = served()

    toolset.call("uhb_route", {"prompt": "hello"})

    assert [path for path, _body in _Handler.calls if path == "/tools/execute"] == []


def test_the_question_is_scoped_to_execute_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """`always allow` must not become `always allow every UHB tool`."""
    seen: List[tuple[str, str]] = []

    class FakeGate:
        def ask(self, server, tool, arguments, *, notify, timeout=120.0):
            seen.append((server, tool))
            return True

    from uh import mcp_http

    monkeypatch.setattr(mcp_http, "GATE", FakeGate())
    approver = uhb_mod.approver_for(lambda _event: None)

    assert approver("execute_code", {"code": "print(1)"}) is True
    assert seen == [(uhb_mod.APPROVAL_SCOPE, "execute_code")]
    assert uhb_mod.APPROVAL_SCOPE != "uhb", "trusting 'uhb' would cover the desktop tools too"
