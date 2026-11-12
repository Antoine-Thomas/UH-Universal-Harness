"""Workspace default: never adopt a system directory, fall back to ~/uh-workspace.

A console opened from ``C:\\Windows\\System32`` used to take the OS as its
workspace, so the file sandbox refused everything the user asked for and the
model went looking for another way. The rule now starts with the directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from uh import cli, sandbox
from uh import config as uh_config
from uh.toolsets.file import FileToolset
from uh.toolsets.terminal import TerminalToolset

# The cwd that triggers the guard on the platform running the tests.
SYSTEM_CWD = Path("C:/Windows/System32") if os.name == "nt" else Path("/usr/bin")


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Windows",
        r"C:\Windows\System32",
        r"C:\Program Files\Git",
        r"C:\Program Files (x86)\Steam",
        r"C:\ProgramData\Microsoft",
        "/usr",
        "/usr/bin",
        "/etc",
        "/etc/apt",
        "/bin",
        "/sbin",
        "/System/Library",
        "/Library/Apple",
        Path("C:/ProgramData"),
    ],
)
def test_system_directories_are_recognised(path) -> None:
    assert sandbox.is_system_directory(path) is True


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Users\searc\Desktop",
        r"C:\Users\searc\UH-Universal-Harness",
        r"C:\WindowsApps",  # not a prefix of C:\Windows: the separator matters
        "/home/searc/projets",
        "/tmp/scratch",
        "/usr-local",
        "",
        None,
    ],
)
def test_ordinary_directories_are_not_system(path) -> None:
    assert sandbox.is_system_directory(path) is False


# --------------------------------------------------------------------------- #
# default_workspace
# --------------------------------------------------------------------------- #
def test_uh_workspace_is_used_as_given(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("UH_WORKSPACE", str(explicit))
    monkeypatch.setattr(sandbox.Path, "cwd", staticmethod(lambda: SYSTEM_CWD))

    assert sandbox.default_workspace() == explicit.resolve()


def test_a_system_cwd_falls_back_and_the_fallback_is_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("UH_WORKSPACE", raising=False)
    monkeypatch.setattr(sandbox.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(sandbox.Path, "cwd", staticmethod(lambda: SYSTEM_CWD))

    workspace = sandbox.default_workspace()

    assert workspace == (tmp_path / "uh-workspace").resolve()
    assert workspace.is_dir(), "the fallback is created on first use"


def test_an_ordinary_cwd_is_used_as_is(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = tmp_path / "projet"
    project.mkdir()
    monkeypatch.delenv("UH_WORKSPACE", raising=False)
    monkeypatch.setattr(sandbox.Path, "cwd", staticmethod(lambda: project))

    assert sandbox.default_workspace() == project.resolve()


def test_the_fallback_is_shared_by_both_repository_toolsets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("UH_WORKSPACE", raising=False)
    monkeypatch.setattr(sandbox.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(sandbox.Path, "cwd", staticmethod(lambda: SYSTEM_CWD))

    expected = (tmp_path / "uh-workspace").resolve()
    assert FileToolset().workspace == expected
    assert TerminalToolset().workspace == expected


# --------------------------------------------------------------------------- #
# the CLI path: same rule, plus one warning
# --------------------------------------------------------------------------- #
class StubProvider:
    model = "stub:1b"

    def chat(self, messages, tools, **kwargs):
        return {"role": "assistant", "content": "ok", "tool_calls": []}


def test_the_cli_reports_where_the_workspace_went(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("UH_HOME", str(home))
    monkeypatch.delenv("UH_WORKSPACE", raising=False)
    monkeypatch.setattr(sandbox.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(sandbox.Path, "cwd", staticmethod(lambda: SYSTEM_CWD))
    monkeypatch.setattr(
        cli, "_build_chain", lambda *args, **kwargs: ([("stub", StubProvider())], "stub:1b")
    )

    session, code = cli._build_chat_session(cli._chat_namespace(no_save=True), uh_config.load())
    assert code == 0 and session is not None
    try:
        assert session.workspace == (home / "uh-workspace").resolve()
    finally:
        session.close()

    assert "[WARN] cwd is a system directory; using ~/uh-workspace instead" in capsys.readouterr().err


def test_workspace_path_reports_its_origin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("UH_WORKSPACE", raising=False)
    monkeypatch.setattr(sandbox.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(sandbox.Path, "cwd", staticmethod(lambda: SYSTEM_CWD))
    assert cli._workspace_path() == ((tmp_path / "uh-workspace").resolve(), "system-fallback")

    monkeypatch.setenv("UH_WORKSPACE", str(tmp_path / "explicit"))
    assert cli._workspace_path()[1] == "UH_WORKSPACE"
    assert cli._workspace_path(str(tmp_path))[1] == "argument"
