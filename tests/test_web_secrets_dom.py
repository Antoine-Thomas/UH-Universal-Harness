"""A key is written once, then never seen again: not in a response, not in a log.

The proof has three legs, and each is checked where it can fail:

* the API answer carries a masked hint only (`sk-…abcd`), never the value;
* the page never puts a stored key back into an input (source check - the value
  is not in the DOM after submission because it is not sent to the page at all);
* nothing written to the logging stack during the request contains the key,
  which is what "never in the logs" actually means.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-secrets"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEY = "sk-live-0123456789abcdef-NEVER-SHOW-THIS"
APP_JS = (Path(__file__).resolve().parents[1] / "uh" / "web" / "static" / "app.js").read_text(
    encoding="utf-8"
)


class Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001 - a broken format string is not the point
            self.lines.append(str(record.msg))


@contextmanager
def serve(app, port: int) -> Iterator[str]:
    # log_config=None keeps uvicorn from resetting the logging config, so the
    # collector below actually sees the access records it asserts on.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, port: int):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UH_HOME", str(home))
    return create_app(
        token=TOKEN,
        port=port,
        host="127.0.0.1",
        workspace=tmp_path,
        provider_factory=lambda kind, model: None,
        kind="stub",
        model="stub:1b",
    )


def test_a_saved_key_is_masked_in_the_answer_and_absent_from_the_logs(
    tmp_path, monkeypatch
) -> None:
    port = free_port()
    app = make_app(tmp_path, monkeypatch, port)
    collector = Collector()
    # The key could leak through our own logging or through the access log: both
    # are watched, and the access log is the one that sees every request line.
    watched = [logging.getLogger(), logging.getLogger("uvicorn.access")]
    for logger in watched:
        logger.addHandler(collector)
    previous = logging.getLogger().level
    logging.getLogger().setLevel(logging.INFO)
    try:
        with serve(app, port) as base:
            with httpx.Client(base_url=base, headers=AUTH, timeout=20.0) as http:
                stored = http.post("/api/providers/deepseek/key", json={"key": KEY})
                assert stored.status_code == 200, stored.text
                assert KEY not in stored.text
                listed = http.get("/api/providers")
                assert listed.status_code == 200
                assert KEY not in listed.text
    finally:
        for logger in watched:
            logger.removeHandler(collector)
        logging.getLogger().setLevel(previous)

    # What the panel receives instead: at most 3 leading + 4 trailing characters.
    payload = stored.json()
    hints = payload.get("key_hints") or []
    assert hints, f"a masked hint is expected, got {payload}"
    assert hints[0].startswith("sk-"), hints
    assert hints[0].endswith("THIS"), hints
    assert len(hints[0]) < len(KEY) / 2, "a hint is not half the key"
    assert "NEVER-SHOW" not in str(payload)

    logged = "\n".join(collector.lines)
    assert "POST /api/providers/deepseek/key" in logged, "the access log did run"
    assert KEY not in logged, "the key reached the logging stack"
    assert "NEVER-SHOW" not in logged


def test_the_page_never_receives_a_stored_key() -> None:
    """The hint is what the panel renders; no code path copies a key into an input."""
    assert "key_hints" in APP_JS
    # The only writes into key inputs are the user's own typing (the field is
    # cleared after a save), never a value read from the API.
    for marker in ("input.value = data.key", "value = section.key", "value = key."):
        assert marker not in APP_JS, f"a stored key could land in the DOM: {marker}"
    assert "keys are write-only: only a masked hint is shown" in APP_JS


def test_the_key_file_itself_is_owner_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from uh import keys as keys_mod

    monkeypatch.setenv("UH_HOME", str(tmp_path))
    path = keys_mod.add_provider("deepseek", "https://api.deepseek.com/v1", "deepseek-chat", KEY)
    assert KEY in path.read_text(encoding="utf-8")
    if os.name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600
