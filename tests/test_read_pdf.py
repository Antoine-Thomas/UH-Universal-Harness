"""read_pdf: offered only when pypdf imports, sandboxed, bounded, honest.

The tool is optional by construction: no pypdf, no tool, no error message. When
pypdf is there (faked here), the extraction, the page ranges and the 10 KB cap
are ours to test.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from uh.toolsets import file as file_mod
from uh.toolsets.file import FileToolset


def tool_names(toolset: FileToolset) -> set[str]:
    return {spec["function"]["name"] for spec in toolset.tools}


def fake_pdf(monkeypatch: pytest.MonkeyPatch, pages: list[str]) -> None:
    """Install a pypdf with the given page texts, and advertise the tool."""

    class Page:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class Reader:
        def __init__(self, _path: str) -> None:
            self.pages = [Page(text) for text in pages]

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=Reader))
    monkeypatch.setattr(file_mod, "pdf_available", lambda: True)


# --------------------------------------------------------------------------- #
# availability
# --------------------------------------------------------------------------- #
def test_without_pypdf_the_tool_does_not_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_mod, "pdf_available", lambda: False)
    toolset = FileToolset(workspace=Path.cwd())

    assert "read_pdf" not in tool_names(toolset)
    answer = toolset.call("read_pdf", {"path": "x.pdf"})
    assert answer.get("error"), "an unknown tool is an error, never a crash"


def test_with_pypdf_the_tool_is_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pdf(monkeypatch, ["page"])
    toolset = FileToolset(workspace=Path.cwd())

    assert "read_pdf" in tool_names(toolset)


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #
def test_it_returns_the_text_and_the_page_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pdf(monkeypatch, ["premiere", "seconde", "troisieme"])
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF-1.4")
    toolset = FileToolset(workspace=tmp_path)

    answer = toolset.call("read_pdf", {"path": "doc.pdf"})

    assert answer["pages"] == 3
    assert "premiere" in answer["text"] and "troisieme" in answer["text"]
    assert answer["truncated"] is False
    assert answer["path"] == "doc.pdf"


def test_a_page_range_is_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pdf(monkeypatch, ["un", "deux", "trois", "quatre"])
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF-1.4")
    toolset = FileToolset(workspace=tmp_path)

    answer = toolset.call("read_pdf", {"path": "doc.pdf", "pages": "2-3"})

    assert answer["read_pages"] == [2, 3]
    assert "deux" in answer["text"] and "trois" in answer["text"]
    assert "quatre" not in answer["text"]


def test_a_list_of_pages_is_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pdf(monkeypatch, ["un", "deux", "trois"])
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4")
    toolset = FileToolset(workspace=tmp_path)

    answer = toolset.call("read_pdf", {"path": "doc.pdf", "pages": "1,3"})

    assert answer["read_pages"] == [1, 3]


def test_default_is_the_head_of_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pdf(monkeypatch, [f"page {index}" for index in range(1, 70)])
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4")
    toolset = FileToolset(workspace=tmp_path)

    answer = toolset.call("read_pdf", {"path": "doc.pdf"})

    assert answer["pages"] == 69
    assert len(answer["read_pages"]) == file_mod.MAX_PDF_PAGES
    assert answer["truncated"] is True, "the rest of the document was not sent"


def test_a_large_extract_is_cut_at_ten_kilobytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pdf(monkeypatch, ["x" * 6000, "y" * 6000])
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4")
    toolset = FileToolset(workspace=tmp_path)

    answer = toolset.call("read_pdf", {"path": "doc.pdf"})

    assert answer["truncated"] is True
    assert len(answer["text"]) <= file_mod.MAX_PDF_CHARS + 100
    assert "text truncated" in answer["text"]


# --------------------------------------------------------------------------- #
# refusals
# --------------------------------------------------------------------------- #
def test_a_page_out_of_range_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pdf(monkeypatch, ["un", "deux"])
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4")
    toolset = FileToolset(workspace=tmp_path)

    answer = toolset.call("read_pdf", {"path": "doc.pdf", "pages": "9"})

    assert "does not exist" in answer["error"]
    assert "2 pages" in answer["error"]


def test_a_nonsense_range_is_a_tool_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pdf(monkeypatch, ["un"])
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4")
    toolset = FileToolset(workspace=tmp_path)

    answer = toolset.call("read_pdf", {"path": "doc.pdf", "pages": "deux-trois"})

    assert "page range" in answer["error"]


def test_a_non_pdf_file_is_refused_with_the_right_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pdf(monkeypatch, ["un"])
    (tmp_path / "notes.md").write_text("bonjour", encoding="utf-8")
    toolset = FileToolset(workspace=tmp_path)

    answer = toolset.call("read_pdf", {"path": "notes.md"})

    assert "not a PDF" in answer["error"]
    assert "read_file" in answer["error"]


def test_a_corrupt_pdf_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Reader:
        def __init__(self, _path: str) -> None:
            raise ValueError("bad xref")

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=Reader))
    monkeypatch.setattr(file_mod, "pdf_available", lambda: True)
    (tmp_path / "casse.pdf").write_bytes(b"%PDF")
    toolset = FileToolset(workspace=tmp_path)

    answer = toolset.call("read_pdf", {"path": "casse.pdf"})

    assert "cannot open the PDF" in answer["error"]


def test_the_sandbox_still_applies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pdf(monkeypatch, ["un"])
    outside = tmp_path.parent / "secret.pdf"
    outside.write_bytes(b"%PDF")
    toolset = FileToolset(workspace=tmp_path)

    answer = toolset.call("read_pdf", {"path": str(outside)})

    assert answer.get("error")
    assert "text" not in answer, "a refusal never leaks content"
