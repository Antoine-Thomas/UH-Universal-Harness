"""Startup banner for the UH CLI (cosmetic, no dependency, no colour).

Rules encoded here:

* the banner is an interactive affordance - it only appears on a TTY, so
  ``uh | cat``, ``uh chat --once`` and every redirected run stay plain text;
* ``UH_BANNER=0`` disables it even on a TTY; ``UH_ASCII=1`` forces the ASCII
  art; a terminal narrower than 40 columns gets the two-line short banner;
* Unicode art is chosen when the console encoding can actually carry the
  box-drawing characters, otherwise the pure-ASCII fallback (Windows conhost /
  legacy code pages). ``cli.main`` forces stdout to utf-8 first, so a modern
  PowerShell or Windows Terminal reaches this module as utf-8 and gets the logo;
* no ANSI sequence is emitted, so ``NO_COLOR`` has no effect here - it is
  documented rather than silently ignored, and colours can be added later
  without changing this contract.

The separators in the ASCII art and in the short banner are ASCII on purpose
(``|``, not ``·``): a fallback that needs UTF-8 is not a fallback.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Optional

from . import __version__

LOGO_UNICODE = f"""\
██╗   ██╗██╗  ██╗
██║   ██║██║  ██║
██║   ██║███████║
██║   ██║██╔══██║
╚██████╔╝██║  ██║
 ╚═════╝ ╚═╝  ╚═╝
  Universal Harness · v{__version__}
"""

_LOGO_ASCII_LINES = (
    " _   _ _   _",
    "| | | | | | |",
    "| |_| | |_| |",
    "|  _  |  _  |",
    "|_| |_|_| |_|",
    f"  Universal Harness | v{__version__}",
)
LOGO_ASCII = "\n".join(_LOGO_ASCII_LINES) + "\n"

_BANNER_SHORT_LINES = (
    f"UH | Universal Harness v{__version__}",
    "provider-agnostic | local-first | ollama + omnirouter",
)
BANNER_SHORT = "\n".join(_BANNER_SHORT_LINES) + "\n"

MIN_WIDTH_FOR_LOGO = 40


def _stdout_is_tty() -> bool:
    """Indirection so tests can flip the TTY state without a real terminal."""
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):  # pragma: no cover - exotic streams
        return False


def should_show() -> bool:
    """True when a banner may be printed: TTY, not disabled by environment."""
    if os.environ.get("UH_BANNER", "").strip() in {"0", "false", "no"}:
        return False
    return _stdout_is_tty()


def _stdout_can_encode(text: str) -> bool:
    """True when the console encoding carries ``text`` without losing glyphs.

    The encoding is the operative signal, not a platform guess: ``cli.main``
    forces stdout to utf-8 before the banner renders, so a code page that still
    cannot encode the box-drawing art is a genuinely legacy console (cp1252,
    cp850). Windows Terminal and PowerShell 5+ report utf-8 once that happened
    and therefore get the logo.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _force_ascii(force_ascii: Optional[bool]) -> bool:
    if force_ascii is not None:
        return force_ascii
    if os.environ.get("UH_ASCII", "").strip() in {"1", "true", "yes"}:
        return True
    return not _stdout_can_encode(LOGO_UNICODE)


def render(width: Optional[int] = None, force_ascii: Optional[bool] = None) -> str:
    """Return the banner to print, without trailing newline handling.

    ``width`` defaults to the real terminal width; under 40 columns the short
    banner is returned whatever the art would be.
    """
    if width is None:
        try:
            width = shutil.get_terminal_size().columns
        except OSError:  # pragma: no cover - no controlling terminal
            width = 80
    if width < MIN_WIDTH_FOR_LOGO:
        return BANNER_SHORT
    return LOGO_ASCII if _force_ascii(force_ascii) else LOGO_UNICODE
