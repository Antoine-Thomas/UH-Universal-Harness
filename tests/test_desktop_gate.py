"""P3-15: the whole `desktop_*` family asks the operator, not only `execute_code`.

A `desktop_*` tool acts through the OS: it clicks, types, opens a URL, and it can
read window titles - which carry e-mail subjects and file names. UHB publishes
those tools in its catalogue, so UH transports them; the gate therefore lives on
UH's side, in `call()`, and it is decided by PREFIX. A tool UHB adds tomorrow
under `desktop_` is gated without anyone editing this file.

Six properties, one test each:

1. no stream wired (the CLI path) -> every gated tool refuses, nothing is sent;
2. the operator refuses -> structured refusal, nothing is sent;
3. the operator approves -> the call reaches UHB (a simulated client here);
4. approving `execute_code` does not authorise `desktop_click` (per-tool scope);
5. a `desktop_*` tool nobody named is gated anyway (the prefix is the gate);
6. `uhb_status`, `uhb_modes`, `uhb_route`, `describe_tools` never ask.

The negative half is the point of tests 1, 2 and 5: none of them asserts a message,
they assert that no request reached the server at all.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Iterator, List

import pytest

from uh.toolsets import uhb as uhb_mod
from uh.toolsets.uhb import Uhbtoolset

#: The six tools UHB catalogues today, plus the one behind its own switch.
DESKTOP_TOOLS = [
    "desktop_windows",
    "desktop_snapshot",
    "desktop_find",
    "desktop_click",
    "desktop_type",
    "desktop_open_url",
]
GATED_TOOLS = DESKTOP_TOOLS + ["execute_code"]

CATALOGUE: Dict[str, Any] = {
    "tools": [
        {
            "name": name,
            "description": f"{name} (simulated)",
            "parameters": {"type": "object", "properties": {}},
            "execution_type": "in_process",
        }
        for name in DESKTOP_TOOLS
    ]
    + [
        # Published by UHB, named nowhere in uh/toolsets/uhb.py: the prefix test.
        {
            "name": "desktop_teleport",
            "description": "a tool that did not exist when this file was written",
            "parameters": {"type": "object", "properties": {}},
            "execution_type": "in_process",
        },
        {
            "name": "execute_code",
            "description": "run python",
            "parameters": {"type": "object", "properties": {"code": {"type": "string"}}},
            "execution_type": "subprocess",
        },
    ]
}


class _Handler(BaseHTTPRequestHandler):
    """A simulated UHB: it records every request the gate lets through."""

    calls: List[tuple[str, Dict[str, Any]]] = []

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = self.path.split("?", 1)[0]
        _Handler.calls.append((path, {}))
        if path == "/tools":
            self._answer(200, CATALOGUE)
        elif path == "/status":
            self._answer(
                200,
                {
                    "version": "1.0",
                    "mode": "dev",
                    "harness": "sim",
                    "providers": ["p"],
                    "modes": ["dev"],
                },
            )
        elif path == "/modes":
            self._answer(200, {"modes": [{"name": "dev", "tools": ["desktop_windows"]}]})
        else:
            self._answer(404, {"error": {"message": "no route"}})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        _Handler.calls.append((path, body))
        if path == "/route":
            self._answer(
                200,
                {
                    "complexity": "trivial",
                    "reason": "simulated",
                    "chain": [{"model": "m"}],
                    "score": 0,
                },
            )
        elif path == "/tools/approvals":
            self._answer(
                200, {"approval_id": "nonce-from-uhb", "tool": "execute_code", "expires_in": 120}
            )
        else:
            self._answer(200, {"ok": True, "simulated": path})

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
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Uhbtoolset]]:
    """A simulated UHB on a free port; ask the fixture for a toolset."""
    _Handler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("UH_UHB_EXECUTE", "1")

    def build(approver=None) -> Uhbtoolset:
        # No `approver` is exactly the CLI path: cli.py builds the toolset with no
        # notify, so nothing can answer the question.
        return Uhbtoolset(url, timeout=5.0, discover=True, approver=approver)

    try:
        yield build
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def posts() -> List[tuple[str, Dict[str, Any]]]:
    """What reached UHB's action routes - approvals excluded, discovery included."""
    return [(path, body) for path, body in _Handler.calls if path.startswith("/tools/")]


def actions() -> List[tuple[str, Dict[str, Any]]]:
    """Only the calls that would have ACTED (execute_code or a desktop_ tool)."""
    return [
        (path, body) for path, body in posts() if path != "/tools/approvals" and path != "/tools"
    ]


# --------------------------------------------------------------------------- #
# 1. no stream wired: the CLI path
# --------------------------------------------------------------------------- #
def test_without_a_stream_every_gated_tool_is_refused(served) -> None:
    """`uh chat` builds no approver, so no desktop_* call may reach UHB."""
    toolset = served()

    for name in GATED_TOOLS:
        result = toolset.call(name, {})

        assert result["type"] == "forbidden", name
        assert result["reason"] == "approval_required", name
        assert result["status"] == 403, name
        assert result["tool"] == name, "the refusal names the tool actually asked for"

    assert actions() == [], "not one action reached UHB"


# --------------------------------------------------------------------------- #
# 2. the operator says no
# --------------------------------------------------------------------------- #
def test_a_refused_approval_sends_nothing(served) -> None:
    asked: List[tuple[str, Dict[str, Any]]] = []

    def refuse(tool: str, arguments: Dict[str, Any]) -> bool:
        asked.append((tool, arguments))
        return False

    toolset = served(refuse)

    result = toolset.call("desktop_click", {"ref": "x:0"})

    assert result["type"] == "forbidden"
    assert result["reason"] == "approval_refused"
    assert result["tool"] == "desktop_click"
    assert asked == [("desktop_click", {"ref": "x:0"})], "the question was asked once"
    assert actions() == []


# --------------------------------------------------------------------------- #
# 3. the operator says yes
# --------------------------------------------------------------------------- #
def test_an_approved_action_reaches_the_simulated_client(served) -> None:
    """Approval is transport, not permission: UHB still decides what it runs."""
    toolset = served(lambda _tool, _arguments: True)

    result = toolset.call("desktop_open_url", {"url": "https://example.invalid"})

    assert result == {"ok": True, "simulated": "/tools/desktop_open_url"}
    assert actions() == [("/tools/desktop_open_url", {"url": "https://example.invalid"})]


# --------------------------------------------------------------------------- #
# 4. scopes do not leak into each other
# --------------------------------------------------------------------------- #
def test_approving_execute_code_does_not_authorise_a_click(monkeypatch: pytest.MonkeyPatch) -> None:
    """An "always allow" is remembered per scope; a shared one would grant the family."""
    seen: List[str] = []

    class FakeGate:
        def ask(self, server, tool, arguments, *, notify, timeout=120.0):
            seen.append(server)
            return server == uhb_mod.approval_scope("execute_code")

    from uh import mcp_http

    monkeypatch.setattr(mcp_http, "GATE", FakeGate())
    approver = uhb_mod.approver_for(lambda _event: None)

    assert approver("execute_code", {"code": "print(1)"}) is True
    assert approver("desktop_click", {"ref": "x:0"}) is False
    assert seen == ["uhb:execute_code", "uhb:desktop_click"]
    assert uhb_mod.APPROVAL_SCOPE == uhb_mod.approval_scope("execute_code")


# --------------------------------------------------------------------------- #
# 5. the prefix, not a list
# --------------------------------------------------------------------------- #
def test_a_desktop_tool_nobody_named_is_gated_by_the_prefix(served) -> None:
    toolset = served()

    assert "desktop_teleport" in toolset.remote, "UHB published it; UH never named it"
    result = toolset.call("desktop_teleport", {})

    assert result["reason"] == "approval_required"
    assert result["tool"] == "desktop_teleport"
    assert actions() == []
    assert uhb_mod.is_gated("desktop_anything") is True
    assert uhb_mod.is_gated("uhb_status") is False


# --------------------------------------------------------------------------- #
# 6. the read-only questions stay free
# --------------------------------------------------------------------------- #
def test_the_read_only_questions_never_ask(served) -> None:
    """A route decision spends nothing, so it must not raise a prompt."""
    asked: List[str] = []

    def remember(tool: str, _arguments: Dict[str, Any]) -> bool:
        asked.append(tool)
        return False

    toolset = served(remember)

    for name, arguments in [
        ("uhb_status", {}),
        ("uhb_modes", {}),
        ("uhb_route", {"prompt": "hello"}),
        ("describe_tools", {}),
    ]:
        result = toolset.call(name, arguments)
        assert "error" not in result, (name, result)

    assert asked == [], "no prompt for a question that spends nothing"
