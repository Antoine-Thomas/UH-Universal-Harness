"""P0-5, commit 1: the images an interface can show, kept out of the model's text.

The defect this closes: `_render()` described an image as
`[image: image/png, 39464 base64 characters]` and threw the bytes away, so a tool
that returned a chart could never be displayed — by anyone, at any point.

The decision the brief asked to take here, before touching the UI:

**the data URL is stored inside the tool message**, therefore inside SQLite, and a
reloaded session shows the chart without asking the server again. The price is
stated and bounded rather than discovered later: 512 KB decoded per image, four
images per result. Past that the artefact says `omitted: "too_large"` and carries
no data — half a PNG is not a picture, and a silent truncation is worse than a
missing one.

`svg` is absent from the whitelist on purpose: it is a document with scripts, and
an `<img>` is not the place to find that out.
"""

from __future__ import annotations

import base64
import json

import pytest

from uh import mcp
from uh.store import Store

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 200).decode()


def payload(*blocks: dict) -> dict:
    return {"content": list(blocks)}


def image(mime: str = "image/png", data: str = PNG) -> dict:
    return {"type": "image", "mimeType": mime, "data": data}


def test_an_allowed_image_becomes_an_artefact_with_its_bytes() -> None:
    artefacts = mcp._artifacts(payload(image())["content"])

    assert len(artefacts) == 1
    assert artefacts[0]["mime"] == "image/png"
    assert artefacts[0]["bytes"] == len(base64.b64decode(PNG))
    assert artefacts[0]["data_url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("mime", ["image/svg+xml", "image/bmp", "text/html", ""])
def test_anything_but_the_whitelist_is_not_an_artefact(mime: str) -> None:
    assert mcp._artifacts(payload(image(mime=mime))["content"]) == []


def test_the_model_never_receives_the_base64() -> None:
    """The whole point of the split: text for the model, bytes for the interface."""
    text, _ = mcp._render(payload({"type": "text", "text": "voici"}, image()))

    assert "artefact 1" in text
    assert PNG not in text, "a base64 PNG is tens of thousands of useless tokens"
    assert len(text) < 200


def test_an_image_above_the_cap_is_reported_not_truncated() -> None:
    big = base64.b64encode(b"x" * (mcp.MAX_ARTIFACT_BYTES + 1)).decode()

    artefacts = mcp._artifacts(payload(image(data=big))["content"])

    assert artefacts[0]["omitted"] == "too_large"
    assert "data_url" not in artefacts[0], "half an image is not an image"


def test_only_the_first_four_are_kept() -> None:
    artefacts = mcp._artifacts(payload(*[image() for _ in range(6)])["content"])

    assert len(artefacts) == mcp.MAX_ARTIFACTS, "a result cannot grow without a ceiling"


def test_corrupt_base64_is_skipped_not_raised() -> None:
    assert mcp._artifacts(payload(image(data="pas du base64 !!"))["content"]) == []


def test_the_artefact_survives_a_reload_because_it_lives_in_the_message(tmp_path) -> None:
    """The decision, measured: no schema change, no second request, no disk file.

    The tool payload is what the agent loop serialises into the message content —
    so storing it is enough for a session to show the image again after a restart.
    If this test ever fails, the persistence decision has to be taken again.
    """
    artefacts = mcp._artifacts(payload(image())["content"])
    message = {
        "role": "tool",
        "name": "screenshot",
        "tool_call_id": "call_img",
        "content": json.dumps({"server": "positron", "tool": "screenshot",
                               "text": "voici", "artifacts": artefacts}),
    }

    store = Store(tmp_path / "uh.db")
    try:
        session_id = store.create_session(model="stub", provider="stub")
        store.append_message(session_id, {"role": "user", "content": "montre"})
        store.append_message(session_id, message)
        stored = store.get_messages(session_id)
    finally:
        store.close()

    reloaded = json.loads(stored[1]["content"])
    assert reloaded["artifacts"][0]["data_url"] == artefacts[0]["data_url"]
    assert stored[1]["tool_call_id"] == "call_img"
    # And the price of that choice, measured rather than assumed:
    assert len(message["content"]) < 2_000_000, "one result stays under ~2 MB in SQLite"
