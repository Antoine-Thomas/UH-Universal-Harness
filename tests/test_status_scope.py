"""The status of a session is that session's model, not the global default.

Gate B caught the header announcing `auto/best-free` while the session it was showing
ran on `qwen2.5:7b`: two models on screen, which is exactly what the interface rules
forbid. The route now answers the pair of the session it is asked about.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-status-session"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class StubProvider:
    model = "stub:1b"

    def chat(self, messages, tools=None, stream=False, **kwargs):  # pragma: no cover
        raise RuntimeError("no turn runs here")


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=tmp_path,
        provider_factory=lambda kind, model: StubProvider(),
        kind="stub",
        model="stub:default",
    )


def get(app, path: str) -> httpx.Response:
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            return await http.get(path, headers=AUTH)

    return asyncio.run(run())


def test_without_a_session_the_status_reports_the_global_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    payload = get(app, "/api/status").json()

    assert (payload["provider"], payload["model"]) == ("stub", "stub:default")


def test_with_a_session_the_status_reports_that_session_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    app.state.session_models["20261119-0000-aaaa"] = ("ollama", "qwen3.5:9b")

    payload = get(app, "/api/status?session_id=20261119-0000-aaaa").json()

    assert (payload["provider"], payload["model"]) == ("ollama", "qwen3.5:9b")
    assert payload["version"], "the rest of the payload is unchanged"


def test_an_unknown_session_falls_back_to_the_global_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    payload = get(app, "/api/status?session_id=inconnue").json()
    assert (payload["provider"], payload["model"]) == ("stub", "stub:default")


def test_the_page_asks_for_the_session_status() -> None:
    import re

    source = (Path(__file__).resolve().parents[1] / "uh" / "web" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    assert re.search(r"api\(`/api/status\$\{query\}`\)", source), (
        "loadStatus must pass the session id, or the header goes back to the default"
    )
    assert "?session_id=" in source
