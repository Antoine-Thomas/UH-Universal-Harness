"""P1-10: an API returns an image - the server fetches it, the browser never does.

Everything here is fail-closed, so the tests are mostly refusals: a loopback URL,
a redirect to one, an oversized body, a declared MIME that lies, an SVG. A guard
that is not tested is a comment.
"""

from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest

from uh import images

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def data_url(payload: bytes = PNG, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode()}"


def stream_response(
    body: bytes, *, status: int = 200, headers: dict | None = None
) -> httpx.Response:
    return httpx.Response(status, content=body, headers=headers or {})


# --------------------------------------------------------------------------- #
# what the bytes are, not what they claim
# --------------------------------------------------------------------------- #
def test_the_mime_comes_from_the_magic_bytes() -> None:
    assert images.sniff_mime(PNG) == "image/png"
    assert images.sniff_mime(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert images.sniff_mime(b"GIF89a...") == "image/gif"
    assert images.sniff_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert images.sniff_mime(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None
    assert images.sniff_mime(b"plain text") is None


def test_a_data_url_whose_declared_type_lies_is_trusted_only_for_its_bytes() -> None:
    normalised = images.normalise(
        [
            {"type": "text", "text": "voici"},
            {"type": "image_url", "image_url": {"url": data_url(mime="image/svg+xml")}},
        ],
        fetch=False,
    )
    assert normalised.text == "voici"
    assert normalised.artifacts[0].mime == "image/png", "the bytes decide"


# --------------------------------------------------------------------------- #
# which URL may be fetched
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8101/secret.png",
        "http://localhost/x.png",
        "http://192.168.1.10/x.png",
        "http://10.0.0.5/x.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/x.png",
    ],
)
def test_a_local_address_is_refused(url: str) -> None:
    assert "refus" in images.check_url(url) or "refused" in images.check_url(url)


@pytest.mark.parametrize(
    "url", ["file:///C:/Windows/win.ini", "ftp://host/x.png", "data:text/html,x"]
)
def test_a_non_http_scheme_is_refused(url: str) -> None:
    assert "schéma" in images.check_url(url) or "http" in images.check_url(url)


def test_credentials_in_the_url_are_refused() -> None:
    assert "identifiants" in images.check_url("https://user:pass@example.com/x.png")


def test_a_public_https_url_is_allowed() -> None:
    assert images.check_url("https://example.com/chart.png") == ""


# --------------------------------------------------------------------------- #
# the download itself
# --------------------------------------------------------------------------- #
def test_a_remote_png_becomes_an_artifact() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    artifact, reason = images.fetch_image("https://example.com/c.png", client=client)

    assert reason == ""
    assert artifact is not None and artifact.mime == "image/png"
    payload = artifact.as_payload()
    assert payload["data_url"].startswith("data:image/png;base64,")
    assert payload["bytes"] == len(PNG)


def test_a_body_over_the_cap_is_refused_whole() -> None:
    big = PNG + b"\x00" * (images.MAX_BYTES + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    artifact, reason = images.fetch_image("https://example.com/c.png", client=client)

    assert artifact is None
    assert "trop volumineuse" in reason


def test_a_declared_length_over_the_cap_is_refused_before_reading() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"", headers={"content-length": str(images.MAX_BYTES + 1)}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    artifact, reason = images.fetch_image("https://example.com/c.png", client=client)

    assert artifact is None and "trop volumineuse" in reason


def test_a_redirect_to_localhost_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8101/secret"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    artifact, reason = images.fetch_image("https://example.com/x.png", client=client)

    assert artifact is None and "redirection" in reason


def test_a_body_that_is_not_an_image_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>nope</html>", headers={"content-type": "image/png"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    artifact, reason = images.fetch_image("https://example.com/x.png", client=client)

    assert artifact is None and "format non reconnu" in reason


# --------------------------------------------------------------------------- #
# a whole answer
# --------------------------------------------------------------------------- #
def test_content_blocks_give_text_plus_artifacts() -> None:
    normalised = images.normalise(
        [
            {"type": "text", "text": "Voici le graphique."},
            {"type": "image_url", "image_url": {"url": data_url()}},
        ],
        fetch=False,
    )
    assert normalised.text == "Voici le graphique."
    assert len(normalised.artifacts) == 1


def test_a_markdown_image_keeps_its_reference_and_gains_bytes() -> None:
    markers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        markers.append(str(request.url))
        return httpx.Response(200, content=PNG)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    normalised = images.normalise("Regarde : ![courbe](https://example.com/c.png)", client=client)

    assert "![courbe](https://example.com/c.png)" in normalised.text, "the text is untouched"
    assert markers == ["https://example.com/c.png"]
    assert len(normalised.artifacts) == 1


def test_a_refused_url_becomes_a_readable_line_not_an_empty_frame() -> None:
    normalised = images.normalise("vois ![x](http://127.0.0.1:8101/secret.png)", fetch=True)
    assert normalised.artifacts == []
    note = images.note_for(normalised.refusals)
    assert "image non affichée" in note and "127.0.0.1" in note


def test_more_than_four_images_stops_at_four() -> None:
    blocks = [{"type": "image_url", "image_url": {"url": data_url()}} for _ in range(7)]
    normalised = images.normalise(blocks, fetch=False)
    assert len(normalised.artifacts) == images.MAX_IMAGES
    assert normalised.refusals, "the rest is reported, not silently dropped"


# --------------------------------------------------------------------------- #
# the seam: answer -> store -> reload, and never back to a provider
# --------------------------------------------------------------------------- #
def test_the_store_round_trips_answer_artifacts(tmp_path: Path) -> None:
    from uh.store import Store

    store = Store(tmp_path / "uh.db")
    session = store.create_session(title="t", model="m", provider="stub")
    store.append_message(
        session,
        {
            "role": "assistant",
            "content": "voici le graphique",
            "artifacts": [{"mime": "image/png", "bytes": 68, "data_url": data_url()}],
        },
    )

    rows = store.get_messages(session)
    assert rows[0]["content"] == "voici le graphique"
    assert rows[0]["artifacts"][0]["mime"] == "image/png"
    assert rows[0]["artifacts"][0]["data_url"].startswith("data:image/png;base64,")
    store.close()


def test_a_database_written_before_the_column_still_opens(tmp_path: Path) -> None:
    """The migration is additive: dropping the column is what an old row looks like."""
    import sqlite3

    from uh.store import Store

    path = tmp_path / "uh.db"
    store = Store(path)
    session = store.create_session(title="t", model="m", provider="stub")
    store.append_message(session, {"role": "user", "content": "bonjour"})
    store.close()

    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE messages DROP COLUMN artifacts")

    reopened = Store(path)  # _ensure_late_columns adds it back, in place
    rows = reopened.get_messages(session)
    assert [row["content"] for row in rows] == ["bonjour"], "no message lost"
    assert "artifacts" not in rows[0]
    reopened.close()


class ImageAnswering:
    """An API that answers with a text block and an image block."""

    name = "stub"
    model = "stub:1b"

    def chat(self, messages, tools, *, on_token=None):
        return {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Voici le graphique."},
                {"type": "image_url", "image_url": {"url": data_url()}},
            ],
            "tool_calls": [],
        }


def test_an_answer_with_an_image_reaches_the_store_and_the_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh import config as uh_config
    from uh import console
    from uh.store import Store

    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    store = Store(tmp_path / "uh.db")
    session_id = store.create_session(title="t", model="stub:1b", provider="stub")
    session = console.ChatSession(
        provider_factory=lambda kind, model: ImageAnswering(),
        provider_kind="stub",
        chain=[("stub", ImageAnswering())],
        model="stub:1b",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=False,
        store=store,
        session_id=session_id,
        max_iterations=2,
        console=console.Console(),
    )
    frames: list[dict] = []
    session.events = frames.append

    assert session.run_text("montre le graphique") == 0

    artifacts = [frame for frame in frames if frame.get("type") == "artifacts"]
    assert artifacts, "the browser is told there is an image to draw"
    assert artifacts[0]["images"][0]["data_url"].startswith("data:image/png;base64,")

    stored = store.get_messages(session_id)
    answer = [message for message in stored if message["role"] == "assistant"][0]
    assert answer["content"] == "Voici le graphique.", "the model's copy is text"
    assert answer["artifacts"][0]["mime"] == "image/png"
    store.close()


def test_a_provider_never_receives_the_answer_images() -> None:
    from uh.providers import request_messages

    message = {
        "role": "assistant",
        "content": "voici",
        "artifacts": [{"mime": "image/png", "bytes": 68, "data_url": data_url()}],
    }

    sent = request_messages([message])[0]
    assert "artifacts" not in sent, "the API is not paying for pixels twice"
    assert sent["content"] == "voici"
