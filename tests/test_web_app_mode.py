"""``uh web --app``: a chromeless window, and an honest fallback when there is none.

No browser is started here. What a test can prove without one is the contract:
the argv carries ``--app=``, the profile lives under ``$UH_HOME`` (a shared
profile would drop the flag on a running browser and open a tab instead), a
machine without Chromium gets ``False`` and the caller opens a tab, and the
launcher installed on the Desktop asks for the window.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh.web import browser

URL = "http://127.0.0.1:8765/?token=abc"


def test_the_window_is_an_app_window_with_its_own_profile(tmp_path: Path) -> None:
    binary = tmp_path / "msedge.exe"
    argv = browser.app_command(binary, URL, tmp_path / "web-app")

    assert argv[0] == str(binary), "the discovered binary, not a re-guessed name"
    assert argv[1] == f"--app={URL}", "no address bar, no tab strip"
    assert any(item.startswith("--window-size=") for item in argv)
    assert any(item.startswith("--user-data-dir=") for item in argv), (
        "a shared profile forwards the URL to a running browser and loses --app"
    )
    assert "--no-first-run" in argv


def test_an_explicit_browser_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "chrome.exe"
    binary.write_bytes(b"")
    monkeypatch.setenv(browser.ENV_BROWSER, str(binary))

    assert browser.find_chromium() == binary


def test_an_explicit_browser_that_does_not_exist_is_not_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UH_BROWSER is a statement, not a hint: a typo must not silently open Edge."""
    monkeypatch.setenv(browser.ENV_BROWSER, str(tmp_path / "nope.exe"))

    assert browser.find_chromium() is None


def test_a_found_browser_is_started_with_the_flag(tmp_path: Path) -> None:
    binary = tmp_path / "msedge.exe"
    binary.write_bytes(b"")
    launched: list[list[str]] = []

    opened = browser.open_app_window(
        URL,
        profile_dir=tmp_path / "profile",
        finder=lambda: binary,
        spawn=lambda argv: launched.append(list(argv)),
    )

    assert opened is True
    assert len(launched) == 1
    assert launched[0][1] == f"--app={URL}"


def test_the_window_opens_in_the_middle_of_the_screen(tmp_path: Path) -> None:
    """A window in a corner reads as a popup; the user asked for an application."""
    import re
    import sys

    binary = tmp_path / "msedge.exe"
    argv = browser.app_command(binary, URL, tmp_path / "profile")
    position = [item for item in argv if item.startswith("--window-position=")]

    if sys.platform.startswith("win"):
        assert position, "Windows knows the screen size: centre on it"
        assert re.fullmatch(r"--window-position=\d+,\d+", position[0])
    else:
        assert position == [], "no guessing on a platform that will not say"


def test_no_chromium_is_a_fallback_not_a_crash(tmp_path: Path) -> None:
    launched: list[list[str]] = []

    opened = browser.open_app_window(
        URL,
        finder=lambda: None,
        spawn=lambda argv: launched.append(list(argv)),
    )

    assert opened is False
    assert launched == [], "nothing to launch: the caller opens a normal tab"
    assert "Edge or Chrome" in browser.fallback_reason()


def test_a_browser_that_refuses_to_start_is_a_fallback(tmp_path: Path) -> None:
    binary = tmp_path / "msedge.exe"
    binary.write_bytes(b"")

    def explode(argv: list[str]) -> None:
        raise OSError("cannot execute")

    assert browser.open_app_window(
        URL, finder=lambda: binary, spawn=explode, profile_dir=tmp_path / "p"
    ) is False


def test_the_cli_passes_app_mode_to_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    from uh import cli
    from uh.web import app as web_app

    seen: dict = {}

    def fake_run_server(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(web_app, "run_server", fake_run_server)

    assert cli.main(["web", "--app", "--no-uhb", "--no-open"]) == 0
    assert seen["app_window"] is True
    assert seen["open_browser"] is False


def test_without_the_flag_the_server_keeps_the_plain_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    from uh import cli
    from uh.web import app as web_app

    seen: dict = {}
    monkeypatch.setattr(web_app, "run_server", lambda **kwargs: seen.update(kwargs) or 0)

    assert cli.main(["web", "--no-uhb", "--no-open"]) == 0
    assert seen["app_window"] is False
