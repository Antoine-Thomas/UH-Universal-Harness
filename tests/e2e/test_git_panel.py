"""Le panneau Git étendu, prouvé à l'écran (P3-16c lot 2).

Un dépôt jetable sert de workspace au serveur de test : indexer et commiter ici ne touche
jamais le dépôt de l'opérateur. Ce qui est vérifié est l'effet - un nouveau SHA apparaît
dans le journal, et le CLI Git confirme le même commit.

    pytest -o addopts="" -m e2e tests/e2e/test_git_panel.py -q
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture()
def work_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "projet-jetable"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "e2e@example.invalid")
    git(repo, "config", "user.name", "E2E Operator")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "notes.txt").write_text("premiere ligne\n", encoding="utf-8")
    git(repo, "add", "notes.txt")
    git(repo, "commit", "-q", "-m", "premier commit")
    return repo


def open_projects(page) -> None:
    if page.eval_on_selector("#providers-panel", "el => el.classList.contains('collapsed')"):
        page.click("#providers-open")
    page.click("#group-projects")
    page.wait_for_selector("#git-changes .git-change, #git-changes .git-empty", timeout=20000)


def test_the_log_shows_the_commit_and_its_short_sha(browser, make_server, work_repo: Path) -> None:
    server = make_server(workspace=work_repo)
    expected = git(work_repo, "rev-parse", "--short", "HEAD")
    context, page = open_app(browser, server)
    try:
        open_projects(page)
        page.wait_for_selector("#git-log .git-entry", timeout=20000)
        shas = page.eval_on_selector_all("#git-log .git-sha", "els => els.map(e => e.textContent.trim())")
        assert shas and shas[0] == expected, f"journal : {shas} attendu {expected}"
        subjects = page.eval_on_selector_all("#git-log .git-subject", "els => els.map(e => e.textContent)")
        assert "premier commit" in subjects
    finally:
        context.close()


def test_stage_then_commit_from_the_panel(browser, make_server, work_repo: Path) -> None:
    """L'onglet enregistre le dépôt, la page indexe, commite, et le CLI confirme."""
    server = make_server(workspace=work_repo)
    token = server.token()
    registered = httpx.post(
        server.base + "/api/projects/add",
        json={"path": str(work_repo)},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    assert registered.status_code == 200, registered.text
    (work_repo / "notes.txt").write_text("premiere ligne\nseconde ligne\n", encoding="utf-8")
    before = git(work_repo, "rev-parse", "HEAD")

    context, page = open_app(browser, server)
    try:
        open_projects(page)
        page.wait_for_selector("#git-changes .git-change", timeout=20000)
        page.click("#git-changes .git-change button.git-act")
        page.wait_for_function(
            "() => /1 index/.test(document.getElementById('git-status').textContent)",
            timeout=20000,
        )
        # Le diff servi est celui du CLI, espaces de fin ignorés.
        page.click("#git-changes .git-path")
        page.wait_for_function(
            "() => { const el = document.getElementById('git-diff');"
            " return el && !el.hidden && el.textContent.includes('+++ b/notes.txt'); }",
            timeout=20000,
        )
        page.fill("#git-message", "e2e: deuxieme ligne")
        page.click("#git-commit")
        page.wait_for_function(
            "() => /commit [0-9a-f]{7}/.test(document.getElementById('git-commit-hint').textContent)",
            timeout=30000,
        )
        hint = page.eval_on_selector("#git-commit-hint", "el => el.textContent")
        after = git(work_repo, "rev-parse", "HEAD")
        assert after != before, "aucun commit n'a été créé"
        assert after[:7] in hint, f"le cockpit annonce le mauvais SHA : {hint!r}"
        assert git(work_repo, "log", "-1", "--pretty=%s") == "e2e: deuxieme ligne"
        assert git(work_repo, "status", "--porcelain", "--untracked-files=no") == ""
        # Le journal se recharge après le commit : on attend le SHA, on ne lit pas une
        # liste encore vide de son nouveau commit.
        page.wait_for_function(
            "(sha) => { const el = document.querySelector('#git-log .git-sha');"
            " return el && el.textContent.trim() === sha; }",
            arg=after[:7],
            timeout=20000,
        )
        shas = page.eval_on_selector_all("#git-log .git-sha", "els => els.map(e => e.textContent.trim())")
        assert shas[0] == after[:7], f"le journal n'affiche pas le nouveau commit : {shas}"
    finally:
        context.close()


def test_a_commit_without_a_message_keeps_the_button_disabled(browser, make_server, work_repo: Path) -> None:
    server = make_server(workspace=work_repo)
    httpx.post(
        server.base + "/api/projects/add",
        json={"path": str(work_repo)},
        headers={"Authorization": f"Bearer {server.token()}"},
        timeout=20,
    )
    (work_repo / "notes.txt").write_text("change\n", encoding="utf-8")
    before = git(work_repo, "rev-parse", "HEAD")
    context, page = open_app(browser, server)
    try:
        open_projects(page)
        page.wait_for_selector("#git-changes .git-change", timeout=20000)
        page.click("#git-changes .git-change button.git-act")
        page.wait_for_function(
            "() => /1 index/.test(document.getElementById('git-status').textContent)",
            timeout=20000,
        )
        assert page.eval_on_selector("#git-commit", "el => el.disabled") is True
        page.fill("#git-message", "   ")
        assert page.eval_on_selector("#git-commit", "el => el.disabled") is True, (
            "un message fait d'espaces ne débloque pas le commit"
        )
        assert git(work_repo, "rev-parse", "HEAD") == before, "aucun commit n'a pu partir"
    finally:
        context.close()
