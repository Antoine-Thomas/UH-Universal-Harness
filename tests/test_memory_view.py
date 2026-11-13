"""Memory views: the wiki on disk, the RAG index, SiYuan, MEMORY.md, search.

The rule under test is honesty: a source that is absent says why, a source that
exists is listed with real sizes, and nothing outside a source root is readable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uh import memory_view


@pytest.fixture
def wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A wiki in UH_WIKI_DIR, with a nested note and a non-note file."""
    root = tmp_path / "wiki"
    (root / "concepts").mkdir(parents=True)
    (root / "index.md").write_text("# Index\nles concepts", encoding="utf-8")
    (root / "concepts" / "rag.md").write_text("# RAG\nrecherche hybride", encoding="utf-8")
    (root / "notes.txt").write_text("une note brute", encoding="utf-8")
    (root / "image.png").write_bytes(b"\x89PNG")
    monkeypatch.setenv("UH_WIKI_DIR", str(root))
    return root


# --------------------------------------------------------------------------- #
# wiki
# --------------------------------------------------------------------------- #
def test_the_configured_wiki_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "ailleurs"
    root.mkdir()
    monkeypatch.setenv("UH_WIKI_DIR", str(root))

    found = memory_view.wiki_root()

    assert found is not None
    assert found == ("configured", root)


def test_notes_are_listed_with_their_relative_path(wiki: Path) -> None:
    notes = memory_view.wiki_notes()

    paths = [note.path for note in notes]
    assert paths == ["concepts/rag.md", "index.md", "notes.txt"]
    assert all(note.source == "configured" for note in notes)
    assert next(n for n in notes if n.path == "index.md").size > 0


def test_a_png_is_not_a_note(wiki: Path) -> None:
    assert "image.png" not in [note.path for note in memory_view.wiki_notes()]


def test_a_note_can_be_read(wiki: Path) -> None:
    note = memory_view.read_note("concepts/rag.md")

    assert note["path"] == "concepts/rag.md"
    assert "recherche hybride" in note["text"]
    assert note["source"] == "configured"


def test_a_note_outside_the_wiki_is_refused(wiki: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(memory_view.MemoryViewError) as caught:
        memory_view.read_note("../secret.md")

    assert "outside the wiki" in str(caught.value)


def test_an_unknown_note_is_named(wiki: Path) -> None:
    with pytest.raises(memory_view.MemoryViewError) as caught:
        memory_view.read_note("nexiste-pas.md")
    assert "unknown note" in str(caught.value)


def test_a_huge_note_is_refused(wiki: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_view, "MAX_NOTE_BYTES", 10)
    (wiki / "gros.md").write_text("x" * 50, encoding="utf-8")

    with pytest.raises(memory_view.MemoryViewError) as caught:
        memory_view.read_note("gros.md")
    assert "limit" in str(caught.value)


def test_without_a_wiki_everything_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UH_WIKI_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(memory_view.Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    monkeypatch.setattr(memory_view, "_uhb_checkout", lambda: None)

    assert memory_view.wiki_root() is None
    assert memory_view.wiki_notes() == []
    with pytest.raises(memory_view.MemoryViewError):
        memory_view.read_note("index.md")


# --------------------------------------------------------------------------- #
# RAG and SiYuan: what UH cannot do, said plainly
# --------------------------------------------------------------------------- #
def test_rag_without_a_checkout_says_where_it_lives(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_view, "_uhb_checkout", lambda: None)

    state = memory_view.rag_state()

    assert state["available"] is False
    assert state["queryable"] is False
    assert "uhb/services/rag" in state["reason"]


def test_rag_without_an_index_names_the_indexer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rag = tmp_path / "uhb" / "services" / "rag"
    rag.mkdir(parents=True)
    monkeypatch.setattr(memory_view, "_uhb_checkout", lambda: tmp_path)

    state = memory_view.rag_state()

    assert state["available"] is False
    assert "README.md" in state["reason"], "the message must say where the index comes from"


def test_rag_with_an_index_reports_it_but_stays_unqueryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rag = tmp_path / "uhb" / "services" / "rag"
    rag.mkdir(parents=True)
    (rag / "index.faiss").write_bytes(b"0" * 2048)
    (rag / "chunks.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    (rag / "manifeste.json").write_text(json.dumps({"documents": ["a", "b", "c"]}), encoding="utf-8")
    monkeypatch.setattr(memory_view, "_uhb_checkout", lambda: tmp_path)

    state = memory_view.rag_state()

    assert state["available"] is True
    assert state["queryable"] is False, "UH does not invent a UHB route"
    assert state["chunks"] == 2
    assert state["documents"] == 3
    assert "no HTTP route" in state["reason"]


def test_siyuan_is_reported_as_unavailable_with_the_configured_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIYUAN_TOKEN", "x")
    monkeypatch.setenv("SIYUAN_URL", "http://127.0.0.1:6806")

    state = memory_view.siyuan_state()

    assert state["available"] is False, "UH itself does not speak SiYuan"
    assert state["reads_via"] == "uhb"
    assert state["ready"] is True
    assert "siyuan/blocks" in state["reason"], "the sentence names the route that reads it"


# --------------------------------------------------------------------------- #
# MEMORY.md and search
# --------------------------------------------------------------------------- #
def test_memory_file_absent_is_not_an_error(tmp_path: Path) -> None:
    state = memory_view.memory_file()

    assert state["available"] is False
    assert "path" in state


def test_memory_file_lists_its_sections(tmp_path: Path) -> None:
    path = tmp_path / "uh-home" / "MEMORY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Memory\n\n## Facts\nle ciel est bleu\n\n## Preferences\nreponses courtes\n",
        encoding="utf-8",
    )

    state = memory_view.memory_file()

    assert state["available"] is True
    assert "Facts" in state["sections"]
    assert state["bytes_by_section"]["Facts"] > 0


def test_search_finds_a_memory_and_a_wiki_note(wiki: Path, tmp_path: Path) -> None:
    memory = tmp_path / "uh-home" / "MEMORY.md"
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text("# Memory\n\n## Facts\nUH tourne en local\n", encoding="utf-8")

    hits = memory_view.search_memory("local") + memory_view.search_memory("hybride")

    kinds = sorted({hit["kind"] for hit in hits})
    assert kinds == ["memory", "wiki"]
    assert any("UH tourne" in hit["snippet"] for hit in hits)


def test_search_is_case_insensitive_and_bounded(wiki: Path) -> None:
    hits = memory_view.search_memory("RECHERCHE", limit=1)

    assert len(hits) == 1
    assert hits[0]["kind"] == "wiki"


def test_an_empty_search_returns_nothing(wiki: Path) -> None:
    assert memory_view.search_memory("   ") == []


def test_sources_gathers_everything_in_one_call(wiki: Path) -> None:
    data = memory_view.sources()

    assert set(data) == {"wiki", "memory", "rag", "siyuan"}
    assert data["wiki"]["available"] is True
    assert data["wiki"]["notes"] == 3
    assert data["rag"]["available"] in (True, False)
