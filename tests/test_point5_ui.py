"""Point 5: the five interface corrections, and the server-side halves they need.

Three of the five live in the browser (one status line per image, exclusive and
reversible votes, an honest label) and are asserted where they exist - in the
served `app.js` / `style.css`, which is the only thing the page runs. The two that
need a server contract are tested here for real: ``restore`` (the undo behind the
trash banner) and ``answer_meta`` (the one model value the header, the footer and
the local-model tab all read).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uh import artifacts
from uh.web.app import answer_meta

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "uh" / "web" / "static" / "app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "uh" / "web" / "static" / "style.css").read_text(encoding="utf-8")


@pytest.fixture()
def runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(artifacts, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(artifacts, "RATINGS_FILE", tmp_path / "ratings.jsonl")
    run = tmp_path / "runs" / "run-20260101-000000-abcdef"
    run.mkdir(parents=True)
    return run


# --------------------------------------------------------------------------- #
# (2) the trash, and its undo
# --------------------------------------------------------------------------- #
def test_restore_puts_the_file_back_where_it_came_from(runs: Path) -> None:
    image = runs / "courbe.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    provenance = {"path": str(image)}

    artifacts.trash(provenance)
    assert not image.exists(), "the click took it out of the run"

    result = artifacts.restore(provenance)

    assert result["restored"] is True
    assert image.read_bytes() == b"\x89PNG\r\n\x1a\nfake", "same bytes, same place"
    assert artifacts.trash_count() == 0, "the sidecar went with it"


def test_restore_refuses_rather_than_overwrite(runs: Path) -> None:
    """Undo must never destroy a file that appeared at the original path."""
    image = runs / "courbe.png"
    image.write_bytes(b"first")
    provenance = {"path": str(image)}
    artifacts.trash(provenance)
    image.write_bytes(b"second")

    with pytest.raises(artifacts.ArtifactActionError) as refusal:
        artifacts.restore(provenance)

    assert "déjà" in str(refusal.value)
    assert image.read_bytes() == b"second", "nothing was lost"


def test_restore_of_a_file_that_is_not_in_the_trash_says_so(runs: Path) -> None:
    with pytest.raises(artifacts.ArtifactActionError) as refusal:
        artifacts.restore({"path": str(runs / "jamais-vue.png")})
    assert "corbeille" in str(refusal.value)


def test_the_browser_hides_the_image_and_offers_the_undo() -> None:
    assert "TRASH_UNDO_MS" in APP_JS
    assert 'figure.classList.add("trashed")' in APP_JS
    assert 'el("button", "tool-artifact-undo", "Annuler")' in APP_JS
    assert '{ ...base, action: "restore" }' in APP_JS
    assert ".tool-artifact.trashed .tool-artifact-img { display: none; }" in STYLE


# --------------------------------------------------------------------------- #
# (3) exclusive, reversible votes
# --------------------------------------------------------------------------- #
def test_a_retracted_vote_is_journalled_not_forgotten(runs: Path) -> None:
    image = runs / "courbe.png"
    image.write_bytes(b"x")
    provenance = {"path": str(image)}

    artifacts.rate(provenance, "up", session_id="s1")
    artifacts.rate(provenance, "none", session_id="s1")

    lines = [json.loads(line) for line in Path(artifacts.RATINGS_FILE).read_text().splitlines()]
    assert [line["vote"] for line in lines] == ["up", "none"]


def test_a_vote_that_is_not_in_the_vocabulary_is_refused(runs: Path) -> None:
    with pytest.raises(artifacts.ArtifactActionError) as refusal:
        artifacts.rate({"path": str(runs / "x.png")}, "maybe")
    assert "none" in str(refusal.value), "the refusal names what is accepted"


def test_the_two_thumbs_are_mutually_exclusive_in_the_page() -> None:
    assert "figure.dataset.vote" in APP_JS
    assert 'current === vote ? "none" : vote' in APP_JS, "clicking the active thumb retracts"
    assert 'setAttribute("aria-pressed"' in APP_JS, "state is announced, not only coloured"
    assert "node.classList.toggle(\"voted\", active)" in APP_JS


# --------------------------------------------------------------------------- #
# (1) one status line, never two glued words
# --------------------------------------------------------------------------- #
def test_a_figure_has_one_status_line_and_it_is_a_block() -> None:
    assert 'figure.querySelector(".tool-artifact-status")' in APP_JS, (
        "the note is looked up, not appended anew on every action"
    )
    assert APP_JS.count("function figureStatus(") == 1
    assert ".tool-artifact-status { display: block;" in STYLE


def test_the_notes_that_produced_the_glued_words_are_gone() -> None:
    for old in ('"notée utile"', '"notée à éviter"', '"gardée", "safe"'):
        assert old not in APP_JS, f"{old} is exactly what glued two states together"


# --------------------------------------------------------------------------- #
# (4) the honest label
# --------------------------------------------------------------------------- #
def test_the_vote_label_does_not_claim_the_model_learns_yet() -> None:
    assert 'const VOTE_NOTE = "noté (pas encore utilisé pour s\'améliorer)"' in APP_JS
    assert "say(figure, wanted === \"none\" ? \"vote retiré\" : VOTE_NOTE)" in APP_JS


# --------------------------------------------------------------------------- #
# (5) one model value, everywhere
# --------------------------------------------------------------------------- #
class _Session:
    def __init__(self, served: str | None) -> None:
        self.last_real_model = served


def test_the_answer_frame_names_the_model_that_really_answered() -> None:
    frame = answer_meta(_Session("deepseek-flash"), "omnirouter", "auto/best-free")

    assert frame == {
        "type": "answer_meta",
        "provider": "omnirouter",
        "model": "deepseek-flash",
        "requested": "auto/best-free",
        "substituted": True,
    }


def test_a_turn_where_the_alias_was_the_model_is_not_called_a_substitution() -> None:
    frame = answer_meta(_Session("qwen3:8b"), "ollama", "qwen3:8b")
    assert frame is not None
    assert frame["substituted"] is False


def test_nothing_answered_yet_sends_no_frame() -> None:
    assert answer_meta(_Session(None), "ollama", "qwen3:8b") is None
    assert answer_meta(object(), "ollama", "qwen3:8b") is None


def test_the_page_renders_one_value_in_the_footer_the_header_and_the_tab() -> None:
    assert 'event === "answer_meta"' in APP_JS
    assert 'el("span", "answer-model")' in APP_JS
    assert "markLocalModelTab()" in APP_JS
    assert '.local-model-name.served::after { content: " · a répondu"' in STYLE
    # The header is updated from the same call, not from a second source.
    assert 'label.title = `${version} · fournisseur : ${provider} · ${tail}`' in APP_JS
