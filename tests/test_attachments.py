"""Attachments: the 4 KB threshold, the honest notes, the folder jail.

Every test works in an isolated ``$UH_HOME`` (conftest), so a file "stored for a
session" is stored under a temporary directory and cannot leak into the real one.
``pypdf`` is not a dependency: the PDF tests install a fake module, which is how
the extraction branch is exercised without pretending the library is here.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from uh import attachments
from uh.attachments import AttachmentError

SESSION = "20261113-0100-aaaa"


def store(name: str, data: bytes) -> attachments.Attachment:
    return attachments.save(SESSION, name, data)


# --------------------------------------------------------------------------- #
# names, sizes, folder
# --------------------------------------------------------------------------- #
def test_safe_name_cannot_escape_or_hide() -> None:
    assert attachments.safe_name("../../etc/passwd") == "passwd"
    assert attachments.safe_name(r"C:\Windows\system32\evil.dll") == "evil.dll"
    assert attachments.safe_name(".bashrc") == "bashrc"
    assert attachments.safe_name("  ") == "file"
    assert attachments.safe_name("notes été (1).md") == "notes_t_1_.md"


def test_human_size_reads_like_a_file_manager() -> None:
    assert attachments.human_size(512) == "512 B"
    assert attachments.human_size(4096) == "4.0 KB"
    assert attachments.human_size(5 * 1024 * 1024) == "5.0 MB"


def test_files_land_under_the_session_folder(tmp_path: Path) -> None:
    stored = store("notes.md", b"bonjour")

    assert attachments.uploads_root() == tmp_path / "uh-home" / "uploads"
    assert stored.path.parent == attachments.session_dir(SESSION)
    assert stored.path.read_bytes() == b"bonjour"


def test_the_same_name_twice_never_overwrites(tmp_path: Path) -> None:
    first = store("notes.md", b"un")
    second = store("notes.md", b"deux")

    assert first.path != second.path
    assert first.path.name == "notes.md"
    assert second.path.name == "notes-2.md"
    assert first.path.read_bytes() == b"un"


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(AttachmentError) as caught:
        store("vide.txt", b"")
    assert "empty" in str(caught.value)


def test_a_file_over_the_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attachments, "MAX_BYTES", 10)
    with pytest.raises(AttachmentError) as caught:
        store("gros.txt", b"x" * 11)
    assert "limit" in str(caught.value)


# --------------------------------------------------------------------------- #
# the threshold
# --------------------------------------------------------------------------- #
def test_short_text_is_inlined() -> None:
    stored = store("notes.md", "# Titre\nune note".encode())

    assert stored.kind == "text"
    assert stored.status == "inlined"
    assert stored.text == "# Titre\nune note"


def test_long_text_is_referenced_not_inlined() -> None:
    body = ("ligne de texte\n" * 600).encode()  # ~9 KB, over the 4 KB limit
    stored = store("journal.txt", body)

    assert stored.status == "referenced"
    assert stored.text == ""
    assert "file tool" in stored.note, "the note says what to do instead"


def test_a_text_file_exactly_at_the_limit_is_still_inlined() -> None:
    body = b"a" * attachments.INLINE_LIMIT
    assert store("pile.txt", body).status == "inlined"


def test_an_image_is_referenced_and_says_why() -> None:
    stored = store("photo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100)

    assert (stored.kind, stored.status) == ("image", "referenced")
    assert "text only" in stored.note
    assert "cannot see it" in stored.note


def test_a_video_is_referenced() -> None:
    stored = store("clip.mp4", b"\x00" * 200)
    assert (stored.kind, stored.status) == ("video", "referenced")


def test_an_unknown_type_is_stored_but_not_read() -> None:
    stored = store("archive.zip", b"PK\x03\x04")
    assert (stored.kind, stored.status) == ("other", "referenced")
    assert "unknown type" in stored.note


def test_a_pdf_without_pypdf_says_how_to_get_it() -> None:
    stored = store("contrat.pdf", b"%PDF-1.4 ...")

    assert (stored.kind, stored.status) == ("pdf", "referenced")
    assert "pip install pypdf" in stored.note


def test_a_pdf_is_extracted_when_pypdf_is_there(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake module: the point is our branch, not the library."""

    class Page:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class Reader:
        def __init__(self, _path: str) -> None:
            self.pages = [Page("premiere page"), Page("seconde page")]

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=Reader))

    stored = store("contrat.pdf", b"%PDF-1.4 ...")

    assert stored.status == "inlined"
    assert "premiere page" in stored.text and "seconde page" in stored.text


def test_a_pdf_without_text_says_it_would_need_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    class Reader:
        def __init__(self, _path: str) -> None:
            self.pages = []

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=Reader))

    assert "OCR" in store("scan.pdf", b"%PDF-1.4 ...").note


def test_a_corrupt_pdf_is_a_note_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    class Reader:
        def __init__(self, _path: str) -> None:
            raise ValueError("bad xref")

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=Reader))

    assert "unreadable PDF" in store("casse.pdf", b"%PDF-1.4 ...").note


# --------------------------------------------------------------------------- #
# the folder jail and the message
# --------------------------------------------------------------------------- #
def test_a_path_outside_the_session_folder_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(AttachmentError) as caught:
        attachments.resolve(SESSION, str(outside))

    assert "not an attachment of this session" in str(caught.value)


def test_a_dotdot_path_is_refused_too(tmp_path: Path) -> None:
    target = attachments.session_dir(SESSION)
    target.mkdir(parents=True, exist_ok=True)
    escape = target / ".." / ".." / ".." / "secret.txt"
    escape.write_text("secret", encoding="utf-8")

    with pytest.raises(AttachmentError):
        attachments.resolve(SESSION, str(escape))


def test_a_missing_attachment_is_named() -> None:
    folder = attachments.session_dir(SESSION)
    folder.mkdir(parents=True, exist_ok=True)
    with pytest.raises(AttachmentError) as caught:
        attachments.resolve(SESSION, str(folder / "disparu.txt"))
    assert "no longer exists" in str(caught.value)


def test_compose_without_attachments_changes_nothing() -> None:
    assert attachments.compose("bonjour", [], SESSION) == "bonjour"


def test_compose_inlines_a_short_file() -> None:
    stored = store("notes.md", b"# Titre\ncontenu")

    composed = attachments.compose("lis ceci", [str(stored.path)], SESSION)

    assert composed.startswith("lis ceci")
    assert attachments.HEADER in composed
    assert "[inlined]" in composed
    assert "notes.md" in composed
    assert "contenu" in composed


def test_compose_references_a_long_file() -> None:
    stored = store("gros.txt", b"x" * 9000)

    composed = attachments.compose("resume", [str(stored.path)], SESSION)

    assert "[referenced]" in composed
    assert str(stored.path) in composed
    assert "x" * 9000 not in composed, "the body must not go out"


def test_compose_works_with_no_text_at_all() -> None:
    stored = store("photo.png", b"\x89PNG" + b"0" * 50)

    composed = attachments.compose("", [str(stored.path)], SESSION)

    assert composed.startswith(attachments.HEADER)
    assert "cannot see it" in composed


def test_compose_refuses_a_file_from_elsewhere(tmp_path: Path) -> None:
    outside = tmp_path / "ailleurs.txt"
    outside.write_text("x", encoding="utf-8")

    with pytest.raises(AttachmentError):
        attachments.compose("hello", [str(outside)], SESSION)
