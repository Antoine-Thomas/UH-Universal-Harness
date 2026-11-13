"""GET /api/telemetry: the route that feeds the panel.

The reading is exercised on a temporary UH_HOME with one recorded turn and one
schedule.log line, so the assertions are about arithmetic and shape, not about
this machine's RAM.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import decision as uh_decision  # noqa: E402
from uh.store import Store  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "telemetry-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(token=TOKEN, port=8765, workspace=tmp_path, kind="stub", model="stub:1b")


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


def run(coro):
    return asyncio.run(coro)


def _record_turn(app, *, provider: str, total: int, at: str) -> None:
    store = Store(app.state.settings.store_path)
    try:
        session_id = store.create_session(model="qwen3:8b", provider=provider)
        uh_decision.record(
            store,
            session_id,
            uh_decision.build_entry(
                provider=provider,
                model="qwen3:8b",
                served="qwen3:8b",
                usage={"prompt": total - 10, "completion": 10, "total": total},
                duration_ms=1200,
                budget=8000,
                at=at,
            ),
        )
    finally:
        store.close()


def test_the_route_needs_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.get("/api/telemetry")
            assert response.status_code == 401

    run(scenario())


def test_the_payload_has_the_three_readings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.get("/api/telemetry", headers=AUTH)
            assert response.status_code == 200
            return response.json()

    body = run(scenario())

    assert set(body) == {"generated_at", "window_days", "usage", "system", "dreams"}
    assert body["window_days"] == 7
    assert len(body["usage"]["days"]) == 7
    assert body["usage"]["cost"]["value"] is None
    assert body["usage"]["cost"]["reason"]
    assert {"cpu_percent", "ram", "vram", "cores", "gpu"} <= set(body["system"])
    assert "passes" in body["dreams"]


def test_a_recorded_turn_lands_on_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    today = datetime.now().astimezone().date().isoformat()
    _record_turn(app, provider="ollama", total=1200, at=f"{today}T09:00:00+01:00")

    async def scenario():
        async with client(app) as http:
            return (await http.get("/api/telemetry", headers=AUTH)).json()

    body = run(scenario())
    bucket = [day for day in body["usage"]["days"] if day["date"] == today][0]

    assert bucket == {"date": today, "local": 1200, "cloud": 0, "turns": 1, "estimated": 0}
    assert body["usage"]["totals"]["local"] == 1200
    assert body["usage"]["totals"]["cloud"] == 0


def test_a_cloud_turn_is_counted_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    today = datetime.now().astimezone().date().isoformat()
    _record_turn(app, provider="deepseek", total=500, at=f"{today}T10:00:00+01:00")

    async def scenario():
        async with client(app) as http:
            return (await http.get("/api/telemetry", headers=AUTH)).json()

    body = run(scenario())

    assert body["usage"]["totals"] == {
        "local": 0,
        "cloud": 500,
        "tokens": 500,
        "prompt": 490,
        "completion": 10,
        "turns": 1,
        "estimated": 0,
    }


def test_the_dream_passes_come_from_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    dreams = tmp_path / "home" / "dreams"
    dreams.mkdir(parents=True)
    (dreams / "schedule.log").write_text(
        "2026-11-13 16:29:23  [0s]  ollama: already_up\n"
        "2026-11-13 16:59:48  [163s]  ok - 2 idea(s), 2063 tokens, 164s, "
        "routed: ollama/qwen3:8b, /tmp/report.md\n",
        encoding="utf-8",
    )

    async def scenario():
        async with client(app) as http:
            return (await http.get("/api/telemetry", headers=AUTH)).json()

    body = run(scenario())
    passes = body["dreams"]["passes"]

    assert len(passes) == 1
    assert passes[0]["status"] == "ok"
    assert passes[0]["tokens"] == 2063
    assert passes[0]["model"] == "qwen3:8b"
    assert body["dreams"]["diagnostics"] == 1
