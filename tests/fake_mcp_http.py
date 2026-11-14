"""A fake MCP server over real sockets: JSON, SSE-in-a-response, legacy SSE.

Used by the transport tests and the cockpit tests. It is deliberately a real
``ThreadingHTTPServer`` (not a mock of httpx) so the client's session header,
chunked event stream, redirect refusal and auth handling are exercised the way a
running server exercises them.
"""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "read",
        "description": "read something",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {"name": "write", "description": "write something", "inputSchema": {"type": "object"}},
    {"name": "list", "description": "list something", "inputSchema": {"type": "object"}},
]


def answer(message: Dict[str, Any]) -> Dict[str, Any]:
    """The JSON-RPC reply this fake gives, or ``{}`` for a notification."""
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-positron", "version": "9.9"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = str((message.get("params") or {}).get("name") or "?")
        result = {"content": [{"type": "text", "text": f"ok:{name}"}]}
    else:  # ping, notifications/initialized
        result = {}
    if request_id is None:
        return {}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class FakeMCP:
    """``mode``: json | sse_json | legacy | redirect | garbage."""

    def __init__(
        self,
        *,
        mode: str = "json",
        token: str = "",
        port: int = 0,
        allow: Optional[List[str]] = None,
    ) -> None:
        self.mode = mode
        self.token = token
        self.allow = allow
        self.healthy = True
        self.calls: List[str] = []
        # One queue per open stream: a leftover connection from an earlier
        # attempt must not swallow the answer meant for the live one.
        self.stream_queues: "List[queue.Queue[Optional[Dict[str, Any]]]]" = []
        self.stream_open = threading.Event()
        self._queues_lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                return

            def _auth_ok(self) -> bool:
                if not fake.token:
                    return True
                return self.headers.get("authorization", "") == f"Bearer {fake.token}"

            def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Mcp-Session-Id", "sess-42")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - http.server's shape
                if fake.mode != "legacy":
                    self._send(405, b"{}")
                    return
                if not self._auth_ok():
                    self._send(401, b"{}")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                mine: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
                with fake._queues_lock:
                    fake.stream_queues.append(mine)
                fake.stream_open.set()
                try:
                    self._write_chunk(b"event: endpoint\ndata: /messages\n\n")
                    while True:
                        item = mine.get()
                        if item is None:
                            break
                        payload = json.dumps(item).encode("utf-8")
                        self._write_chunk(b"event: message\ndata: " + payload + b"\n\n")
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (ConnectionError, OSError):
                    pass

            def _write_chunk(self, payload: bytes) -> None:
                self.wfile.write(b"%x\r\n" % len(payload) + payload + b"\r\n")
                self.wfile.flush()

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                if not self._auth_ok():
                    self._send(401, b'{"error":"nope"}')
                    return
                if not fake.healthy:
                    self._send(503, b"{}")
                    return
                if fake.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://elsewhere.example/mcp")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if fake.mode == "garbage":
                    self._send(200, b"<html>not mcp</html>", "text/html")
                    return
                try:
                    message = json.loads(raw.decode("utf-8"))
                except ValueError:
                    self._send(400, b"{}")
                    return
                reply = answer(message)
                method = str(message.get("method") or "")
                if method == "tools/call":
                    fake.calls.append(str((message.get("params") or {}).get("name") or ""))
                if fake.mode == "legacy":
                    if reply:
                        with fake._queues_lock:
                            target = fake.stream_queues[-1] if fake.stream_queues else None
                        if target is not None:
                            target.put(reply)
                    self._send(202, b"", "application/json")
                    return
                if not reply:
                    self._send(202, b"", "application/json")
                    return
                if fake.mode == "sse_json":
                    body = f"event: message\ndata: {json.dumps(reply)}\n\n".encode("utf-8")
                    self._send(200, body, "text/event-stream")
                    return
                self._send(200, json.dumps(reply).encode("utf-8"))

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}{'/sse' if self.mode == 'legacy' else '/mcp'}"

    def close_stream(self) -> None:
        with self._queues_lock:
            queues = list(self.stream_queues)
        for item in queues:
            item.put(None)

    def stop(self) -> None:
        self.close_stream()
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> "FakeMCP":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()
