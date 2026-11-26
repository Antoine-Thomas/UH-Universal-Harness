"""Point 2: a read-only mode is not offered what it forbids, and names the cause.

Two halves, tested separately and then together:

* the **offer** — in `chat`, the turn is handed the read-only view of the
  catalogue, so a model is not invited to write and then refused;
* the **answer** — a call to a tool the mode forbids (a replay, an invention, a
  model that ignores its catalogue) comes back typed `mode_forbidden` with the
  mode that refused it, which is what the browser turns into « Autoriser pour
  cette conversation ». It must never reach the approval gate: there is nothing
  to authorise about a door the mode has already locked.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from uh import config as uh_config
from uh import console, mode_gate
from uh.agent import _execute
from uh.toolsets import uhb as uhb_mod

URL = "http://127.0.0.1:8101"


# --------------------------------------------------------------------------- #
# the offer
# --------------------------------------------------------------------------- #
class FakeToolset:
    name = "file"

    def __init__(self, names: tuple[str, ...]) -> None:
        self.tools = [
            {"type": "function", "function": {"name": name, "parameters": {}}} for name in names
        ]
        self.called: list[str] = []

    def call(self, name: str, args: dict) -> dict:
        self.called.append(name)
        return {"ok": True}


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> console.ChatSession:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))

    class Quiet:
        model = "stub:1b"

        def chat(self, messages, tools, *, on_token=None):
            return {"role": "assistant", "content": "ok", "tool_calls": []}

    return console.ChatSession(
        provider_factory=lambda kind, model: Quiet(),
        provider_kind="stub",
        chain=[("stub", Quiet())],
        model="stub:1b",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=False,
        store=None,
        max_iterations=2,
        mode=mode,
    )


def _offered(toolsets: dict) -> set[str]:
    return {
        str((spec.get("function") or {}).get("name") or "")
        for toolset in toolsets.values()
        for spec in (getattr(toolset, "tools", None) or [])
    }


def test_a_read_only_mode_offers_only_reading_tools(tmp_path: Path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch, "chat")
    session.toolsets = {
        "file": FakeToolset(("read_file", "write_file", "patch")),
        "terminal": FakeToolset(("terminal",)),
        "memory": FakeToolset(("memory_read", "memory_write")),
        "web_search": FakeToolset(("web_search",)),
    }

    offered = session._intent_toolsets([{"role": "user", "content": "écris un fichier"}])

    assert _offered(offered) == {"read_file", "memory_read", "web_search"}
    assert "terminal" not in offered, "an empty toolset is not an offer"
    assert set(session.last_withheld) == {"write_file", "patch", "terminal", "memory_write"}


def test_the_withheld_names_are_the_dangerous_ones(tmp_path: Path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch, "chat")
    session.toolsets = {"file": FakeToolset(("write_file", "patch"))}

    offered = session._intent_toolsets([])

    assert offered == {}, "nothing to offer is nothing to offer"
    assert mode_gate.READ_ONLY_TOOLS.isdisjoint(session.last_withheld)


def test_a_writing_mode_is_untouched(tmp_path: Path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch, "dev")
    session.toolsets = {"file": FakeToolset(("read_file", "write_file"))}

    offered = session._intent_toolsets([])

    assert _offered(offered) == {"read_file", "write_file"}
    assert session.last_withheld == []


def test_an_unknown_mode_narrows_nothing(tmp_path: Path, monkeypatch) -> None:
    """A dead UHB is not a reason to guess a read-only profile."""
    session = _session(tmp_path, monkeypatch, "")
    session.toolsets = {"file": FakeToolset(("read_file", "write_file"))}

    assert _offered(session._intent_toolsets([])) == {"read_file", "write_file"}


def test_the_read_only_view_still_calls_the_real_toolset(tmp_path: Path, monkeypatch) -> None:
    """The view decides what is offered; it must not become a second implementation."""
    session = _session(tmp_path, monkeypatch, "chat")
    real = FakeToolset(("read_file", "write_file"))
    session.toolsets = {"file": real}

    offered = session._intent_toolsets([])

    assert offered["file"].call("read_file", {"path": "a"}) == {"ok": True}
    assert real.called == ["read_file"]


# --------------------------------------------------------------------------- #
# the answer: mode_forbidden, and no approval
# --------------------------------------------------------------------------- #
def _catalogue_chat_mode() -> dict:
    return {
        "tools": [
            {
                "name": "read_file",
                "description": "Read a file.",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                "execution_type": "in_process",
                "available": True,
                "available_in": ["chat", "dev"],
            },
            {
                "name": "execute_code",
                "description": "Run Python.",
                "parameters": {"type": "object", "properties": {"code": {"type": "string"}}},
                "execution_type": "subprocess",
                "available": False,
                "available_in": ["dev", "review"],
            },
        ]
    }


def _toolset_in_chat_mode(approver=None) -> uhb_mod.Uhbtoolset:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"connected": True, "mode": "chat", "version": "0.2.0"})
        return httpx.Response(200, json=_catalogue_chat_mode())

    return uhb_mod.Uhbtoolset(
        URL,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        discover=True,
        approver=approver,
    )


def test_a_withheld_uh_tool_explains_itself(tmp_path: Path, monkeypatch) -> None:
    """A hallucinated `write_file` in chat is a mode refusal, not an unknown name."""
    session = _session(tmp_path, monkeypatch, "chat")
    session.toolsets = {"file": FakeToolset(("read_file", "write_file"))}
    offer = session._intent_toolsets([])

    message, refused = _execute({}, "write_file", {"path": "a"}, "call_w", offer)

    assert refused is True
    payload = json.loads(message["content"])
    assert payload["reason"] == "mode_forbidden", "the browser keys the card on this string"
    assert payload["mode"] == "chat"
    assert payload["tool"] == "write_file"


def test_the_frame_that_builds_the_card_is_keyed_on_mode_forbidden() -> None:
    """The link between the tool result and the browser's « Autoriser » card."""
    content = json.dumps(
        {
            "error": "…",
            "type": "forbidden",
            "tool": "write_file",
            "reason": "mode_forbidden",
            "mode": "chat",
        }
    )

    frame = mode_gate.mode_refused_frame("write_file", content)
    assert frame == {"type": "mode_refused", "tool": "write_file", "mode": "chat"}

    assert mode_gate.mode_refused_frame("read_file", '{"ok": true}') is None, (
        "an ordinary tool result must not draw a card"
    )
    # A payload that says mode_forbidden but is not JSON still earns the card:
    # the sentence is the signal, the mode is the detail.
    assert mode_gate.mode_refused_frame("x", "mode_forbidden (le mode refuse)") == {
        "type": "mode_refused",
        "tool": "x",
        "mode": "chat",
    }


def test_a_mode_forbidden_tool_is_named_with_its_mode() -> None:
    toolset = _toolset_in_chat_mode()

    assert "execute_code" not in [spec["function"]["name"] for spec in toolset.tools]
    assert "execute_code" in toolset.blocked

    answer = toolset.explain_missing("execute_code")
    assert answer is not None
    assert answer["reason"] == "mode_forbidden"
    assert answer["mode"] == "chat"
    assert answer["available_in"] == ["dev", "review"]
    assert "chat" in answer["error"]

    assert toolset.explain_missing("read_file") is None, "an offered tool is not missing"
    assert toolset.explain_missing("invented_tool") is None, "« unknown » stays unknown"


def test_a_forbidden_tool_never_asks_for_an_approval() -> None:
    asked: list[str] = []

    def approver(tool: str, arguments: dict) -> bool:
        asked.append(tool)
        return True

    toolset = _toolset_in_chat_mode(approver=approver)

    result = toolset.call("execute_code", {"code": "print(1)"})

    assert result["type"] == "forbidden"
    assert asked == [], "the gate is asked about offered tools only"
    assert toolset.explain_missing("execute_code")["reason"] == "mode_forbidden"
    assert asked == [], "an explanation is not a permission either"


def test_the_agent_types_the_refusal_so_the_browser_can_offer_the_card() -> None:
    toolset = _toolset_in_chat_mode()

    message, refused = _execute({}, "execute_code", {}, "call_1", {"uhb": toolset})

    assert refused is True
    payload = json.loads(message["content"])
    assert payload["reason"] == "mode_forbidden", "the browser keys the card on this string"
    assert payload["mode"] == "chat"
    assert payload["tool"] == "execute_code"
    assert message["tool_call_id"] == "call_1"


def test_without_a_mode_aware_toolset_the_message_is_unchanged() -> None:
    """The switch case, and a plain unknown name, keep their own wording."""
    message, refused = _execute({}, "execute_code", {}, "call_2")

    payload = json.loads(message["content"])
    assert refused is True
    assert payload["reason"] == "execute_code_not_exposed"

    other, _ = _execute({}, "invented_tool", {}, "call_3")
    assert json.loads(other["content"])["reason"] == "unknown_tool"


def test_a_read_only_mode_still_offers_the_photo_search() -> None:
    """Chercher une photo n'écrit rien : le mode `chat` doit l'offrir.

    `web_image_search` est né après `READ_ONLY_TOOLS` (G11) et n'y avait pas été
    ajouté : le mode en lecture seule, seul mode livré avec le produit, retirait
    donc l'outil et la demande de photo redevenait une recherche texte. Relevé par
    la revue de la PR #78.
    """
    toolset = FakeToolset(("read_file", "web_search", "web_image_search", "write_file"))

    view = mode_gate.ReadOnlyToolset(toolset, "chat")
    offered = [spec["function"]["name"] for spec in view.tools]

    assert "web_image_search" in offered
    assert view.explain_missing("web_image_search") is None
    assert "write_file" not in offered
    assert (view.explain_missing("write_file") or {}).get("reason") == "mode_forbidden"
