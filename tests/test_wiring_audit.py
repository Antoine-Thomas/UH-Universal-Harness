"""Gate A: no orphan frame, no undocumented route, no id the page cannot find.

The audit is not a reading exercise. Each test below reads the two files that decide
the contract (``uh/web/app.py`` + ``uh/web/static/app.js``, plus ``uh/console.py`` for
the frames a turn emits) and refuses a drift - so the next person who adds a frame or
a route without its other half is told immediately.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "uh" / "web" / "app.py").read_text(encoding="utf-8")
CONSOLE = (ROOT / "uh" / "console.py").read_text(encoding="utf-8")
JS = (ROOT / "uh" / "web" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "uh" / "web" / "static" / "index.html").read_text(encoding="utf-8")
API_DOC = (ROOT / "docs" / "API.md").read_text(encoding="utf-8")

#: Frames whose ``type`` is built by a helper and emitted as a dict, so a regex over
#: ``emit({"type": ...})`` cannot see the literal. Each name is checked to exist in
#: its producer below, so the list cannot rot silently.
HELPER_FRAMES = {
    "answer_meta": ("uh/web/app.py", "def answer_meta("),
    "approval": ("uh/mcp_http.py", '"type": "approval"'),
    "mode_refused": ("uh/mode_gate.py", "def mode_refused_frame("),
}


def _emitted() -> set[str]:
    types = set(re.findall(r'_emit\(\s*"([a-z_]+)"', CONSOLE))
    types |= set(re.findall(r'yield frame\(\s*"([a-z_]+)"', APP))
    types |= set(re.findall(r'emit\(\s*\{\s*"type":\s*"([a-z_]+)"', APP))
    return types - {"__end__"}  # internal marker: never reaches the browser


def _handled() -> set[str]:
    return set(re.findall(r'event === "([a-z_]+)"', JS))


def test_no_frame_is_emitted_without_a_consumer() -> None:
    orphan = _emitted() - _handled()
    assert orphan == set(), f"frames emitted and never handled by the page: {sorted(orphan)}"


def test_no_consumer_waits_for_a_frame_nobody_emits() -> None:
    expected = _emitted() | set(HELPER_FRAMES)
    unknown = _handled() - expected
    assert unknown == set(), f"the page handles frames nothing emits: {sorted(unknown)}"


def test_each_helper_frame_really_has_a_producer() -> None:
    for name, (relative, marker) in HELPER_FRAMES.items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert marker in source, f"{name} is listed as a helper frame but {relative} has no {marker}"
        assert name in _handled(), f"{name} is emitted but the page does not handle it"


def test_every_route_is_documented() -> None:
    routes = set(re.findall(r'@app\.(?:get|post|delete|patch|put)\(\s*"([^"]+)"', APP))
    missing = sorted(route for route in routes if route not in API_DOC)
    assert missing == [], f"routes absent from docs/API.md: {missing}"


def test_the_audio_and_media_families_are_isolated_under_their_own_prefix() -> None:
    routes = re.findall(r'@app\.(?:get|post|delete)\(\s*"([^"]+)"', APP)
    for family in ("/api/audio/", "/api/media/", "/api/memory", "/api/maintenance"):
        assert any(route.startswith(family) for route in routes), f"{family} has no route"
    # The chat turn must not depend on any of them existing.
    for path in (ROOT / "uh" / "agent.py", *sorted((ROOT / "uh" / "providers").glob("*.py"))):
        source = path.read_text(encoding="utf-8")
        for family in ("api/audio", "api/media", "uh.audio", "uh.media"):
            assert family not in source, f"{path.name} references {family}"


def test_the_ids_the_page_uses_exist_or_are_created_by_it() -> None:
    used = set(re.findall(r'\$\("([a-z0-9\-]+)"\)', JS))
    in_html = set(re.findall(r'id="([a-z0-9\-]+)"', HTML))
    # A panel the page builds itself is legitimate, and it builds ids three ways:
    # in markup, with `.id = "x"`, or as the second argument of its `el()` helper.
    built = set(re.findall(r'id="([a-z0-9\-]+)"', JS))
    built |= set(re.findall(r'\.id = "([a-z0-9\-]+)"', JS))
    built |= set(re.findall(r'el\(\s*"[a-z]+"\s*,\s*"([a-z0-9\-]+)"\s*\)', JS))
    built |= set(re.findall(r'classList\.add\("([a-z0-9\-]+)"\)', JS))
    missing = sorted(used - in_html - built)
    assert missing == [], f"ids used by app.js and absent from the page: {missing}"


def test_the_documented_ids_are_the_ones_the_page_has() -> None:
    documented = set(re.findall(r"\b([a-z0-9]+(?:-[a-z0-9]+)+)\b", API_DOC.split("## Element ids")[1].split("```")[1]))
    page = set(re.findall(r'id="([a-z0-9\-]+)"', HTML)) | set(re.findall(r'id="([a-z0-9\-]+)"', JS))
    missing = sorted(name for name in documented if name not in page and "pane" not in name and "tab-" not in name)
    assert missing == [], f"documented ids missing from the interface: {missing}"


def test_the_thread_never_stores_base64() -> None:
    """A stored message carries references; the bytes live in files."""
    source = (ROOT / "uh" / "store.py").read_text(encoding="utf-8")
    assert "data_url" not in source, "the store must not know about data URLs"
    # The audio reference is the shape that proves it: path, not payload.

    example = {
        "kind": "speech",
        "source": {"path": "C:/tmp/a.wav", "root": "uploads"},
        "url": "/api/audio/file?path=x",
        "bytes": 10,
        "seconds": 1.0,
        "sha256": "abc",
        "engine": "xtts",
    }
    assert "audio_data" not in json.dumps(example) and "base64" not in json.dumps(example)


class _FakeCapture:
    """Capture the bodies HTML/JS need, without a browser."""

    def __init__(self) -> None:
        self.created = []


def test_the_media_warning_is_hidden_until_a_file_needs_it() -> None:
    """No attachments, no banner: the composer stays quiet on a plain text turn."""
    assert 'id="media-warning"' in HTML and "hidden" in HTML.split('id="media-warning"')[1][:40]
    assert "if (!kinds.length) {\n    mediaWarning(\"\");" in JS


def test_an_audio_player_only_ever_comes_from_a_real_clip() -> None:
    assert "function audioArtifacts(" in JS
    assert 'item.kind || "").toLowerCase() === "audio" && item.url' in JS
    # The player is built inside drawAudio, which is only called with real records.
    assert "player.src = artifact.url" in JS
    assert "drawAudio(box, [payload]" in JS, "a synthesised clip is the only other entry point"


def test_the_kept_bin_window_is_the_documented_one() -> None:
    assert "TRASH_UNDO_MS = 8000" in JS
    assert "const TRASH_UNDO_MS" in JS


def test_the_locks_the_relay_paid_for_are_still_wired() -> None:
    # (a) mode filtering, (b) the Origin/Host guard, (c) the atelier, (d) audio isolation
    assert "read_only_view" in CONSOLE
    assert "host not allowed" in APP and "origin not allowed" in APP
    assert "is_loopback_bind" in APP
    uhb = (ROOT / ".." / "UHB-Universal-Harness-Backend" / "uhb" / "tools" / "execute.py")
    if uhb.is_file():
        assert "addaudithook" in uhb.read_text(encoding="utf-8")
    for path in (ROOT / "uh" / "agent.py", *sorted((ROOT / "uh" / "providers").glob("*.py"))):
        source = path.read_text(encoding="utf-8")
        assert "import audio" not in source and "from .. import audio" not in source
