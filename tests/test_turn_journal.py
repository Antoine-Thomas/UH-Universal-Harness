"""The turn journal: enough to answer "what blocked my turn", never a word of it.

Rotated, greppable, and it must never contain a prompt, an answer, a tool argument
or a key - a journal that quotes the conversation is a leak with a retention policy.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import turnlog  # noqa: E402
from uh.web import app as web_app  # noqa: E402

TOKEN = "turn-journal-token"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CANARY = "sk-LEAKCANARY-8f2b"


class SlowToolset:
    """A tool that takes time, with a payload that must never reach the journal."""

    name = "slow"
    tools = [
        {
            "type": "function",
            "function": {
                "name": "sleep_tool",
                "description": "sleep a little",
                "parameters": {
                    "type": "object",
                    "properties": {"seconds": {"type": "number"}, "payload": {"type": "string"}},
                },
            },
        }
    ]

    def __init__(self, seconds: float = 0.2) -> None:
        self.seconds = seconds

    def call(self, name: str, args: dict) -> dict:
        time.sleep(float(args.get("seconds") or self.seconds))
        return {"ok": True, "payload": args.get("payload")}


class ToolThenAnswer:
    """One tool call, then an answer that carries the canary too."""

    model = "stub:1b"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "sleep_tool",
                            "arguments": {"seconds": 0.2, "payload": CANARY},
                        }
                    }
                ],
            }
        if on_token is not None:
            on_token(f"voici {CANARY}")
        return {"role": "assistant", "content": f"voici {CANARY}", "tool_calls": []}


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider=None):
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    turnlog.reset_for_tests()
    provider = provider or ToolThenAnswer()
    return web_app.create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=tmp_path,
        provider_factory=lambda kind, model: provider,
        kind="stub",
        model="stub:1b",
    )


def run_chat(app, text: str = "fais le tour") -> str:
    async def scenario() -> str:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url=BASE, headers=AUTH, timeout=60
        ) as http:
            session = (await http.post("/api/sessions", json={})).json()["session"]["id"]
            async with http.stream(
                "POST", f"/api/sessions/{session}/chat", json={"message": text}
            ) as reply:
                chunks = [chunk async for chunk in reply.aiter_text()]
            return "".join(chunks)

    return asyncio.run(scenario())


def journal() -> str:
    path = turnlog.log_path()
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_the_journal_writes_a_greppable_line(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    turnlog.reset_for_tests()

    turnlog.event("tool_end", turn="ab12cd34", tool="execute_code", ms=4210, status="ok")

    text = journal()
    assert "turn tool_end" in text
    assert "turn=ab12cd34" in text and "tool=execute_code" in text
    assert "ms=4210" in text and "status=ok" in text
    assert text.strip().count("\n") == 0, "one line per event"


def test_a_turn_journals_its_tools_and_its_end(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        web_app,
        "_toolsets",
        lambda _app, *, workspace=None, read_roots=None, web_search=True, notify=None: {
            "slow": SlowToolset()
        },
    )

    run_chat(app)

    text = journal()
    assert "turn_start" in text, text
    assert "tool_start" in text and "tool=sleep_tool" in text
    assert "tool_end" in text and "status=ok" in text
    # A tool that takes 200 ms must show it: "ms=" is the point of the line.
    assert any("ms=" in line and "tool_end" in line for line in text.splitlines())
    assert "turn_end" in text and "reason=done" in text


def test_a_turn_that_never_answers_is_journalled_as_a_deadline(tmp_path, monkeypatch) -> None:
    class Hanging:
        model = "stub:1b"

        def chat(self, messages, tools, *, on_token=None):
            time.sleep(3)  # the worker thread blocks, the stream must cap
            return {"role": "assistant", "content": "", "tool_calls": []}

    monkeypatch.setattr(web_app, "HEARTBEAT_S", 0.1)
    monkeypatch.setattr(web_app, "TURN_CAP_S", 0.3)
    app = make_app(tmp_path, monkeypatch, provider=Hanging())

    body = run_chat(app)

    assert "event: done" in body
    assert "reason=deadline" in journal()


def test_no_argument_no_answer_and_no_key_ever_reaches_the_journal(tmp_path, monkeypatch) -> None:
    """The canary is in a tool argument, in the answer and in the prompt: none of them
    may appear. This is the test that makes the journal safe to ship."""
    app = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        web_app,
        "_toolsets",
        lambda _app, *, workspace=None, read_roots=None, web_search=True, notify=None: {
            "slow": SlowToolset()
        },
    )

    run_chat(app, text=f"utilise la cle {CANARY} pour finir")

    text = journal()
    assert text, "the journal must have been written"
    assert CANARY not in text
    assert "utilise la cle" not in text
    assert "voici" not in text
