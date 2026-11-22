"""P3-16b, part 2: the routes, their guards, and the proxy that never decides.

Three properties are tested here, and they are the ones a cockpit lives or dies on:

* the token is required (a page is not an authorisation);
* a project switch is refused unless the folder really is a known repository - and when
  it is accepted, it lands in the **session's** metadata, not in a global (D-5);
* ``/api/runs`` is a proxy: when UHB is down the answer is ``ok: False`` with a reason,
  never a 500 and never an empty success.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-projects"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class StubProvider:
    model = "stub:1b"

    def chat(self, messages, tools=None, stream=False, **kwargs):  # pragma: no cover
        raise RuntimeError("no turn runs here")


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "T"),
    ):
        subprocess.run(("git", "-C", str(path), *args), capture_output=True, check=False)
    (path / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(path), "add", "-A"), capture_output=True, check=False)
    subprocess.run(
        ("git", "-C", str(path), "commit", "-m", "first"), capture_output=True, check=False
    )
    return path


@pytest.fixture()
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    workspace = make_repo(tmp_path / "atelier" / "projet")
    monkeypatch.setattr("uh.projects.allowed_root", lambda: tmp_path / "atelier")
    return create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=workspace,
        provider_factory=lambda kind, model: StubProvider(),
        kind="stub",
        model="stub:1b",
    )


def call(app, method: str, path: str, payload: dict | None = None, headers: dict | None = None):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            # `headers or AUTH` substituted AUTH for the empty dict: a test that
            # believes it sent no token would have sent one (same trap as gate B).
            return await http.request(
                method, path, json=payload, headers=AUTH if headers is None else headers
            )

    return asyncio.run(run())


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #
def test_the_projects_routes_require_the_token(app) -> None:
    assert call(app, "GET", "/api/projects", headers={}).status_code == 401
    assert call(app, "POST", "/api/projects/switch", {"path": "x"}, headers={}).status_code == 401
    assert call(app, "POST", "/api/runs", {"action": "prune"}, headers={}).status_code == 401
    assert call(app, "GET", "/api/runs", headers={}).status_code == 401


def test_a_cross_origin_switch_is_refused(app, tmp_path: Path) -> None:
    response = call(
        app,
        "POST",
        "/api/projects/switch",
        {"path": str(tmp_path)},
        headers={**AUTH, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# projects
# --------------------------------------------------------------------------- #
def test_the_current_project_is_listed_with_its_real_status(app) -> None:
    payload = call(app, "GET", "/api/projects").json()

    assert payload["ok"] is True
    first = payload["projects"][0]
    assert first["current"] is True and first["branch"] == "main"
    terminal = subprocess.run(
        ("git", "-C", first["path"], "status", "--porcelain=v1", "-b"),
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert first["porcelain"] == terminal, "D-4 : le panneau doit montrer la sortie du terminal"


def test_adding_a_non_repository_is_a_400(app, tmp_path: Path) -> None:
    plain = tmp_path / "pas-un-depot"
    plain.mkdir()
    response = call(app, "POST", "/api/projects/add", {"path": str(plain)})
    assert response.status_code == 400


def test_switching_stores_the_workspace_on_the_session(app, tmp_path: Path) -> None:
    """D-5, server side: the switch is per session, so no other turn is moved."""
    other = make_repo(tmp_path / "atelier" / "autre")
    session_id = call(app, "POST", "/api/sessions", {"title": "bascule"}).json()["session"]["id"]

    response = call(
        app, "POST", "/api/projects/switch", {"path": str(other), "session_id": session_id}
    )

    assert response.status_code == 200
    assert response.json()["workspace"] == str(other)
    listing = call(app, "GET", f"/api/projects?session_id={session_id}").json()
    assert Path(listing["current"]) == other


def test_a_switch_outside_the_root_is_refused(app, tmp_path: Path) -> None:
    outside = make_repo(tmp_path / "ailleurs")
    response = call(app, "POST", "/api/projects/switch", {"path": str(outside)})
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# runs proxy
# --------------------------------------------------------------------------- #
def test_the_runs_route_is_a_proxy_and_says_so_when_uhb_is_down(app, monkeypatch) -> None:
    from uh import uhb_runs

    monkeypatch.setattr(
        uhb_runs,
        "status",
        lambda: {"ok": False, "reason": "UHB injoignable (ConnectError)", "runs": []},
    )
    payload = call(app, "GET", "/api/runs").json()

    assert payload["ok"] is False and "injoignable" in payload["reason"]
    assert payload["runs"] == [], "un panneau vide vaut mieux qu'une fausse liste"


def test_prune_via_the_route_stays_a_dry_run_unless_apply(app, monkeypatch) -> None:
    from uh import uhb_runs

    seen: list[bool] = []
    monkeypatch.setattr(
        uhb_runs,
        "prune",
        lambda apply=False: seen.append(apply) or {"ok": True, "dry_run": not apply},
    )

    call(app, "POST", "/api/runs", {"action": "prune"})
    call(app, "POST", "/api/runs", {"action": "prune", "apply": True})

    assert seen == [False, True]


def test_close_requires_a_run_name(app, monkeypatch) -> None:
    from uh import uhb_runs

    called: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        uhb_runs, "close", lambda run, keep, summary="": called.append((run, keep)) or {"ok": True}
    )

    assert call(app, "POST", "/api/runs", {"action": "close"}).status_code == 400
    assert (
        call(
            app, "POST", "/api/runs", {"action": "close", "run": "run-1", "keep": True}
        ).status_code
        == 200
    )
    assert called == [("run-1", True)]


def test_an_unknown_action_is_a_400(app) -> None:
    response = call(app, "POST", "/api/runs", {"action": "explode"})
    assert response.status_code == 400
    assert "close" in json.dumps(response.json())
