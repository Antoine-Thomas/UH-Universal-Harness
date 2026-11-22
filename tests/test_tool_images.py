"""P1-13: the images a tool produced join the turn's artifacts, and nothing else does.

``execute_code`` (UHB) answers with ``images: [{mime, bytes, data_url}]`` once the
snippet wrote files. Those images have to reach the conversation the same way a
provider's own image block does — same payload shape, same browser path — while
staying under the rules that already exist: only an inlined data URL, MIME read
from the bytes, and only the tool messages of *this* turn.

Measured before this lot (2026-11-18): the UHB environment had neither numpy nor
matplotlib, so a chart could not be drawn at all; and UH only harvested images
from the assistant's text, never from a tool result.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from uh.console import ChatSession

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = (ROOT / "uh" / "console.py").read_text(encoding="utf-8")

#: A 1x1 PNG, the smallest thing the browser will actually draw.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()


def session_with(history: list[dict]) -> ChatSession:
    session = ChatSession.__new__(
        ChatSession
    )  # no server, no console: the helper only reads history
    session.history = history
    session.events = None
    return session


def tool_message(name: str, payload: dict) -> dict:
    return {"role": "tool", "name": name, "content": json.dumps(payload)}


def test_an_execute_code_image_becomes_an_artifact() -> None:
    session = session_with(
        [
            {"role": "user", "content": "trace la courbe"},
            tool_message(
                "execute_code",
                {"images": [{"mime": "image/png", "bytes": len(PNG), "data_url": PNG_URL}]},
            ),
            {"role": "assistant", "content": "Voici la courbe."},
        ]
    )

    payloads = session._tool_images(0)

    assert len(payloads) == 1
    assert payloads[0]["mime"] == "image/png"
    assert payloads[0]["bytes"] == len(PNG)
    assert payloads[0]["data_url"].startswith("data:image/png;base64,")


def test_a_data_url_that_is_not_an_image_is_refused() -> None:
    text_url = "data:text/plain;base64," + base64.b64encode(b"not an image").decode()
    session = session_with(
        [tool_message("execute_code", {"images": [{"mime": "image/png", "data_url": text_url}]})]
    )

    assert session._tool_images(0) == [], "le MIME vient des octets, pas du champ declare"


def test_an_image_from_another_tool_is_ignored() -> None:
    session = session_with(
        [tool_message("read_file", {"images": [{"mime": "image/png", "data_url": PNG_URL}]})]
    )

    assert session._tool_images(0) == []


def test_only_this_turns_tool_messages_are_harvested() -> None:
    history = [
        tool_message("execute_code", {"images": [{"mime": "image/png", "data_url": PNG_URL}]}),
        {"role": "user", "content": "et maintenant un autre graphe"},
    ]
    session = session_with(history)

    assert session._tool_images(0), "dans la fenetre, l'image est vue"
    assert session._tool_images(1) == [], "un tour ne rejoue pas les images du precedent"


def test_a_path_without_a_data_url_is_not_read() -> None:
    # UHB can answer with a path when the caller did not ask for inline images: the
    # browser must never receive a filesystem path, and nothing is read from disk.
    session = session_with(
        [
            tool_message(
                "execute_code", {"images": [{"path": r"C:\tmp\run\chart.png", "data_url": None}]}
            )
        ]
    )

    assert session._tool_images(0) == []


def test_the_prompt_asks_for_a_drawn_chart_not_a_tutorial() -> None:
    assert "When the user asks for a chart, a plot, a diagram or a map, draw it" in CONSOLE
    assert "execute_code and have it save a .png" in CONSOLE
    assert "Agg backend" in CONSOLE, "sans serveur graphique, matplotlib doit etre en Agg"
    assert "Never answer such a request " in CONSOLE, "et jamais un tutoriel a la place"
    # Measured on a real turn (2026-11-18): asked to save a PNG, qwen3:8b wrote to
    # Desktop/UH_OUTPUTS. UHB only collects images from the run directory, so the
    # turn produced no artifact and the user saw a file:// path. The prompt says
    # where, now.
    assert "bare file name" in CONSOLE and "no subfolder" in CONSOLE


class _QuietConsole:
    """The turn only talks to its console for diagnostics: record, never print."""

    def __init__(self) -> None:
        self.details: list[str] = []

    def detail(self, message: str) -> None:
        self.details.append(message)

    def errout(self, message: str) -> None:
        self.details.append(message)

    def stream_write(self, text: str) -> None:  # pragma: no cover - not reached here
        del text


def test_the_finished_turn_attaches_the_tool_images_and_emits_them_once() -> None:
    """The bridge itself: tool images reach ``message["artifacts"]`` and one event."""
    events: list[dict] = []
    session = ChatSession.__new__(ChatSession)
    session.history = [
        {"role": "user", "content": "trace la courbe"},
        tool_message("execute_code", {"images": [{"mime": "image/png", "data_url": PNG_URL}]}),
        {"role": "assistant", "content": "Voici la courbe."},
    ]
    session.events = events.append
    session.console = _QuietConsole()

    session._normalise_answer_images(0)

    artifacts = session.history[-1].get("artifacts") or []
    assert len(artifacts) == 1 and artifacts[0]["mime"] == "image/png"
    emitted = [event for event in events if event.get("type") == "artifacts"]
    assert len(emitted) == 1, "un seul evenement, sinon l'image est dessinee deux fois"
    assert emitted[0]["images"] == artifacts


def test_the_base64_leaves_the_tool_message() -> None:
    """Codex review on PR #25: a 14 KB chart is thousands of tokens, twice over.

    The bytes belong to ``message["artifacts"]``. What stays in the ``role: tool``
    message is what the model may read on the next turn: no data URL, and a marker
    that says the image was attached.
    """
    session = session_with(
        [
            tool_message(
                "execute_code",
                {
                    "ok": True,
                    "stdout": "figure ecrite",
                    "images": [{"mime": "image/png", "bytes": len(PNG), "data_url": PNG_URL}],
                },
            )
        ]
    )

    payloads = session._tool_images(0)

    assert len(payloads) == 1, "les octets sont bien recoltes"
    stored = json.loads(session.history[0]["content"])
    image = stored["images"][0]
    assert "data_url" not in image, "le base64 ne doit jamais rester dans le message outil"
    assert "base64 retire" in image["attached"]
    assert stored["stdout"] == "figure ecrite", "le reste du resultat est intact"
    assert stored["ok"] is True


def test_a_retry_in_the_same_turn_does_not_draw_the_chart_twice() -> None:
    """Codex review on PR #25: the empty-answer nudge normalises the same window twice."""
    events: list[dict] = []
    session = ChatSession.__new__(ChatSession)
    session.history = [
        {"role": "user", "content": "trace la courbe"},
        tool_message("execute_code", {"images": [{"mime": "image/png", "data_url": PNG_URL}]}),
        {"role": "assistant", "content": ""},
    ]
    session.events = events.append
    session.console = _QuietConsole()
    session._artifact_keys = set()

    session._normalise_answer_images(0)
    # The nudge produced a second assistant message, and the turn normalises again.
    session.history.append({"role": "assistant", "content": "Voici la courbe."})
    session._normalise_answer_images(0)

    emitted = [event for event in events if event.get("type") == "artifacts"]
    assert len(emitted) == 1, "un deuxieme evenement dessinerait le meme graphique en double"
    assert len(emitted[0]["images"]) == 1
    assert not session.history[-1].get("artifacts"), "la seconde bulle n'herite pas d'un doublon"


def test_two_different_charts_in_one_turn_are_both_kept() -> None:
    """Deduplication is by bytes, not by count: two different images stay two."""
    other = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    other_url = "data:image/png;base64," + base64.b64encode(other).decode()
    events: list[dict] = []
    session = ChatSession.__new__(ChatSession)
    session.history = [
        tool_message("execute_code", {"images": [{"mime": "image/png", "data_url": PNG_URL}]}),
        tool_message("execute_code", {"images": [{"mime": "image/png", "data_url": other_url}]}),
        {"role": "assistant", "content": "Deux courbes."},
    ]
    session.events = events.append
    session.console = _QuietConsole()

    session._normalise_answer_images(0)

    assert len(session.history[-1]["artifacts"]) == 2
    assert len([e for e in events if e.get("type") == "artifacts"]) == 1


# --------------------------------------------------------------------------- #
# The request side: UHB inlines only when asked (measured 2026-11-18 on a real
# T10 turn — the model never passes `include_base64`, so every image came back
# as `data_url: null` and the transcript could only show a path).
# --------------------------------------------------------------------------- #
def test_execute_code_asks_uhb_to_inline_the_images(monkeypatch) -> None:
    """The chat renders data URLs, so the client asks for them — once, always."""
    import httpx

    from uh.toolsets.uhb import Uhbtoolset

    monkeypatch.setenv("UH_UHB_EXECUTE", "1")  # a subprocess tool is published only on the switch

    posted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "tools": [
                        {
                            "name": "execute_code",
                            "description": "run python",
                            "parameters": {"type": "object", "properties": {}},
                            "execution_type": "subprocess",
                        }
                    ]
                },
            )
        if request.url.path == "/tools/approvals":
            return httpx.Response(200, json={"approval_id": "nonce", "tool": "execute_code"})
        posted.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json={"ok": True, "images": []})

    toolset = Uhbtoolset(
        "http://127.0.0.1:8101",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        discover=True,
        approver=lambda _tool, _arguments: True,
    )

    toolset.call("execute_code", {"code": "print(1)"})

    assert posted and posted[0]["include_base64"] is True
    assert posted[0]["code"] == "print(1)"


def test_a_model_cannot_turn_the_inlining_off(monkeypatch) -> None:
    """`include_base64: false` breaks the transcript, so it is not the model's call."""
    import httpx

    from uh.toolsets.uhb import Uhbtoolset

    monkeypatch.setenv("UH_UHB_EXECUTE", "1")  # a subprocess tool is published only on the switch

    posted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "tools": [
                        {
                            "name": "execute_code",
                            "description": "run python",
                            "parameters": {"type": "object", "properties": {}},
                            "execution_type": "subprocess",
                        }
                    ]
                },
            )
        if request.url.path == "/tools/approvals":
            return httpx.Response(200, json={"approval_id": "nonce", "tool": "execute_code"})
        posted.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json={"ok": True, "images": []})

    toolset = Uhbtoolset(
        "http://127.0.0.1:8101",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        discover=True,
        approver=lambda _tool, _arguments: True,
    )

    toolset.call("execute_code", {"code": "print(1)", "include_base64": False})

    assert posted and posted[0]["include_base64"] is True


# --------------------------------------------------------------------------- #
# Provenance: the figure keeps the file it came from, server-side only, so the
# Keep / Trash / vote buttons have something real to act on (P1-11).
# --------------------------------------------------------------------------- #
def test_the_artifact_keeps_the_provenance_of_its_file() -> None:
    session = session_with(
        [
            tool_message(
                "execute_code",
                {
                    "run_dir": r"C:\Users\searc\.uhb\runs\run-20260101-120000-abcdef",
                    "images": [
                        {
                            "mime": "image/png",
                            "data_url": PNG_URL,
                            "path": r"C:\Users\searc\.uhb\runs\run-1\chart.png",
                            "relative": "chart.png",
                        }
                    ],
                },
            )
        ]
    )

    payloads = session._tool_images(0)

    assert payloads and payloads[0]["source"]["relative"] == "chart.png"
    assert payloads[0]["source"]["path"].endswith("chart.png")
    assert payloads[0]["source"]["run_dir"].endswith("run-20260101-120000-abcdef")
    assert "source" not in json.loads(session.history[0]["content"]), (
        "the path stays in the message's artifacts, never in what the model re-reads"
    )
