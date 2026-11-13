"""The pass sees the tooling, not only the notes: skills and MCP as sources.

Skills and MCP servers live on this machine, so they are read without UHB - and
they are never *called*: the pass asks the model for workflows that name real
tools, and keeps only the names the inventory can prove. A 03:00 job that can
write through somebody else's server with nobody awake is the failure this file
exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import httpx
import pytest

from uh import mcp, skills
from uh.dream_pass import DreamConfig, DreamScheduler

UHB = "http://127.0.0.1:8101"


class RecordingProvider:
    """A model that answers a fixed JSON and keeps the prompts it was given."""

    def __init__(self, answer: dict) -> None:
        self.answer = json.dumps(answer)
        self.calls = 0
        self.prompts: List[str] = []

    def usage_snapshot(self) -> dict:
        return {"prompt": 0, "completion": 0, "total": 0}

    def chat(self, messages, tools=None, on_token=None):
        self.calls += 1
        self.prompts.append("\n".join(str(item.get("content") or "") for item in messages))
        return {"role": "assistant", "content": self.answer}


def router(route: dict | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/siyuan/blocks":
            return httpx.Response(200, json={"count": 1, "blocks": [
                {"id": "b0", "type": "p", "hpath": "/x", "content": "note sur les serveurs MCP"}
            ]})
        if request.url.path == "/rag/status":
            return httpx.Response(200, json={"built": True, "source": "service", "chunks": 12})
        if request.url.path == "/route":
            return httpx.Response(
                200, json=route or {"mode": "chat", "complexity": "standard", "reason": "two sources"}
            )
        return httpx.Response(404, json={"error": {"message": "not found"}})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "uh-home"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UH_HOME", str(target))
    return target


def answer(**extra) -> dict:
    base = {"duplicates": [], "orphans": [], "inconsistencies": [], "ideas": []}
    base.update(extra)
    return base


# --------------------------------------------------------------------------- #
# skills
# --------------------------------------------------------------------------- #
def test_an_installed_skill_reaches_the_prompt(home: Path, tmp_path: Path) -> None:
    skills.create(
        title="Publier sur PyPI",
        description="Construire, vérifier, téléverser",
        instructions="1. python -m build",
        name="pypi-publish",
    )
    provider = RecordingProvider(answer())
    engine = DreamScheduler(
        config=DreamConfig(sources=("skills",), output_dir=tmp_path / "passes"),
        provider=provider,
        uhb_url=UHB,
        client=router(),
    )

    report = engine.run_once()

    assert report.sources["skills"]["available"] is True
    assert "1 skill(s)" in report.sources["skills"]["detail"]
    assert provider.calls == 1
    assert "[skill user] Publier sur PyPI" in provider.prompts[-1]


def test_no_skill_is_an_absent_source_not_a_crash(home: Path, tmp_path: Path) -> None:
    provider = RecordingProvider(answer())
    engine = DreamScheduler(
        config=DreamConfig(sources=("skills",), output_dir=tmp_path / "passes"),
        provider=provider,
        uhb_url=UHB,
        client=router(),
    )

    report = engine.run_once()

    assert report.sources["skills"]["available"] is False
    assert "no skill installed" in report.sources["skills"]["detail"]
    assert report.status == "partial", "a source nobody could read is not an 'ok' night"


# --------------------------------------------------------------------------- #
# MCP
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_mcp(monkeypatch: pytest.MonkeyPatch) -> mcp.ServerConfig:
    server = mcp.ServerConfig(name="filesystem", command="npx", args=("-y", "server-filesystem"))
    monkeypatch.setattr(mcp, "servers_from_config", lambda config=None: ([server], []))
    monkeypatch.setattr(
        mcp,
        "status",
        lambda config=None: {
            "servers": [
                {
                    "name": "filesystem",
                    "connected": True,
                    "tools": ["read_file", "list_directory"],
                }
            ]
        },
    )
    return server


def test_a_connected_server_is_inventoried_with_its_tools(
    home: Path, tmp_path: Path, fake_mcp: mcp.ServerConfig
) -> None:
    provider = RecordingProvider(answer())
    engine = DreamScheduler(
        config=DreamConfig(sources=("mcp",), output_dir=tmp_path / "passes"),
        provider=provider,
        uhb_url=UHB,
        client=router(),
    )

    report = engine.run_once()

    assert report.sources["mcp"]["available"] is True
    assert "1 server(s), 1 connected" in report.sources["mcp"]["detail"]
    prompt = provider.prompts[-1]
    assert "[mcp filesystem]" in prompt
    assert "read_file, list_directory" in prompt


def test_a_workflow_may_only_name_a_tool_that_exists(
    home: Path, tmp_path: Path, fake_mcp: mcp.ServerConfig
) -> None:
    """The model proposed 'search_files' and a missile launcher: neither is published."""
    provider = RecordingProvider(
        answer(
            workflows=[
                {
                    "title": "Lire les notes en vrac",
                    "why": "trois formats coexistent",
                    "tools": ["mcp__filesystem__read_file", "mcp__filesystem__search_files"],
                },
                {
                    "title": "Tout effacer",
                    "why": "inventé",
                    "tools": ["mcp__filesystem__launch_missiles"],
                },
            ]
        )
    )
    engine = DreamScheduler(
        config=DreamConfig(sources=("mcp",), output_dir=tmp_path / "passes"),
        provider=provider,
        uhb_url=UHB,
        client=router(),
    )

    report = engine.run_once()

    assert [item["title"] for item in report.workflows] == ["Lire les notes en vrac"]
    assert report.workflows[0]["tools"] == ["mcp__filesystem__read_file"]
    assert "launch_missiles" not in report.render()


def test_a_workflow_never_runs_through_the_toolset(
    home: Path, tmp_path: Path, fake_mcp: mcp.ServerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inventory, never execution: no MCP tool is called by a pass."""
    called: List[str] = []
    monkeypatch.setattr(mcp, "client_for", lambda config: called.append(config.name))

    provider = RecordingProvider(
        answer(workflows=[{"title": "x", "why": "y", "tools": ["mcp__filesystem__read_file"]}])
    )
    engine = DreamScheduler(
        config=DreamConfig(sources=("mcp",), output_dir=tmp_path / "passes"),
        provider=provider,
        uhb_url=UHB,
        client=router(),
    )

    engine.run_once()

    assert called == [], "a night pass must never start an MCP server to answer"


def test_the_report_lists_the_workflows_under_a_clear_heading(
    home: Path, tmp_path: Path, fake_mcp: mcp.ServerConfig
) -> None:
    provider = RecordingProvider(
        answer(workflows=[{"title": "Audit", "why": "doublons", "tools": ["mcp__filesystem__read_file"]}])
    )
    engine = DreamScheduler(
        config=DreamConfig(sources=("mcp",), output_dir=tmp_path / "passes"),
        provider=provider,
        uhb_url=UHB,
        client=router(),
    )

    text = engine.run_once().render()

    assert "## Workflows proposed (never run)" in text
    assert "Audit" in text
    assert "mcp__filesystem__read_file" in text


# --------------------------------------------------------------------------- #
# UHB down
# --------------------------------------------------------------------------- #
def test_without_uhb_the_local_sources_are_still_inventoried(home: Path, tmp_path: Path) -> None:
    skills.create(title="Trier", description="d", instructions="i", name="trier")
    provider = RecordingProvider(answer())
    engine = DreamScheduler(
        config=DreamConfig(sources=("siyuan", "rag", "skills"), output_dir=tmp_path / "passes"),
        provider=provider,
        uhb_url="",
    )

    report = engine.run_once()

    assert report.status == "partial"
    assert "UHB offline" in report.reason
    assert report.sources["skills"]["available"] is True
    assert report.sources["siyuan"]["available"] is False
    assert provider.calls == 0, "no route, no classification, no spend"
    assert report.artifacts, "a partial pass with content is worth a file"


def test_without_uhb_and_with_nothing_local_the_pass_writes_nothing(
    home: Path, tmp_path: Path
) -> None:
    provider = RecordingProvider(answer())
    engine = DreamScheduler(
        config=DreamConfig(output_dir=tmp_path / "passes"), provider=provider, uhb_url=""
    )

    report = engine.run_once()

    assert report.status == "skipped"
    assert report.artifacts == []
    assert provider.calls == 0
