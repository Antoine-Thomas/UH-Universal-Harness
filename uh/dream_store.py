"""Read, archive, delete and "fulfil" the passes a dream left behind.

A pass writes a folder per night: ``report.md`` and ``journal-<date>.jsonl``.
This module is the only thing that touches them, so the UI never has to know the
layout, and every destructive action is in one place.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

from .config import uh_home

STAMP = re.compile(r"^\d{8}-\d{6}$")


def passes_dir() -> Path:
    return uh_home() / "dreams" / "passes"


def archived_dir() -> Path:
    return uh_home() / "dreams" / "archived"


def actions_dir() -> Path:
    return uh_home() / "dreams" / "actions"


def _folder(stamp: str) -> Optional[Path]:
    """Only a stamp that looks like one, and only if it exists.

    The guard is the path traversal guard: ``stamp`` comes from a URL.
    """
    if not STAMP.match(stamp or ""):
        return None
    candidate = passes_dir() / stamp
    return candidate if candidate.is_dir() else None


def _summary(report: str) -> str:
    for line in report.splitlines():
        clean = line.strip()
        if clean and not clean.startswith(("#", "-", "*")):
            return clean[:160]
    return ""


def _ideas(report: str) -> list:
    """The numbered items under a heading that talks about ideas."""
    ideas: list = []
    collecting = False
    for line in report.splitlines():
        if line.startswith("##"):
            collecting = "idea" in line.lower() or "proposal" in line.lower()
            continue
        if collecting:
            match = re.match(r"^\s*\d+\.\s+\*\*(.+?)\*\*\s*-?\s*(.*)$", line)
            if match:
                ideas.append({"title": match.group(1).strip(), "detail": match.group(2).strip()})
    return ideas


def read_dream(stamp: str) -> Optional[dict]:
    folder = _folder(stamp)
    if folder is None:
        return None
    report_path = folder / "report.md"
    try:
        report = report_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        report = ""
    status = "unknown"
    tokens = 0
    for line in report.splitlines():
        if line.startswith("- status:"):
            status = line.split(":", 1)[1].strip().strip("*")
        if line.startswith("- tokens:"):
            match = re.search(r"(\d+)", line)
            tokens = int(match.group(1)) if match else 0
    return {
        "stamp": stamp,
        "status": status,
        "tokens": tokens,
        "summary": _summary(report),
        "ideas": _ideas(report),
        "report": report,
        "path": str(report_path),
    }


def list_dreams(limit: int = 20) -> list:
    folder = passes_dir()
    if not folder.is_dir():
        return []
    stamps = sorted(
        (item.name for item in folder.iterdir() if item.is_dir() and STAMP.match(item.name)),
        reverse=True,
    )
    result = []
    for stamp in stamps[:limit]:
        entry = read_dream(stamp)
        if entry:
            entry.pop("report", None)
            result.append(entry)
    return result


def delete(stamp: str) -> bool:
    folder = _folder(stamp)
    if folder is None:
        return False
    shutil.rmtree(folder)
    return True


def archive(stamp: str) -> Optional[str]:
    folder = _folder(stamp)
    if folder is None:
        return None
    target = archived_dir()
    target.mkdir(parents=True, exist_ok=True)
    destination = target / stamp
    if destination.exists():
        return str(destination)
    shutil.move(str(folder), str(destination))
    return str(destination)


def fulfill(stamp: str) -> Optional[str]:
    """Turn a dream into a plan of action - a file, never a change.

    Nothing here touches the machine: the operator reads the plan, then decides.
    The plan is also copied into ``outputs`` (the folder the user actually looks
    at): a plan the user cannot find is a plan that never happened.
    """
    entry = read_dream(stamp)
    if entry is None:
        return None
    target = actions_dir()
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{stamp}.md"
    lines = [
        f"# Plan of action - dream {stamp}",
        "",
        f"- status of the pass: {entry['status']}",
        f"- tokens counted: {entry['tokens']}",
        f"- source report: {entry['path']}",
        "",
        "Nothing in this file has been applied. Check an item, then do it.",
        "",
        "## Ideas",
        "",
    ]
    if entry["ideas"]:
        for index, idea in enumerate(entry["ideas"], start=1):
            lines.append(f"{index}. [ ] **{idea['title']}** - {idea['detail']}")
    else:
        lines.append("_This pass produced no idea to act on._")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    # The copy in `outputs` is what the user opens; the one in actions/ is the
    # record. Two places on purpose: one is history, the other is a delivery.
    try:
        from .system_map import outputs_dir

        delivered = outputs_dir()
        delivered.mkdir(parents=True, exist_ok=True)
        (delivered / f"dream-{stamp}.md").write_text("\n".join(lines), encoding="utf-8")
    except OSError:  # noqa: BLE001 - a missing delivery must not lose the plan
        pass
    return str(path)


__all__ = [
    "actions_dir",
    "archive",
    "archived_dir",
    "delete",
    "fulfill",
    "list_dreams",
    "passes_dir",
    "read_dream",
]
