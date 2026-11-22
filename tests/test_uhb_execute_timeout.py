"""P0-4: the client must outwait the snippet it asked UHB to run.

A tool that runs Python answers only when the child is over - killed on timeout,
artifacts scanned, images described. So the transport budget of an
``execute_code`` call cannot be the 5 s of a route decision: a snippet allowed to
run 10 s would be cut at 5 and reported as "uhb unreachable" while UHB was
working exactly as asked.

These tests use a real HTTP server on a loopback port rather than a
``MockTransport``: the question is whether a socket stays open long enough, and a
scripted transport cannot answer it.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Iterator

import pytest

from uh.toolsets import uhb as uhb_mod
from uh.toolsets.uhb import Uhbtoolset

#: What UHB really publishes: the clamp is in the schema, not in this file.
EXECUTE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "maxLength": 200_000},
        "timeout": {"type": "integer", "minimum": 1, "maximum": 300, "default": 30},
    },
    "required": ["code"],
}


def result_payload(**overrides: Any) -> Dict[str, Any]:
    payload = {
        "ok": True,
        "exit_code": 0,
        "stdout": "done\n",
        "stderr": "",
        "timed_out": False,
        "duration_s": 8.01,
        "timeout_s": 10.0,
        "mode": "dev",
        "run_dir": "/tmp/run-x",
        "command": ["python", "_uhb_runner.py"],
        "images": [],
        "notes": [],
    }
    payload.update(overrides)
    return payload


class _Handler(BaseHTTPRequestHandler):
    """Routes scripted by the test: path -> (delay in seconds, payload)."""

    routes: Dict[str, tuple[float, Dict[str, Any]]] = {}

    def log_message(self, *args: Any) -> None:  # keep pytest output clean
        pass

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._answer(
            {
                "tools": [
                    {
                        "name": "execute_code",
                        "description": "run python",
                        "parameters": EXECUTE_SCHEMA,
                        "execution_type": "subprocess",
                    },
                    {
                        "name": "desktop_snapshot",
                        "description": "look at the screen",
                        "parameters": {"type": "object", "properties": {}},
                        "execution_type": "in_process",
                    },
                ]
            }
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)  # drain, the body is not under test
        if self.path == "/tools/approvals":
            # The nonce UHB would mint: without one the client may not execute at all.
            self._answer(
                {"approval_id": "nonce-timeout", "tool": "execute_code", "expires_in": 120}
            )
            return
        delay, payload = self.routes.get(self.path, (0.0, {"error": "no route"}))
        time.sleep(delay)
        self._answer(payload)

    def _answer(self, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            # A test that wants the client to give up makes the server write into a
            # closed socket: that is the point of the test, not a failure to report.
            pass


@pytest.fixture()
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Uhbtoolset]]:
    """Serve on a free port; ask the fixture for a toolset with a short default."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("UH_UHB_EXECUTE", "1")
    try:

        def build(**kwargs: Any) -> Uhbtoolset:
            kwargs.setdefault("timeout", 5.0)
            kwargs.setdefault("approver", lambda _tool, _arguments: True)
            return Uhbtoolset(url, discover=True, **kwargs)

        yield build
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_snippet_that_sleeps_eight_seconds_is_not_reported_unreachable(served) -> None:
    """The regression this lot exists for: 8 s of legitimate work, 5 s of patience."""
    _Handler.routes = {"/tools/execute": (8.0, result_payload())}
    toolset = served()

    started = time.monotonic()
    result = toolset.call("execute_code", {"code": "import time; time.sleep(8)", "timeout": 10})
    elapsed = time.monotonic() - started

    assert result.get("kind") != "transport", f"cut at {elapsed:.1f}s: {result}"
    assert "unreachable" not in str(result).lower()
    assert result["ok"] is True
    assert result["stdout"] == "done\n"
    assert elapsed >= 8.0, "the client really waited for the child"


def test_a_snippet_that_exceeds_its_timeout_comes_back_structured(served) -> None:
    """Past the timeout UHB kills the tree and answers: that answer is the result.

    The snippet is allowed 6 s, so UHB answers around 6 s — longer than the 5 s a
    route decision gets. The relayed payload must be UHB's structured timeout, never
    a transport error and never an exception.
    """
    _Handler.routes = {
        "/tools/execute": (
            6.4,
            result_payload(
                ok=False,
                exit_code=-1,
                stdout="",
                timed_out=True,
                duration_s=6.02,
                timeout_s=6.0,
                notes=["timed out after 6.0s: the process tree was killed"],
            ),
        )
    }
    toolset = served()

    result = toolset.call("execute_code", {"code": "import time; time.sleep(30)", "timeout": 6})

    assert result.get("kind") != "transport"
    assert result["timed_out"] is True
    assert result["ok"] is False
    assert any("timed out" in note for note in result["notes"])


def test_the_budget_is_the_requested_timeout_plus_a_margin(served) -> None:
    toolset = served()

    assert toolset._call_timeout("execute_code", {"timeout": 10}) == 10 + uhb_mod.EXECUTE_MARGIN
    # no timeout asked: UHB's own default, read from the schema (30), plus margin
    assert toolset.execute_limits == {"default": 30.0, "maximum": 300.0}
    assert toolset._call_timeout("execute_code", {}) == 30 + uhb_mod.EXECUTE_MARGIN
    # above the clamp UHB applies: the ceiling is the schema's, not a guess
    assert (
        toolset._call_timeout("execute_code", {"timeout": 10_000}) == 300 + uhb_mod.EXECUTE_MARGIN
    )
    # junk or zero falls back to the default rather than to a zero-second wait
    assert toolset._call_timeout("execute_code", {"timeout": "soon"}) == 30 + uhb_mod.EXECUTE_MARGIN
    assert toolset._call_timeout("execute_code", {"timeout": 0}) == 30 + uhb_mod.EXECUTE_MARGIN


def test_only_execute_code_gets_the_long_budget(served) -> None:
    """A route decision stays a local, fast call: 5 s, not 45."""
    toolset = served()

    assert toolset._call_timeout("uhb_route", {"prompt": "x"}) == 5.0
    assert toolset._call_timeout("desktop_snapshot", {}) == 5.0


def test_another_tool_is_still_cut_at_its_own_timeout(served) -> None:
    """The margin must not leak to the generic route: a hung tool still fails fast."""
    _Handler.routes = {"/tools/desktop_snapshot": (2.5, {"ok": True})}
    toolset = served(timeout=1.0)

    result = toolset.call("desktop_snapshot", {})

    assert result.get("kind") == "transport", "a 2.5 s answer against 1 s must be cut"
