"""P1-14 close, P1-13 memory, P1-12 cleanup - the three promises, tested.

* a prepared copy lives exactly one turn, unless its original is marked « Garder »;
* a batch bigger than the per-turn cap is sampled, not rejected, and the operator is
  told how many were left out;
* a kept image lands in the memory as PNG lossless at full resolution, a kept voice
  clip as FLAC 24-bit stereo, indexed by SHA-256 and tied to its message;
* emptying the bin forgets the memory entries it holds, and nothing else;
* the sweep deletes only prepared/stale files, and the orphan report sees both ways.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from uh import attachments, maintenance, media, memory

pytest.importorskip("PIL")


def png(tmp: Path, name: str, size=(3840, 2160)) -> Path:
    from PIL import Image

    path = tmp / name
    Image.effect_noise(size, 80).convert("RGB").save(path)
    return path


def wav(tmp: Path, name: str = "clip.wav", seconds: float = 0.3) -> Path:
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x01" * int(24000 * seconds))
    path = tmp / name
    path.write_bytes(buffer.getvalue())
    return path


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True)
    return tmp_path


# --------------------------------------------------------------------------- #
# P1-14 : the purge, and the kept copy
# --------------------------------------------------------------------------- #
def test_the_prepared_copy_dies_with_the_turn(home: Path) -> None:
    session = home / "home" / "uploads" / "s1"
    session.mkdir(parents=True)
    original = png(session, "shot.png", size=(2400, 1350))

    record = media.prepare(original)
    sent = Path(record["sent"][0])
    assert sent.is_file()

    removed = media.purge(session)

    assert removed >= 1 and not sent.exists()
    assert original.is_file(), "the operator's file is not the turn's"


def test_a_kept_original_keeps_its_prepared_copy(home: Path) -> None:
    session = home / "home" / "uploads" / "s1"
    session.mkdir(parents=True)
    original = png(session, "shot.png", size=(2400, 1350))
    record = media.prepare(original)
    sent = Path(record["sent"][0])

    original.with_name(original.name + ".keep").write_text("{}", encoding="utf-8")
    media.purge(session)

    assert sent.is_file(), "garder l'image et perdre la copie envoyée serait incohérent"


def test_a_big_batch_is_sampled_not_refused(home: Path) -> None:
    session = home / "home" / "uploads" / "s2"
    session.mkdir(parents=True)
    files = [png(session, f"shot{index}.png", size=(1600, 900)) for index in range(15)]

    record = attachments.media_for([str(path) for path in files], "s2")

    assert len(record["images"]) == attachments.MAX_IMAGES_PER_TURN
    assert record["omitted"] == 15 - attachments.MAX_IMAGES_PER_TURN
    assert len(record["items"]) == 15, "every file was still prepared and reported"


# --------------------------------------------------------------------------- #
# P1-13 : the memory
# --------------------------------------------------------------------------- #
def test_a_kept_4k_picture_is_stored_as_lossless_png_at_full_size(home: Path) -> None:
    original = png(home, "big.png")
    entry = memory.keep(original, kind="image", session_id="s1", message_id=7)

    stored = Path(entry["path"])
    assert stored.suffix == ".png" and stored.is_file()
    assert (entry["width"], entry["height"]) == (3840, 2160), "a 4K comes back as 4K"
    assert entry["format"] == "png" and entry["message_id"] == 7
    assert len(entry["sha256"]) == 64


def test_keeping_twice_does_not_duplicate(home: Path) -> None:
    original = png(home, "big.png", size=(800, 600))
    first = memory.keep(original, kind="image")
    second = memory.keep(original, kind="image")

    assert first["sha256"] == second["sha256"]
    assert len(memory.entries()) == 1
    assert len(list((memory.memory_root() / "image").iterdir())) == 1


@pytest.mark.skipif(not media.ffmpeg_path(), reason="ffmpeg absent")
def test_a_kept_voice_clip_becomes_flac_24_bits_stereo(home: Path) -> None:
    source = wav(home)
    entry = memory.keep(source, kind="audio", session_id="s1")

    stored = Path(entry["path"])
    assert stored.suffix == ".flac" and stored.is_file()
    assert entry["format"] == "flac" and entry["bits"] == 24 and entry["channels"] == 2
    assert entry["bytes"] > 0 and entry["seconds"] > 0


def test_emptying_the_bin_forgets_only_that_file(home: Path) -> None:
    kept_a = png(home, "a.png", size=(400, 300))
    kept_b = png(home, "b.png", size=(400, 300))
    entry_a = memory.keep(kept_a, kind="image")
    memory.keep(kept_b, kind="image")

    removed = memory.drop_for(kept_a)

    assert removed == 1
    assert not Path(entry_a["path"]).exists()
    assert [str(Path(item["source"]).name) for item in memory.entries()] == ["b.png"]


def test_the_memory_weighs_what_it_says(home: Path) -> None:
    memory.keep(png(home, "a.png", size=(600, 400)), kind="image")
    weight = memory.weight()

    assert weight["entries"] == 1
    assert weight["bytes"] >= (memory.memory_root() / "image").stat().st_size
    assert weight["by_kind"].get("image", 0) > 0


# --------------------------------------------------------------------------- #
# P1-12 : sweep and orphan report
# --------------------------------------------------------------------------- #
def test_the_sweep_removes_prepared_copies_and_reports_nothing_it_kept(home: Path) -> None:
    session = home / "home" / "uploads" / "s3"
    session.mkdir(parents=True)
    original = png(session, "shot.png", size=(1600, 900))
    media.prepare(original)

    swept = maintenance.sweep_folder(session)

    assert swept["derived"] >= 1
    assert original.is_file()
    assert not (session / "derived").exists()


def test_an_orphan_file_is_reported_and_a_mentioned_one_is_not(home: Path) -> None:
    session = home / "home" / "uploads" / "s4"
    session.mkdir(parents=True)
    mentioned = png(session, "seen.png", size=(300, 200))
    png(session, "orphan.png", size=(300, 200))
    rows = [{"role": "user", "content": f"[attached files] - seen.png {mentioned}"}]

    data = maintenance.report(session, rows)

    reported = [Path(item["path"]).name for item in data["unmentioned"]]
    assert "orphan.png" in reported and "seen.png" not in reported
    assert data["missing"] == []


def test_a_message_pointing_at_a_missing_file_is_reported_not_deleted(home: Path) -> None:
    session = home / "home" / "uploads" / "s5"
    session.mkdir(parents=True)
    gone = session / "gone.png"
    rows = [{"role": "user", "content": f"regarde {gone}"}]

    data = maintenance.report(session, rows)

    assert str(gone) in data["missing"]
    assert not gone.exists(), "a report never recreates what it cannot find"


def test_clean_unmentioned_deletes_only_what_it_reported(home: Path) -> None:
    session = home / "home" / "uploads" / "s6"
    session.mkdir(parents=True)
    kept = png(session, "kept.png", size=(300, 200))
    orphan = png(session, "orphan.png", size=(300, 200))
    kept.with_name(kept.name + ".keep").write_text("{}", encoding="utf-8")
    rows: list[dict] = []

    result = maintenance.clean_unmentioned(session, rows)

    assert result["removed"] == 1
    assert orphan.exists() is False
    assert kept.is_file(), "a .keep file is never swept, mentioned or not"
