"""P0-5 end to end: a provider asks for a tool, the tool returns an image.

The whole chain, with no browser and no network: an OpenAI-shaped answer carrying
a tool call, the MCP stdio client running a real child process, the artifact
crossing the SSE stream as a data URL, and the same bytes found again by reading
the session back — which is what a reload does.

The model never sees the base64 (it gets a marker), and the interface never gets
the marker alone (it gets `artifacts`).
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Optional

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh.web.app import create_app  # noqa: E402
from uh.web.sse import parse_frames  # noqa: E402

TOKEN = "test-token-image-artifacts"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TOOL = "mcp__chart__render"

#: A real 1x1 PNG: magic bytes included, so the whitelist and the size floor are
#: exercised by something a browser can actually draw.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

FAKE_SERVER = r"""
import base64
import json
import sys

PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "chart", "version": "0.0.0"},
        }})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [
            {"name": "render", "description": "Draw a chart",
             "inputSchema": {"type": "object", "properties": {}}},
        ]}})
    elif method == "tools/call":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"content": [
            {"type": "image", "data": PNG, "mimeType": "image/png"},
        ]}})
    elif request_id is not None:
        send({"jsonrpc": "2.0", "id": request_id,
              "error": {"code": -32601, "message": "unknown method"}})
"""


class ChartProvider:
    """Asks for the image once, then answers. Records what the model was shown."""

    model = "stub:1b"

    def __init__(self) -> None:
        self.calls = 0
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": TOOL, "arguments": {}}}],
            }
        return {"role": "assistant", "content": "Le graphique est affiché."}


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: Optional[object] = None):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UH_HOME", str(home))
    script = home / "chart_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    (home / "config.toml").write_text(
        "[[mcp.servers]]\n"
        'name = "chart"\n'
        f'command = "{Path(__import__("sys").executable).as_posix()}"\n'
        f'args = ["{script.as_posix()}"]\n',
        encoding="utf-8",
    )
    return create_app(
        token=TOKEN,
        port=8765,
        workspace=tmp_path,
        provider_factory=lambda kind, model: provider,
        kind="stub",
        model="stub:1b",
    )


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


def test_an_api_answer_that_calls_a_tool_puts_an_image_in_the_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ChartProvider()
    app = make_app(tmp_path, monkeypatch, provider=provider)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)).json()
            session_id = created["session"]["id"]
            reply = await http.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "trace un graphique"},
                headers=AUTH,
            )
            assert reply.status_code == 200
            frames = parse_frames(reply.text)

            results = [data for event, data in frames if event == "tool_result"]
            assert results, "the tool ran and its result reached the transcript"
            payload = json.loads(results[0]["content"])
            artifacts = payload.get("artifacts") or []
            assert artifacts, "the image crossed the stream as an artifact"
            assert artifacts[0]["mime"] == "image/png"
            assert artifacts[0]["bytes"] == len(PNG_BYTES)
            assert artifacts[0]["data_url"].startswith("data:image/png;base64,")
            assert artifacts[0]["data_url"].endswith(base64.b64encode(PNG_BYTES).decode()), (
                "the interface gets the real bytes, not a placeholder"
            )

            # The model's copy: a marker, never tens of thousands of characters.
            text = payload.get("text") or ""
            assert "[image: image/png" in text
            assert base64.b64encode(PNG_BYTES).decode() not in text

            stored = (await http.get(f"/api/sessions/{session_id}/messages", headers=AUTH)).json()[
                "messages"
            ]
            carriage = [m for m in stored if "artifacts" in str(m.get("content") or "")]
            assert carriage, "the artifact is in the store: a reload shows it again"
            assert "data:image/png;base64," in str(carriage[0]["content"])

            # And what a provider actually sends: `request_messages` is the one
            # choke point every provider passes its payload through, and it must
            # strip the bytes while the session keeps them.
            from uh.providers import request_messages

            wire = json.dumps(request_messages(provider.seen[-1]))
            assert "data:image/png;base64," not in wire, "the API is not paying for pixels"
            assert "[image: image/png" in wire, "the model still knows an image exists"

    asyncio.run(scenario())
