"""P0-6, point 3: a real UHB, mode `chat`, and a 403 that reaches the loop.

The other integration tests use one half each — a `MockTransport` for the payload
shape, a fake provider for the loop. This one runs the real thing: a `uhb run
--mode chat` subprocess on a free port, UH's own `uhb` toolset against it, and the
refusal travelling back into `run_turn` with its `tool_call_id` intact.

Skips when the UHB checkout is absent: the test needs both halves on disk.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import pytest

from uh import uhb_spawn
from uh.agent import run_turn
from uh.toolsets.file import FileToolset
from uh.toolsets.uhb import Uhbtoolset

UHB = uhb_spawn.find_checkout()
pytestmark = pytest.mark.skipif(UHB is None, reason="UHB checkout not found on this machine")


class ScriptedProvider:
    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if not self.replies:
            raise AssertionError("provider called more times than scripted")
        return self.replies.pop(0)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait(predicate, *, timeout: float = 60.0, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_a_chat_mode_uhb_refuses_execute_code_and_the_loop_keeps_the_id(
    tmp_path, monkeypatch
) -> None:
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("UH_UHB_URL", url)
    monkeypatch.setenv("UH_UHB_EXECUTE", "1")
    monkeypatch.setattr(uhb_spawn, "stop_recorded", lambda *a, **k: False)

    child = subprocess.Popen(
        [sys.executable, "-m", "uhb.entrypoints.cli", "run", "--mode", "chat", "--port", str(port)],
        cwd=str(UHB), env=dict(os.environ),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        assert wait(lambda: uhb_spawn.port_open(url)), "the chat-mode UHB never came up"

        toolset = Uhbtoolset(url, timeout=5.0, discover=True, approver=lambda _t, _a: True)
        names = [spec["function"]["name"] for spec in toolset.tools]
        assert "execute_code" in names, "the catalogue publishes it; the mode is what refuses"

        provider = ScriptedProvider([
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_real", "function": {"name": "execute_code",
                                                 "arguments": {"code": "print(1)"}}},
            ]},
            {"role": "assistant", "content": "je ne peux pas exécuter de code ici"},
        ])

        history = run_turn(
            [{"role": "user", "content": "lance print(1)"}],
            provider,
            {"file": FileToolset(workspace=tmp_path), "uhb": toolset},
            on_notice=lambda _text: None,
        )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)

    tools = [message for message in history if message.get("role") == "tool"]
    assert len(tools) == 1
    assert tools[0]["tool_call_id"] == "call_real", "the refusal names the call it refuses"
    payload = json.loads(tools[0]["content"])
    assert payload["type"] == "forbidden"
    assert payload["status"] == 403, "the 403 is UHB's, not UH's"
    assert "execute_code" in payload["error"]
    assert "chat" in payload["error"].lower(), (
        "the refusal names the mode: it is not the approval that stopped this call"
    )
    assert history[-1]["content"] == "je ne peux pas exécuter de code ici"
    assert provider.calls == 2, "one refusal, then the answer: no loop"


def test_a_real_uhb_runs_nothing_when_the_operator_refuses(tmp_path, monkeypatch) -> None:
    """The interface's refusal must reach the model as a refusal, with no side effect."""
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("UH_UHB_URL", url)
    monkeypatch.setenv("UH_UHB_EXECUTE", "1")

    child = subprocess.Popen(
        [sys.executable, "-m", "uhb.entrypoints.cli", "run", "--mode", "dev", "--port", str(port)],
        cwd=str(UHB), env=dict(os.environ),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        assert wait(lambda: uhb_spawn.port_open(url)), "the dev-mode UHB never came up"

        refused = Uhbtoolset(url, timeout=5.0, discover=True, approver=lambda _t, _a: False)
        result = refused.call(
            "execute_code", {"code": "open('side-effect.txt','w').write('x')", "timeout": 10}
        )

        assert result["type"] == "forbidden"
        assert result["reason"] == "approval_refused"
        assert "run_dir" not in result, "nothing ran, so nothing reports a run directory"

        approved = Uhbtoolset(url, timeout=5.0, discover=True, approver=lambda _t, _a: True)
        allowed = approved.call("execute_code", {"code": "print('ran')", "timeout": 10})
        assert allowed["ok"] is True
        assert allowed["stdout"].strip() == "ran"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
