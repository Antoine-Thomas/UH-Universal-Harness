"""Git étendu (P3-16c lot 2) : journal, index, commit, et la borne du dépôt actif.

Chaque test travaille sur un dépôt jetable créé dans ``tmp_path`` et enregistré : rien
n'approche le dépôt de l'opérateur. Les assertions comparent au CLI Git lui-même - un
diff qui « ressemble » à un diff n'est pas une preuve.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import projects as uh_projects  # noqa: E402
from uh.web.app import _git_dirty, create_app  # noqa: E402

TOKEN = "test-token-git"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def run(coro):
    return asyncio.run(coro)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def git_raw(repo: Path, *args: str) -> str:
    """Comme ``git``, mais sans ``strip()`` : un diff se compare octet pour octet, et
    ``strip()`` mange justement les espaces de fin de la dernière ligne - le sujet du test."""
    proc = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dépôt neuf, un commit initial, et un ``UH_HOME`` isolé pour le registre."""
    home = tmp_path / "uh-home"
    # La fixture globale (`isolated_home`) crée déjà ce dossier et pointe UH_HOME dessus :
    # on le réutilise, on ne le recrée pas.
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UH_HOME", str(home))
    work = tmp_path / "projet"
    work.mkdir()
    git(work, "init", "-q")
    git(work, "config", "user.email", "test@example.invalid")
    git(work, "config", "user.name", "Test Operator")
    git(work, "config", "commit.gpgsign", "false")
    (work / "notes.txt").write_text("premiere ligne\n", encoding="utf-8")
    git(work, "add", "notes.txt")
    git(work, "commit", "-q", "-m", "premier commit")
    uh_projects.add(work, "projet-jetable")
    return work


@pytest.fixture()
def app(repo: Path):
    return create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=repo,
        provider_factory=lambda kind, model: _Stub(),
        kind="stub",
        model="stub:1b",
    )


class _Stub:
    model = "stub:1b"
    streaming = True

    def chat(self, messages, tools, *, on_token=None):
        return {"role": "assistant", "content": "ok", "tool_calls": []}


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


# --------------------------------------------------------------------------- #
# le pied de page : un fichier non suivi n'est pas une modification
# --------------------------------------------------------------------------- #
def test_an_untracked_file_does_not_mark_the_repository_dirty(repo: Path) -> None:
    assert _git_dirty(repo) is False, "un dépôt propre est propre"
    (repo / "petit_prince.svg").write_text("<svg/>", encoding="utf-8")
    assert _git_dirty(repo) is False, "un fichier non suivi n'est pas une modification"


def test_a_tracked_modification_marks_it_dirty(repo: Path) -> None:
    (repo / "notes.txt").write_text("premiere ligne\nseconde ligne\n", encoding="utf-8")
    assert _git_dirty(repo) is True


def test_a_staged_change_also_marks_it_dirty(repo: Path) -> None:
    (repo / "notes.txt").write_text("change\n", encoding="utf-8")
    git(repo, "add", "notes.txt")
    assert _git_dirty(repo) is True


def test_the_panel_separates_tracked_and_untracked(repo: Path) -> None:
    (repo / "notes.txt").write_text("modifie\n", encoding="utf-8")
    (repo / "nouveau.md").write_text("non suivi\n", encoding="utf-8")
    state = uh_projects.describe(repo)
    assert state["tracked"] == 1
    assert state["untracked"] == 1
    assert state["dirty"] == 2, "le total reste le total, pour le panneau"


# --------------------------------------------------------------------------- #
# journal
# --------------------------------------------------------------------------- #
def test_the_log_shows_the_commit_with_its_real_sha(app, repo: Path) -> None:
    sha = git(repo, "rev-parse", "HEAD")

    async def go():
        async with client(app) as http:
            return await http.get("/api/git/log", headers=AUTH)

    body = run(go()).json()
    assert body["ok"] is True
    assert len(body["commits"]) == 1
    entry = body["commits"][0]
    assert entry["sha"] == sha
    assert entry["short"] == sha[:7] == git(repo, "rev-parse", "--short", "HEAD")
    assert entry["subject"] == "premier commit"
    assert entry["author"] == "Test Operator"
    assert entry["date"][:4].isdigit()


def test_the_log_is_paginated(app, repo: Path) -> None:
    for index in range(3):
        (repo / f"f{index}.txt").write_text("x", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", f"commit {index}")

    async def page(offset: int):
        async with client(app) as http:
            return await http.get(
                "/api/git/log", params={"limit": 2, "offset": offset}, headers=AUTH
            )

    first = run(page(0)).json()
    second = run(page(2)).json()
    assert [c["subject"] for c in first["commits"]] == ["commit 2", "commit 1"]
    assert first["has_more"] is True
    assert [c["subject"] for c in second["commits"]] == ["commit 0", "premier commit"]
    assert second["has_more"] is False


def test_an_empty_repository_says_so_without_failing(app, repo: Path, tmp_path: Path) -> None:
    empty = tmp_path / "vide"
    empty.mkdir()
    git(empty, "init", "-q")
    uh_projects.add(empty, "vide")
    empty_app = create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=empty,
        provider_factory=lambda kind, model: _Stub(),
        kind="stub",
        model="stub:1b",
    )

    async def go():
        async with client(empty_app) as http:
            return await http.get("/api/git/log", headers=AUTH)

    body = run(go()).json()
    assert body["ok"] is True and body["commits"] == []


# --------------------------------------------------------------------------- #
# diff
# --------------------------------------------------------------------------- #
def test_the_diff_is_the_one_git_prints(app, repo: Path) -> None:
    (repo / "notes.txt").write_text("premiere ligne\nune autre   \n", encoding="utf-8")
    expected = git_raw(repo, "diff", "--no-color", "--ignore-space-at-eol")

    async def go():
        async with client(app) as http:
            return await http.get("/api/git/diff", params={"file": "notes.txt"}, headers=AUTH)

    body = run(go()).json()
    assert body["diff"] == expected, "le diff servi est celui du CLI, octet pour octet"
    assert "+++ b/notes.txt" in body["diff"]


def test_trailing_spaces_alone_produce_no_diff(app, repo: Path) -> None:
    """`--ignore-space-at-eol` : ajouter des espaces en fin de ligne ne fait pas un diff."""
    (repo / "notes.txt").write_text("premiere ligne   \n", encoding="utf-8")

    async def go():
        async with client(app) as http:
            return await http.get("/api/git/diff", headers=AUTH)

    body = run(go()).json()
    assert body["diff"].strip() == ""


# --------------------------------------------------------------------------- #
# index et commit
# --------------------------------------------------------------------------- #
def test_stage_then_commit_publishes_a_new_sha(app, repo: Path) -> None:
    before = git(repo, "rev-parse", "HEAD")
    (repo / "notes.txt").write_text("premiere ligne\nseconde ligne\n", encoding="utf-8")

    async def go():
        async with client(app) as http:
            staged = await http.post("/api/git/stage", json={"files": ["notes.txt"]}, headers=AUTH)
            committed = await http.post(
                "/api/git/commit", json={"message": "deuxieme ligne"}, headers=AUTH
            )
            log = await http.get("/api/git/log", headers=AUTH)
            return staged, committed, log

    staged, committed, log = run(go())
    assert staged.status_code == 200, staged.text
    assert staged.json()["staged"] == ["notes.txt"]
    assert committed.status_code == 200, committed.text
    body = committed.json()
    after = git(repo, "rev-parse", "HEAD")
    assert body["sha"] == after != before
    assert body["short"] == after[:7]
    assert git(repo, "log", "-1", "--pretty=%s") == "deuxieme ligne"
    assert log.json()["commits"][0]["sha"] == after


def test_unstage_puts_the_file_back_in_the_worktree(app, repo: Path) -> None:
    (repo / "notes.txt").write_text("change\n", encoding="utf-8")

    async def go():
        async with client(app) as http:
            await http.post("/api/git/stage", json={"files": ["notes.txt"]}, headers=AUTH)
            unstaged = await http.post(
                "/api/git/unstage", json={"files": ["notes.txt"]}, headers=AUTH
            )
            return unstaged

    response = run(go())
    assert response.status_code == 200, response.text
    assert response.json()["unstaged"] == ["notes.txt"]
    assert git(repo, "diff", "--cached", "--name-only") == ""


def test_a_commit_without_a_message_is_refused(app, repo: Path) -> None:
    (repo / "notes.txt").write_text("change\n", encoding="utf-8")
    before = git(repo, "rev-parse", "HEAD")

    async def go():
        async with client(app) as http:
            await http.post("/api/git/stage", json={"files": ["notes.txt"]}, headers=AUTH)
            return await http.post("/api/git/commit", json={"message": "   "}, headers=AUTH)

    response = run(go())
    assert response.status_code == 400
    assert "obligatoire" in response.json()["detail"]
    assert git(repo, "rev-parse", "HEAD") == before, "aucun commit n'a été créé"


def test_a_commit_with_nothing_staged_is_refused_by_git(app, repo: Path) -> None:
    async def go():
        async with client(app) as http:
            return await http.post("/api/git/commit", json={"message": "rien"}, headers=AUTH)

    response = run(go())
    assert response.status_code == 400
    assert response.json()["detail"]


def test_session_workspace_only_the_active_repository_accepts_writes(
    app, repo: Path, tmp_path: Path
) -> None:
    other = tmp_path / "autre"
    other.mkdir()
    git(other, "init", "-q")
    uh_projects.add(other, "autre")
    (repo / "notes.txt").write_text("change\n", encoding="utf-8")

    async def go():
        async with client(app) as http:
            return await http.post(
                "/api/git/stage",
                json={"path": str(other), "files": ["notes.txt"]},
                headers=AUTH,
            )

    response = run(go())
    assert response.status_code == 403, response.text
    assert "dépôt actif" in response.json()["detail"]


def test_an_unregistered_repository_is_refused(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enregistré ET sous la racine : être un dépôt ne suffit pas.

    La clôture est posée directement sur `allowed_root` : écrire un config.toml pour la
    faire exister dépendrait du parseur, et un test qui teste le parseur au lieu de la
    borne ne prouve rien sur la borne.
    """
    stranger = tmp_path / "inconnu"
    stranger.mkdir()
    git(stranger, "init", "-q")
    git(stranger, "config", "user.email", "x@example.invalid")
    git(stranger, "config", "user.name", "X")
    (stranger / "a.txt").write_text("a", encoding="utf-8")
    git(stranger, "add", ".")
    git(stranger, "commit", "-q", "-m", "a")
    monkefence = tmp_path / "cloture"
    monkefence.mkdir()
    monkeypatch.setattr(uh_projects, "allowed_root", lambda: monkefence)

    with pytest.raises(uh_projects.ProjectsError) as caught:
        uh_projects.writable(stranger)
    assert "enregistré" in str(caught.value) or "projects.root" in str(caught.value)
    # Le dépôt enregistré, lui, reste autorisé : la clôture n'est pas un refus global.
    assert uh_projects.writable(repo) == repo.resolve()


def test_a_relative_escape_is_refused(app, repo: Path) -> None:
    (repo / "notes.txt").write_text("change\n", encoding="utf-8")

    async def go():
        async with client(app) as http:
            return await http.post("/api/git/stage", json={"files": ["../notes.txt"]}, headers=AUTH)

    response = run(go())
    assert response.status_code == 400, response.text
    assert "hors du dépôt" in response.json()["detail"]


def test_an_absolute_path_is_refused(app, repo: Path) -> None:
    async def go():
        async with client(app) as http:
            return await http.post(
                "/api/git/stage", json={"files": [str(repo / "notes.txt")]}, headers=AUTH
            )

    response = run(go())
    assert response.status_code == 400
    assert "absolu" in response.json()["detail"]


def test_nothing_here_offers_a_push(app) -> None:
    """La règle du projet : pas de push sans GO explicite. Aucune route ne le fait."""
    routes = {route.path for route in app.routes}
    assert not any("push" in path for path in routes), sorted(routes)
