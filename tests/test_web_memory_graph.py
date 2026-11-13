"""GET /api/memory/graph: the nodes and the edges, and which ones are real.

The graph's value is that it refuses to draw a wire UH does not have. These tests
pin the counts it can measure and the three sources it must mark unwired.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh.web.app import create_app  # noqa: E402

TOKEN = "graph-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("UH_UHB_URL", "")
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(token=TOKEN, port=8765, workspace=tmp_path, kind="stub", model="stub:1b")


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


def run(coro):
    return asyncio.run(coro)


def test_the_graph_needs_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return (await http.get("/api/memory/graph")).status_code

    assert run(scenario()) == 401


def test_the_seven_nodes_are_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return (await http.get("/api/memory/graph", headers=AUTH)).json()

    body = run(scenario())
    ids = {node["id"] for node in body["nodes"]}
    assert ids == {"memory", "wiki", "rag", "siyuan", "turn", "sessions", "dreams"}
    columns = {node["id"]: node["column"] for node in body["nodes"]}
    assert columns["memory"] == columns["wiki"] == "in"
    assert columns["turn"] == "core"
    assert columns["sessions"] == columns["dreams"] == "out"


def test_only_two_edges_are_wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MEMORY.md and the session history reach a turn. RAG, SiYuan, the wiki and
    the dreams do not, and the payload says so instead of drawing a hope."""
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return (await http.get("/api/memory/graph", headers=AUTH)).json()

    body = run(scenario())
    wired = {(edge["from"], edge["to"]) for edge in body["edges"] if edge["wired"]}
    unwired = {edge["from"] for edge in body["edges"] if not edge["wired"]}

    assert wired == {("memory", "turn"), ("sessions", "turn")}
    assert unwired == {"dreams", "wiki", "rag", "siyuan"}
    assert body["wired"] == 2


def test_the_counts_come_from_the_store_and_the_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    (home / "MEMORY.md").write_text("# Preferences\n\ndark mode\n\n# Projets\n\nUH\n", encoding="utf-8")
    dreams = home / "dreams" / "passes" / "20261113-030000"
    dreams.mkdir(parents=True)
    (dreams / "report.md").write_text(
        "# Dream pass\n\n- status: ok\n\n## Ideas\n1. **Une idee** - deux phrases.\n", encoding="utf-8"
    )

    async def scenario():
        async with client(app) as http:
            await http.post("/api/sessions", json={"title": "une session"}, headers=AUTH)
            return (await http.get("/api/memory/graph", headers=AUTH)).json()

    body = run(scenario())
    nodes = {node["id"]: node for node in body["nodes"]}
    assert nodes["sessions"]["count"] == 1
    assert nodes["sessions"]["detail"].startswith("0 message")
    assert nodes["dreams"]["count"] == 1
    assert nodes["memory"]["count"] >= 1
    assert nodes["memory"]["state"] == "on"
    assert nodes["rag"]["count"] is None or isinstance(nodes["rag"]["count"], int)
    assert nodes["rag"]["state"] == "off"
    assert nodes["siyuan"]["state"] == "off"


def test_an_absent_memory_file_is_not_reported_as_having_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`memory_file()` lists the *default* section names when the file is missing:
    showing "3 sections" next to a file that does not exist would be a lie."""
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return (await http.get("/api/memory/graph", headers=AUTH)).json()

    body = run(scenario())
    memory = {node["id"]: node for node in body["nodes"]}["memory"]

    assert memory["state"] == "off"
    assert memory["count"] is None
    assert "absent" in memory["detail"]
