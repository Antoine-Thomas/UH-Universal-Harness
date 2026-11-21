"""`GET /api/uhb/overview`: UHB's status, modes and tools behind one UH route."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_web import AUTH, client, make_app, run
from uh.toolsets import uhb as uhb_mod

URL = "http://127.0.0.1:8101"


def test_the_overview_gathers_status_modes_and_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uhb_mod, "resolve_url", lambda: URL)
    monkeypatch.setattr(
        uhb_mod.Uhbtoolset, "status", lambda self: {"connected": True, "mode": "chat"}
    )
    monkeypatch.setattr(
        uhb_mod.Uhbtoolset, "modes", lambda self: {"modes": [{"name": "chat"}, {"name": "dev"}]}
    )
    monkeypatch.setattr(
        uhb_mod.Uhbtoolset,
        "describe_tools",
        lambda self, name="", mode="": {
            "tools": [{"name": "execute_code", "available_in": ["dev"]}]
        },
    )
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            data = (await http.get("/api/uhb/overview", headers=AUTH)).json()
            assert data["connected"] is True
            assert data["status"]["mode"] == "chat"
            assert [m["name"] for m in data["modes"]] == ["chat", "dev"]
            assert data["tools"]["tools"][0]["available_in"] == ["dev"]
            assert data["errors"] == {}
            assert data["owned"] is False

    run(scenario())


def test_a_failing_part_is_reported_without_failing_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uhb_mod, "resolve_url", lambda: URL)
    monkeypatch.setattr(
        uhb_mod.Uhbtoolset, "status", lambda self: {"connected": True, "mode": "dev"}
    )
    monkeypatch.setattr(uhb_mod.Uhbtoolset, "modes", lambda self: {"error": "HTTP 404"})
    monkeypatch.setattr(
        uhb_mod.Uhbtoolset, "describe_tools", lambda self, name="", mode="": {"tools": []}
    )
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            data = (await http.get("/api/uhb/overview", headers=AUTH)).json()
            assert data["connected"] is True and data["modes"] is None
            assert data["errors"] == {"modes": "HTTP 404"}

    run(scenario())


def test_the_overview_says_so_when_uhb_is_down_or_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            monkeypatch.setattr(uhb_mod, "resolve_url", lambda: None)
            off = (await http.get("/api/uhb/overview", headers=AUTH)).json()
            assert off["connected"] is False and "disabled" in off["reason"]

            monkeypatch.setattr(uhb_mod, "resolve_url", lambda: URL)
            monkeypatch.setattr(uhb_mod.Uhbtoolset, "status", lambda self: {"error": "unreachable"})
            down = (await http.get("/api/uhb/overview", headers=AUTH)).json()
            assert down["connected"] is False and down["reason"] == "unreachable"

    run(scenario())


def test_the_overview_requires_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            assert (await http.get("/api/uhb/overview")).status_code == 401

    run(scenario())
