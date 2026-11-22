"""`uh web` started from a protected directory lands in ``~/uh-workspace``.

A Windows shortcut opens in ``C:\\Windows\\System32``, a terminal opened to look at
the configuration sits in ``~/.uh``: in both cases ``write_file`` used to fail
because the web server adopted the cwd as its workspace. Everything here runs in
a throwaway home: the real ``~/.uh`` and ``~/uh-workspace`` are never touched.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from uh import cli, sandbox
from uh.web.app import create_app

SYSTEM_CWD = Path("C:/Windows/System32") if os.name == "nt" else Path("/usr/bin")


@pytest.fixture()
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("UH_HOME", str(home / ".uh"))
    monkeypatch.delenv("UH_WORKSPACE", raising=False)
    monkeypatch.setattr(sandbox.Path, "home", staticmethod(lambda: home))
    return home


def test_uh_home_and_what_is_under_it_are_recognised(isolated: Path) -> None:
    uh_home = isolated / ".uh"
    uh_home.mkdir()
    (uh_home / "dreams").mkdir()
    assert sandbox.is_uh_home(uh_home)
    assert sandbox.is_uh_home(uh_home / "dreams")
    assert not sandbox.is_uh_home(isolated / ".uh-other")
    assert not sandbox.is_uh_home(isolated / "projects")
    assert not sandbox.is_uh_home(None)


def test_web_started_in_uh_home_works_in_the_fallback_workspace(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    uh_home = isolated / ".uh"
    uh_home.mkdir()
    monkeypatch.setattr(sandbox.Path, "cwd", staticmethod(lambda: uh_home))

    app = create_app(token="t")

    expected = (isolated / "uh-workspace").resolve()
    assert app.state.settings.workspace == expected
    assert expected.is_dir(), "created when it did not exist"
    assert not list(uh_home.iterdir()), "nothing was written in UH_HOME"


def test_web_started_in_a_system_directory_works_in_the_fallback_workspace(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    monkeypatch.setattr(sandbox.Path, "cwd", staticmethod(lambda: SYSTEM_CWD))
    app = create_app(token="t")
    assert app.state.settings.workspace == (isolated / "uh-workspace").resolve()


def test_a_normal_project_directory_is_kept(
    monkeypatch: pytest.MonkeyPatch, isolated: Path, tmp_path: Path
) -> None:
    project = tmp_path / "projet"
    project.mkdir()
    monkeypatch.setattr(sandbox.Path, "cwd", staticmethod(lambda: project))
    assert create_app(token="t").state.settings.workspace == project.resolve()
    assert not (isolated / "uh-workspace").exists()


def test_an_explicit_workspace_is_trusted_even_in_uh_home(isolated: Path, tmp_path: Path) -> None:
    chosen = tmp_path / "choisi"
    chosen.mkdir()
    assert create_app(token="t", workspace=chosen).state.settings.workspace == chosen.resolve()


def test_the_cli_resolver_applies_the_same_rule_to_uh_home(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    uh_home = isolated / ".uh"
    uh_home.mkdir()
    monkeypatch.setattr(sandbox.Path, "cwd", staticmethod(lambda: uh_home))
    path, origin = cli._workspace_path()
    assert (path, origin) == ((isolated / "uh-workspace").resolve(), "system-fallback")
