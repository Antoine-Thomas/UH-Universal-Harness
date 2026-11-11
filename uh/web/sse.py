"""SSE framing helpers (text/event-stream)."""

from __future__ import annotations

import json
from typing import Any


def frame(event: str, data: Any) -> str:
    """One SSE frame. ``data`` is JSON-encoded on a single line (no newlines)."""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    payload = payload.replace("\r", " ").replace("\n", " ")
    return f"event: {event}\ndata: {payload}\n\n"


def parse_frames(raw: str) -> list[tuple[str, dict]]:
    """Test helper: turn a raw SSE body into ``[(event, data), ...]``."""
    frames: list[tuple[str, dict]] = []
    event = "message"
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            try:
                frames.append((event, json.loads("\n".join(data_lines))))
            except json.JSONDecodeError:
                frames.append((event, {"raw": "\n".join(data_lines)}))
        event = "message"
    return frames
