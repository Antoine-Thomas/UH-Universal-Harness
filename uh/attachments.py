"""Attachments: the file a user drops on the chat, stored and described.

The rule is one threshold and one question: is the text cheaper to **send** than
to **describe**? Under :data:`INLINE_LIMIT` bytes it is inlined into the message
- the model reads it without a tool call, and the payload stays small. Above it,
only a reference goes out: name, size, absolute path, and a note saying what the
model can do with it. That is context pruning applied at the door, before the
turn is built.

What each kind does today:

* ``.txt``/``.md``/``.csv``/``.json``/code - decoded, inlined under the limit;
* ``.pdf`` - text through ``pypdf`` **if it is installed** (optional import, never
  a hard dependency); the same threshold applies to the extracted text;
* images and videos - referenced only. UH sends text to its providers: a picture
  path in a text message does not make it visible, and pretending otherwise
  would be the kind of lie a user discovers at the worst moment;
* anything else - referenced, with the size.

Files land in ``$UH_HOME/uploads/<session_id>/``. Nothing outside that folder is
ever accepted back: a path from the browser is resolved and rejected unless it
is inside, so an attachment list cannot be turned into an arbitrary file read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

#: Text under this size is inlined; above it, the model gets a reference.
INLINE_LIMIT = 4096
#: Hard cap on one uploaded file. Larger than any conversation needs, small
#: enough that a mistyped drag of a disk image fails fast.
MAX_BYTES = 20 * 1024 * 1024
#: The header line that separates the user's words from the attachments.
HEADER = "[attached files]"

TEXT_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".log",
        ".py",
        ".js",
        ".ts",
        ".html",
        ".css",
        ".sh",
        ".ps1",
    }
)
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi"})

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class AttachmentError(RuntimeError):
    """A file was refused or could not be read. The message is user-facing."""


@dataclass(frozen=True)
class Attachment:
    """One stored file and what the model will be told about it."""

    name: str
    path: Path
    size: int
    kind: str  # text | pdf | image | video | other
    status: str  # inlined | referenced
    text: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        """The JSON shape the browser sees (no file content)."""
        return {
            "name": self.name,
            "path": str(self.path),
            "size": self.size,
            "kind": self.kind,
            "status": self.status,
            "note": self.note,
        }


def human_size(size: int) -> str:
    """``512 B``, ``4.0 KB``, ``5.0 MB`` - the units a user compares against."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"  # pragma: no cover - unreachable


def uploads_root() -> Path:
    from .toolsets.memory import uh_home

    return uh_home() / "uploads"


def session_dir(session_id: str) -> Path:
    """Where one session's files live. Created on demand."""
    safe = _SAFE.sub("_", session_id.strip()) or "session"
    return uploads_root() / safe


def safe_name(filename: str) -> str:
    """A file name that cannot escape its folder or hide itself."""
    base = Path(filename.replace("\\", "/")).name.strip()
    base = _SAFE.sub("_", base).lstrip(".") or "file"
    return base[:120]


def _unique(folder: Path, name: str) -> Path:
    """``notes.md``, then ``notes-2.md``: uploading twice never overwrites."""
    target = folder / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(2, 1000):
        candidate = folder / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise AttachmentError(f"too many files named {name} in {folder}")


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _pdf_text(path: Path) -> tuple[str, str]:
    """Extracted text and a note. Empty text with a note when pypdf is absent."""
    try:
        from pypdf import PdfReader  # optional: never a dependency
    except ImportError:
        return "", "PDF text extraction needs pypdf: pip install pypdf"
    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - a corrupt PDF is a note, not a crash
        return "", f"unreadable PDF ({type(exc).__name__})"
    text = "\n".join(page.strip() for page in pages if page.strip())
    if not text:
        return "", "no extractable text (scanned PDF? it would need OCR)"
    return text, ""


def extract(path: Path, name: Optional[str] = None) -> Attachment:
    """Describe one stored file: kind, status, and the text when it is inlined."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise AttachmentError(f"cannot read {path.name}: {exc}") from exc
    label = name or path.name
    suffix = path.suffix.lower()

    if suffix in IMAGE_SUFFIXES or suffix in VIDEO_SUFFIXES:
        kind = "image" if suffix in IMAGE_SUFFIXES else "video"
        return Attachment(
            name=label,
            path=path,
            size=size,
            kind=kind,
            status="referenced",
            note=(
                "the current providers read text only: the file is stored and its "
                "path is below, but the model cannot see it"
            ),
        )

    if suffix == ".pdf":
        text, note = _pdf_text(path)
        if text and len(text.encode("utf-8")) <= INLINE_LIMIT:
            return Attachment(label, path, size, "pdf", "inlined", text=text)
        if text:
            note = "too long to inline; ask for a page range or a smaller extract"
        return Attachment(label, path, size, "pdf", "referenced", note=note)

    if suffix in TEXT_SUFFIXES:
        try:
            text = _decode(path.read_bytes())
        except OSError as exc:
            raise AttachmentError(f"cannot read {path.name}: {exc}") from exc
        if len(text.encode("utf-8")) <= INLINE_LIMIT:
            return Attachment(label, path, size, "text", "inlined", text=text)
        return Attachment(
            label,
            path,
            size,
            "text",
            "referenced",
            note=(
                f"over {human_size(INLINE_LIMIT)}, so it is not inlined: read it with "
                "the file tool, or ask for the part you need"
            ),
        )

    return Attachment(
        label,
        path,
        size,
        "other",
        "referenced",
        note="unknown type: stored, not read",
    )


def save(session_id: str, filename: str, data: bytes) -> Attachment:
    """Store one upload and describe it. Raises :class:`AttachmentError`."""
    if not data:
        raise AttachmentError("the file is empty")
    if len(data) > MAX_BYTES:
        raise AttachmentError(
            f"the file is {human_size(len(data))}; the limit is {human_size(MAX_BYTES)}"
        )
    folder = session_dir(session_id)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = _unique(folder, safe_name(filename))
        target.write_bytes(data)
    except OSError as exc:
        raise AttachmentError(f"cannot store the file: {exc}") from exc
    return extract(target)


def resolve(session_id: str, raw_path: str) -> Path:
    """A path the browser sent back, checked against this session's folder."""
    candidate = Path(raw_path).expanduser().resolve()
    folder = session_dir(session_id).resolve()
    if candidate != folder and folder not in candidate.parents:
        raise AttachmentError(f"{candidate.name} is not an attachment of this session")
    if not candidate.is_file():
        raise AttachmentError(f"{candidate.name} no longer exists")
    return candidate


def compose(text: str, paths: Iterable[str], session_id: str) -> str:
    """The user's message plus the attachments block. Empty input is unchanged.

    Raises :class:`AttachmentError` for a path outside the session: an attachment
    list is not a licence to read arbitrary files.
    """
    items: List[Attachment] = []
    for raw in paths or []:
        target = resolve(session_id, str(raw))
        items.append(extract(target))
    if not items:
        return text
    lines = [text, "", HEADER]
    for item in items:
        lines.append(f"- {item.name} ({human_size(item.size)}) [{item.status}] {item.path}")
        if item.status == "inlined" and item.text:
            lines.append("```")
            lines.append(item.text.rstrip("\n"))
            lines.append("```")
        elif item.note:
            lines.append(f"  note: {item.note}")
    return "\n".join(lines).strip()


__all__ = [
    "Attachment",
    "AttachmentError",
    "HEADER",
    "INLINE_LIMIT",
    "MAX_BYTES",
    "compose",
    "extract",
    "human_size",
    "resolve",
    "safe_name",
    "save",
    "session_dir",
    "uploads_root",
]
