"""`uh install-shortcut`: launcher script, generated icon, Windows-only refusal.

The shortcut itself is created by the Windows shell (``WScript.Shell``), so these
tests check everything around it: the script we write, the ICO container we
generate, the PowerShell that would be run, and the two failure modes (not
Windows, no Desktop). Nothing here needs a real Desktop or a real ``.lnk``.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

from uh import cli, shortcut


# --------------------------------------------------------------------------- #
# the launcher script
# --------------------------------------------------------------------------- #
def test_the_launcher_runs_uh_web_and_forwards_arguments() -> None:
    text = shortcut.script_text()

    assert "& $uh.Source web --app @args" in text
    assert "Get-Command uh" in text, "a missing uh must be explained, not flashed away"
    assert "MessageBox" in text, "a hidden window cannot show a message in the console"
    assert "uh-web.log" in text, "the failure has to be readable after the dialog is closed"
    assert "Read-Host" not in text, "no prompt: nobody is watching the window"


def test_the_launcher_is_ascii_so_powershell_51_reads_it() -> None:
    """Windows PowerShell reads a BOM-less .ps1 as ANSI: one accent would break it."""
    text = shortcut.script_text()

    assert text.isascii()
    text.encode("ascii")  # twice, on purpose: the encoding is part of the contract


def test_the_launcher_carries_the_searching_murphy_colours() -> None:
    text = shortcut.script_text()

    assert "38;2;253;197;2" in text, "accent #fdc502 as a 24-bit foreground"
    assert "38;2;40;69;67" in text, "ink #284543 as a 24-bit foreground"
    assert shortcut.INK in text and shortcut.ACCENT in text
    assert "Add-Type" in text, "virtual-terminal processing has to be enabled"
    assert "$ink = ''" in text, "fallback when the console refuses VT: plain text"


# --------------------------------------------------------------------------- #
# the icon
# --------------------------------------------------------------------------- #
def test_one_png_entry_per_size() -> None:
    data = shortcut.icon_bytes([16, 32, 48, 256])

    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, 4)

    entries = [struct.unpack("<BBBBHHII", data[6 + 16 * i : 22 + 16 * i]) for i in range(4)]
    assert [entry[0] for entry in entries] == [16, 32, 48, 0], "0 encodes 256"
    assert len({entry[1] for entry in entries}) == 4
    assert all(entry[4:6] == (1, 32) for entry in entries), "32-bit RGBA, one plane"
    for _width, _height, _colors, _pad, _planes, _bits, size, offset in entries:
        png = data[offset : offset + size]
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert png[-8:-4] == b"IEND"
    assert entries[0][7] == 6 + 16 * 4, "the first image starts after the directory"
    last = entries[-1]
    assert last[7] + last[6] == len(data), "no trailing bytes"


def test_the_shipped_icon_carries_every_size_windows_asks_for() -> None:
    data = shortcut.icon_bytes()

    assert struct.unpack("<H", data[4:6])[0] == len(shortcut.ICON_SIZES)
    assert shortcut.ICON_SIZES == (16, 32, 48, 256)


def test_the_png_declares_rgba_8_bit() -> None:
    png = shortcut.png_bytes(32)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    length, kind = struct.unpack(">I", png[8:12])[0], png[12:16]
    assert (length, kind) == (13, b"IHDR")
    width, height, depth, colour = struct.unpack(">IIBB", png[16:26])
    assert (width, height, depth, colour) == (32, 32, 8, 6)


def test_the_tile_is_rounded_transparent_and_shaded() -> None:
    size = 64
    raw = shortcut.pixels(size)
    stride = size * 4

    def pixel(x: int, y: int) -> tuple[int, int, int, int]:
        base = y * stride + x * 4
        return tuple(raw[base : base + 4])  # type: ignore[return-value]

    assert pixel(0, 0)[3] == 0, "the corner is rounded: fully transparent"
    assert pixel(size // 2, 2)[3] == 255, "the top edge is opaque"
    top = pixel(2, size // 2)
    bottom = pixel(2, size - 2)
    assert sum(bottom[:3]) < sum(top[:3]), "the gradient runs light at the top"


def test_the_monogram_is_gold_and_centred_on_the_tile() -> None:
    """Two letters, centred: the ink margin is balanced left and right."""
    size = 128
    raw = shortcut.pixels(size)
    stride = size * 4

    def gold_at(x: int, y: int) -> bool:
        base = y * stride + x * 4
        red, green, blue = raw[base], raw[base + 1], raw[base + 2]
        return red > 200 and green > 160 and blue < 80

    gold_columns = [x for x in range(size) if any(gold_at(x, y) for y in range(size))]
    assert gold_columns, "the monogram has to be gold, not ink on ink"
    left_margin = gold_columns[0]
    right_margin = size - 1 - gold_columns[-1]
    assert abs(left_margin - right_margin) <= 2, "the letters are centred"
    assert len(gold_columns) > size // 3, "the accent has to be a visible share of the tile"

    # the middle of the tile falls in the gap between "U" and "H"
    assert not gold_at(size // 2, size // 2)


# --------------------------------------------------------------------------- #
# install
# --------------------------------------------------------------------------- #
def test_install_writes_the_launcher_and_the_link(tmp_path: Path) -> None:
    home = tmp_path / "home"
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    commands: list[str] = []

    def fake_runner(script: str) -> str:
        commands.append(script)
        (desktop / shortcut.LINK_NAME).write_text("", encoding="utf-8")  # what WScript does
        return str(desktop / shortcut.LINK_NAME)

    created = shortcut.install(directory=home, desktop=desktop, runner=fake_runner)

    assert created.script == home / shortcut.SCRIPT_NAME
    assert created.icon == home / shortcut.ICON_NAME
    assert created.link == desktop / shortcut.LINK_NAME
    assert created.script.read_text(encoding="ascii").startswith("# UH web UI")
    assert created.icon.read_bytes()[2:4] == b"\x01\x00"
    assert len(created.icon.read_bytes()) > 1000

    script = commands[0]
    assert "WScript.Shell" in script
    assert "powershell.exe" in script
    assert f"-File \"{created.script}\"" in script
    assert str(created.icon) in script
    assert shortcut.LINK_DESCRIPTION in script
    assert "-WindowStyle Hidden" in script, "no console window on top of the browser"
    assert "$lnk.WindowStyle = 7" in script, "minimized in the taskbar, not focused"


def test_install_refuses_a_desktop_that_does_not_exist(tmp_path: Path) -> None:
    home = tmp_path / "home"
    with pytest.raises(shortcut.ShortcutError) as caught:
        shortcut.install(directory=home, desktop=tmp_path / "nope", runner=lambda s: "")

    assert "UH_DESKTOP" in str(caught.value)
    assert (home / shortcut.SCRIPT_NAME).is_file(), "the two harmless files stay"


def test_install_reports_a_shell_failure_without_raising_a_traceback(tmp_path: Path) -> None:
    desktop = tmp_path / "Desktop"
    desktop.mkdir()

    def broken(script: str) -> str:
        raise shortcut.ShortcutError("powershell failed: access denied")

    with pytest.raises(shortcut.ShortcutError) as caught:
        shortcut.install(directory=tmp_path / "home", desktop=desktop, runner=broken)

    assert "access denied" in str(caught.value)


def test_install_detects_a_shortcut_the_shell_did_not_write(tmp_path: Path) -> None:
    """PowerShell exiting 0 is not proof: the file is the proof."""
    desktop = tmp_path / "Desktop"
    desktop.mkdir()

    with pytest.raises(shortcut.ShortcutError):
        shortcut.install(directory=tmp_path / "home", desktop=desktop, runner=lambda s: "C:\\x.lnk")


def test_the_desktop_can_be_overridden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_DESKTOP", str(tmp_path / "ailleurs"))

    assert shortcut.desktop_directory() == tmp_path / "ailleurs"


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
def test_an_old_launcher_is_refreshed_for_app_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrading must not leave the user with a launcher that opens a tab forever."""
    monkeypatch.setattr(shortcut, "windows", lambda: True)
    monkeypatch.setattr(shortcut, "home_directory", lambda: tmp_path)
    launcher = tmp_path / shortcut.SCRIPT_NAME
    launcher.write_text("Write-UhLog 'x'\n& $uh.Source web @args\n", encoding="utf-8")

    refreshed = shortcut.refresh_launcher_if_stale()

    assert refreshed == launcher
    assert "--app" in launcher.read_text(encoding="utf-8")


def test_a_launcher_we_did_not_write_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shortcut, "windows", lambda: True)
    monkeypatch.setattr(shortcut, "home_directory", lambda: tmp_path)
    launcher = tmp_path / shortcut.SCRIPT_NAME
    body = "& $uh.Source web --port 9000\n"
    launcher.write_text(body, encoding="utf-8")

    assert shortcut.refresh_launcher_if_stale() is None
    assert launcher.read_text(encoding="utf-8") == body


def test_the_cli_refuses_platforms_that_are_not_windows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(shortcut, "windows", lambda: False)
    monkeypatch.setattr(sys, "platform", "linux")

    assert cli.main(["install-shortcut"]) == 1

    captured = capsys.readouterr()
    assert "Windows only" in captured.err
    assert "uh web" in captured.err, "the refusal says what to do instead"
    assert "Traceback" not in captured.err


def test_the_cli_prints_the_created_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(shortcut, "windows", lambda: True)
    created = shortcut.Install(
        script=tmp_path / "uh-web.ps1", icon=tmp_path / "uh-web.ico", link=tmp_path / "UH web.lnk"
    )
    monkeypatch.setattr(shortcut, "install", lambda: created)

    assert cli.main(["install-shortcut"]) == 0

    out = capsys.readouterr().out
    assert str(tmp_path / "UH web.lnk") in out
    assert "double-click" in out


def test_the_cli_turns_a_shortcut_error_into_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(shortcut, "windows", lambda: True)

    def boom() -> shortcut.Install:
        raise shortcut.ShortcutError("powershell failed: access denied")

    monkeypatch.setattr(shortcut, "install", boom)

    assert cli.main(["install-shortcut"]) == 1
    assert "uh install-shortcut: powershell failed: access denied" in capsys.readouterr().err
