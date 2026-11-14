"""Where everything lives, written down so the agent can answer without guessing.

Asked "where are my dreams stored?", a model that was never told invents a path.
This module is the answer: the map is *generated* from the same functions the
rest of the code uses (``uh_home``, ``default_store_path``, ``dream_store``,
``skills``), so it cannot drift from reality the way a hand-written README does.

Two forms:

* :func:`render` - the full markdown, also written to ``$UH_HOME/SYSTEM_MAP.md``;
* :func:`brief` - the handful of lines that ride on the system prompt of every
  turn. Cheap on purpose: names and paths, no directory scan, no count that a
  long turn could make wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from . import config as uh_config
from . import dream_store, skills
from .store import default_store_path

OUTPUTS_KEY = "outputs_dir"


def outputs_dir(cfg: Optional[uh_config.Config] = None) -> Path:
    """Where a *realisation* is delivered - a file the user can open.

    Configurable (``outputs_dir`` in ``config.toml``) because "the Desktop" is a
    Windows habit, not a rule; the default is inside ``$UH_HOME`` so nothing is
    written outside the harness unless the user said where.
    """
    raw = ""
    try:
        settings = cfg if cfg is not None else uh_config.load()
    except Exception:  # noqa: BLE001 - a broken config must not break the map
        settings = None
    if settings is not None:
        # ``Config`` carries a dict (``.get``); anything else may expose an
        # attribute. Support both so a caller with a stub is still testable.
        getter = getattr(settings, "get", None)
        value = getter(OUTPUTS_KEY) if callable(getter) else getattr(settings, OUTPUTS_KEY, "")
        raw = str(value or "").strip()
    if raw:
        return Path(raw.replace("$UH_HOME", str(uh_config.uh_home()))).expanduser()
    return uh_config.uh_home() / "outputs"


def entries(*, workspace: Optional[Path] = None) -> list[dict[str, Any]]:
    """Every place that matters, with the label the answer should use."""
    home = uh_config.uh_home()
    rows: list[dict[str, Any]] = [
        {
            "label": "home",
            "path": str(home),
            "note": "config.toml, keys.toml, logs; everything else is relative to it",
        },
        {
            "label": "chats",
            "path": str(default_store_path()),
            "note": "SQLite: sessions and messages - the conversation you are reading",
        },
        {
            "label": "dreams (passes)",
            "path": str(dream_store.passes_dir()),
            "note": "one folder per night pass (report.md, ideas, status)",
        },
        {
            "label": "dreams (archived)",
            "path": str(dream_store.archived_dir()),
            "note": "passes the user stored, out of the way",
        },
        {
            "label": "dreams (actions)",
            "path": str(dream_store.actions_dir()),
            "note": "'Exaucer' writes a plan here - a plan, never a change",
        },
        {
            "label": "outputs",
            "path": str(outputs_dir()),
            "note": "what a realisation delivers: a file the user opens",
        },
        {
            "label": "skills (yours)",
            "path": str(skills.user_dir()),
            "note": "one .md per skill you added; duplicates can be merged here",
        },
        {
            "label": "skills (built-in)",
            "path": str(skills.native_dir()),
            "note": "shipped with the package, read-only",
        },
        {
            "label": "models",
            "path": "Ollama (http://127.0.0.1:11434)",
            "note": "pulled into Ollama's own store, not into UH_HOME",
        },
    ]
    if workspace is not None:
        rows.insert(
            1,
            {
                "label": "workspace",
                "path": str(workspace),
                "note": "the folder the file tools may read and write",
            },
        )
    return rows


def render(*, workspace: Optional[Path] = None) -> str:
    """The full map, in markdown."""
    lines = [
        "# UH system map",
        "",
        "Generated from the running code: this is where everything actually is.",
        "Nothing here needs to be edited by hand.",
        "",
    ]
    for row in entries(workspace=workspace):
        lines.append(f"- **{row['label']}** - `{row['path']}`")
        lines.append(f"  - {row['note']}")
    lines += [
        "",
        "## The cycle",
        "",
        "1. a chat turn is written to the store (chats),",
        "2. a night pass reads the store, the skills and the MCP servers and writes",
        "   a report into `dreams (passes)`,",
        "3. the user can *store* a pass (it moves to `dreams (archived)`) or *fulfil*",
        "   it, which writes a plan into `dreams (actions)`,",
        "4. a realisation is the only step that writes where the user looks: `outputs`.",
        "",
    ]
    return "\n".join(lines)


def write(*, workspace: Optional[Path] = None) -> Path:
    """Write the map next to the config. Returns the file."""
    path = uh_config.uh_home() / "SYSTEM_MAP.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(workspace=workspace), encoding="utf-8")
    return path


def brief(*, workspace: Optional[Path] = None) -> str:
    """The lines the model gets on every turn. Never guess a path: read it here."""
    lines = ["Where things are on this machine (this list is authoritative):"]
    for row in entries(workspace=workspace):
        lines.append(f"- {row['label']}: {row['path']}")
    lines.append(
        "When the user asks where something is, answer with these paths. "
        "A realisation writes one file into `outputs`; a night pass never writes there."
    )
    return "\n".join(lines)


__all__ = ["OUTPUTS_KEY", "brief", "entries", "outputs_dir", "render", "write"]
