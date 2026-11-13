"""The cockpit in its own window (``uh web --app``), without a new toolchain.

A browser window with no tab bar and no address bar is a desktop application
for every purpose a user has: one icon, one window, Alt-Tab shows "UH". Chrome
and Edge have shipped that for years as ``--app=<url>``, so the choice is
between a flag and a Rust toolchain (Tauri: ~1.5 GB, WebView2, code signing
before Windows stops calling the installer a virus). The flag wins until there
is a reason for the toolchain.

What is deliberately *not* here:

* a bundled Chromium - it would double the wheel for a window frame;
* a silent fallback - if no Chromium is found, the caller is told and a normal
  tab opens instead. A user who sees an address bar must know why;
* a profile shared with the user's browsing. ``--user-data-dir`` points at
  ``$UH_HOME/web-app``: the app window keeps its own history and extensions out
  of the browser the user actually browses with, and it cannot be blocked by an
  already-running Edge whose profile is locked.

Discovery is explicit rather than a ``PATH`` guess: on Windows, Edge is found
through its four documented install roots, Chrome through its three; on Linux
and macOS the usual names are resolved with :func:`shutil.which`. The
environment override ``UH_BROWSER`` wins over everything, which is also how the
tests stay off the real machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

#: The window the app mode opens. Wide enough for the three panels and the
#: detail drawer of the cockpit, short enough to fit a 1080p screen with a
#: taskbar.
WINDOW_SIZE = "1280,880"

#: Inside ``$UH_HOME``: the app window's own browser profile.
PROFILE_DIR = "web-app"

#: ``UH_BROWSER`` names a Chromium binary explicitly (tests, unusual installs).
ENV_BROWSER = "UH_BROWSER"

#: ``(binary, roots)`` - Edge first: it ships with Windows and cannot be
#: uninstalled, so it is the likeliest answer on the platform UH is built for.
WINDOWS_CANDIDATES = (
    (
        "msedge.exe",
        (
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application",
            r"%ProgramFiles%\Microsoft\Edge\Application",
            r"%LOCALAPPDATA%\Microsoft\Edge\Application",
        ),
    ),
    (
        "chrome.exe",
        (
            r"%ProgramFiles%\Google\Chrome\Application",
            r"%ProgramFiles(x86)%\Google\Chrome\Application",
            r"%LOCALAPPDATA%\Google\Chrome\Application",
        ),
    ),
)

#: Names resolved with ``which`` everywhere else, in preference order.
UNIX_NAMES = (
    "microsoft-edge",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
)

MAC_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)

#: Runner seam: tests pass a fake instead of starting a browser.
Spawn = Callable[[Sequence[str]], None]


def windows() -> bool:
    """True on Windows. A function, not a constant: tests replace it."""
    return sys.platform.startswith("win")


def _expand(root: str) -> Path:
    """Expand ``%VAR%`` on Windows and ``$VAR``/``~`` everywhere."""
    text = os.path.expandvars(os.path.expanduser(root))
    return Path(text)


def _candidates() -> Iterable[Path]:
    override = os.environ.get(ENV_BROWSER)
    if override:
        yield Path(override).expanduser()
        return
    if windows():
        for binary, roots in WINDOWS_CANDIDATES:
            for root in roots:
                yield _expand(root) / binary
        return
    if sys.platform == "darwin":
        for path in MAC_PATHS:
            yield Path(path)
        return
    for name in UNIX_NAMES:
        found = shutil.which(name)
        if found:
            yield Path(found)


def find_chromium() -> Optional[Path]:
    """The first Chromium-family binary that exists, or ``None``.

    Edge and Chrome are both Chromium: ``--app`` is the same flag, and the UI is
    the same engine. Firefox has no equivalent, so it is not a candidate - a
    fallback tab is better than a half-working window.
    """
    for candidate in _candidates():
        try:
            if candidate.is_file():
                return candidate
        except OSError:  # pragma: no cover - an unreadable root is not a browser
            continue
    return None


def centered_position(width: int = 1280, height: int = 880) -> Optional[str]:
    """``--window-position=x,y`` centred on the primary screen, Windows only.

    An app window that lands in a corner reads as a popup; the user asked for
    something that opens like a program. Computed at launch so a 4K screen and a
    laptop both get the middle, and omitted when the platform will not say.
    """
    if not windows():
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        screen_w = int(user32.GetSystemMetrics(0))
        screen_h = int(user32.GetSystemMetrics(1))
        if screen_w <= 0 or screen_h <= 0:
            return None
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        return f"--window-position={x},{y}"
    except Exception:  # noqa: BLE001 - a hint, not a requirement
        return None


def app_command(binary: Path, url: str, profile_dir: Optional[Path] = None) -> List[str]:
    """The argv that opens ``url`` as a window. Pure, so it can be asserted on.

    ``--user-data-dir`` is not cosmetic: without it, a running Edge forwards the
    URL to the existing process, and depending on the version the ``--app`` flag
    is dropped on the way - the user gets a tab in the browser they were already
    using, which is exactly what this feature exists to avoid.
    """
    position = centered_position()
    argv = [
        str(binary),
        f"--app={url}",
        f"--window-size={WINDOW_SIZE}",
        position or "",
        f"--user-data-dir={profile_dir}" if profile_dir else "",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    return [item for item in argv if item]


def _spawn(argv: Sequence[str]) -> None:
    """Start the window and forget it: UH does not own the browser process."""
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True}
    if windows():
        # A detached console-less child: no black window flashes over the UI.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(list(argv), **kwargs)  # noqa: S603 - a known browser, a local URL


def open_app_window(
    url: str,
    *,
    profile_dir: Optional[Path] = None,
    finder: Callable[[], Optional[Path]] = find_chromium,
    spawn: Spawn = _spawn,
) -> bool:
    """Open ``url`` as a chromeless window. Returns False when there is no browser.

    Never raises: a machine without Edge or Chrome is a normal machine, and the
    caller's fallback (a regular tab) is a working outcome, not an error.
    """
    binary = finder()
    if binary is None:
        return False
    if profile_dir is None:
        from ..config import uh_home

        profile_dir = uh_home() / PROFILE_DIR
    try:
        spawn(app_command(binary, url, profile_dir))
    except (OSError, ValueError):
        return False
    return True


def fallback_reason() -> str:
    """One sentence for the console when no Chromium was found."""
    return (
        "no Edge or Chrome found: opening a normal browser tab "
        f"(set {ENV_BROWSER} to a Chromium binary to get the window)"
    )
