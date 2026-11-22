"""One line per turn event: what was running, for how long, and how it ended.

Why this file exists: the question "why does my turn never come back" was
unanswerable. The server knew, but only in a queue that nobody kept. This journal
answers it with timings and outcomes.

What it never writes: the prompt, the answer, an argument, a tool payload, a token.
A file that quotes a conversation is a leak with a rotation policy. Fields are
names, counts, durations and verdicts - nothing a user typed.
"""

from __future__ import annotations

import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

from . import config

#: One file, rotated: a journal that grows without bound is a second problem.
MAX_BYTES = 1_000_000
BACKUPS = 3
#: A single field never needs more than this to be useful.
FIELD_LIMIT = 120

_LOGGER: Optional[logging.Logger] = None
_LOCK = threading.Lock()
_HANDLER: Optional[RotatingFileHandler] = None


def log_path() -> Path:
    """``$UH_HOME/uh-web.log`` - the file the launcher already writes next to."""
    return config.uh_home() / "uh-web.log"


def _clean(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:FIELD_LIMIT]


def _logger() -> logging.Logger:
    global _LOGGER, _HANDLER
    with _LOCK:
        if _LOGGER is not None:
            return _LOGGER
        logger = logging.getLogger("uh.turn")
        logger.setLevel(logging.INFO)
        logger.propagate = False  # the console already has uvicorn's log
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
        _HANDLER = handler
        _LOGGER = logger
        return logger


def event(kind: str, **fields: Any) -> None:
    """Write one line. Failures are swallowed: a turn never fails on its journal.

    ``fields`` are ``k=v`` pairs; ``None`` values are dropped so a line stays short
    and greppable (``turn tool_end turn=ab12 tool=execute_code ms=4210 status=ok``).
    """
    try:
        payload = " ".join(
            f"{key}={_clean(value)}" for key, value in fields.items() if value is not None
        )
        _logger().info("turn %s%s", kind, f" {payload}" if payload else "")
    except Exception:  # noqa: BLE001 - observability must never break a turn
        pass


def reset_for_tests() -> None:
    """Drop the cached logger and its file handle (a test changes UH_HOME)."""
    global _LOGGER, _HANDLER
    with _LOCK:
        if _HANDLER is not None:
            try:
                _HANDLER.close()
            except Exception:  # noqa: BLE001
                pass
        if _LOGGER is not None:
            for existing in list(_LOGGER.handlers):
                _LOGGER.removeHandler(existing)
        _LOGGER = None
        _HANDLER = None


__all__ = ["BACKUPS", "FIELD_LIMIT", "MAX_BYTES", "event", "log_path", "reset_for_tests"]
