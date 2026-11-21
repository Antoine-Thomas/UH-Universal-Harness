"""P1-14: a 4K image is shrunk before it leaves, a video becomes frames, and the
turn asks for an analysis when the operator only dropped files.

The four promises of the lot, each tested where it lives:

* the original file is never touched, and the copy is under the budget;
* a video is reduced to keyframes (+ its soundtrack when it has one);
* `_images` exists only on the request copy - never in the stored history;
* files without words become a normal French question, not a list of file names.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from uh import attachments, media
from uh.agent import _with_images
from uh.providers import request_messages

pytest.importorskip("PIL")


def make_image(path: Path, size=(3840, 2160), colour=(12, 160, 147)) -> Path:
    from PIL import Image, ImageDraw

    # Full-frame noise: a flat synthetic picture compresses to a few hundred KB and
    # would let a broken budget pass. A screenshot is closer to this.
    image = Image.effect_noise(size, 90).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.text((200, 200), "HELLO 42", fill=(255, 255, 255))
    image.save(path)
    return path


# --------------------------------------------------------------------------- #
# images
# --------------------------------------------------------------------------- #
def test_a_4k_image_is_shrunk_under_the_budget_and_the_original_survives(tmp_path: Path) -> None:
    original = make_image(tmp_path / "capture-4k.png")
    before = original.stat().st_size
    assert before > media.IMAGE_BUDGET, "the fixture must be heavier than the budget"

    sent = media.shrink_image(original)

    assert sent != original and sent.is_file()
    assert sent.stat().st_size <= media.IMAGE_BUDGET
    assert media.image_info(sent)["width"] <= media.MAX_SIDE
    assert original.stat().st_size == before, "the file the operator chose is untouched"
    assert media.kind_of(sent) == "image"


def test_shrinking_refuses_a_file_that_is_not_an_image(tmp_path: Path) -> None:
    bogus = tmp_path / "not-an-image.png"
    bogus.write_bytes(b"definitely not a png")
    with pytest.raises(media.MediaError):
        media.shrink_image(bogus)


# --------------------------------------------------------------------------- #
# video
# --------------------------------------------------------------------------- #
def _ffmpeg_available() -> bool:
    return bool(media.ffmpeg_path())


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg absent")
def test_a_video_becomes_keyframes_and_reports_its_codec(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [
            media.ffmpeg_path(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        capture_output=True,
        check=False,
        timeout=120,
    )
    if not clip.is_file():
        pytest.skip("ffmpeg cannot encode libx264 here")

    info = media.video_info(clip)
    assert info["codec"] in {"h264", "avc1"} and info["seconds"] > 0
    assert info["hevc"] is False, "H.264 is not flagged as HEVC"

    frames = media.video_frames(clip, count=3)
    assert len(frames) >= 1
    for frame in frames:
        assert frame.is_file() and frame.parent.name == "derived"
        assert media.image_info(frame)["width"] <= media.MAX_SIDE


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg absent")
def test_the_soundtrack_is_extracted_when_there_is_one(tmp_path: Path) -> None:
    clip = tmp_path / "with-sound.mp4"
    subprocess.run(
        [
            media.ffmpeg_path(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(clip),
        ],
        capture_output=True,
        check=False,
        timeout=120,
    )
    if not clip.is_file():
        pytest.skip("ffmpeg cannot build an mp4 with sound here")

    soundtrack = media.video_audio(clip)
    assert soundtrack is not None and soundtrack.is_file()


# --------------------------------------------------------------------------- #
# prepare + purge
# --------------------------------------------------------------------------- #
def test_prepare_says_what_a_turn_would_send(tmp_path: Path) -> None:
    image = make_image(tmp_path / "big.png", size=(3000, 2000))
    record = media.prepare(image)

    assert record["kind"] == "image"
    assert record["original"] == str(image)
    assert len(record["sent"]) == 1 and Path(record["sent"][0]).stat().st_size <= media.IMAGE_BUDGET


def test_purge_removes_only_the_prepared_copies(tmp_path: Path) -> None:
    original = make_image(tmp_path / "keep.png", size=(800, 600))
    media.shrink_image(original)
    assert list((tmp_path / "derived").iterdir()), "a copy exists before the purge"

    removed = media.purge(tmp_path)

    assert removed >= 1
    assert not (tmp_path / "derived").exists()
    assert original.is_file(), "the original belongs to the operator, not to the purge"


# --------------------------------------------------------------------------- #
# the wire: images per provider, never in the store
# --------------------------------------------------------------------------- #
def test_ollama_gets_an_images_array_and_openai_gets_content_parts() -> None:
    base = [{"role": "user", "content": "que vois-tu ?", "_images": ["QUJD"]}]

    ollama = request_messages(base, wire="ollama")
    assert ollama[0]["images"] == ["QUJD"]
    assert "_images" not in ollama[0]

    openai = request_messages(base, wire="openai")
    parts = openai[0]["content"]
    assert isinstance(parts, list) and parts[0]["type"] == "text"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "_images" not in openai[0]


def test_an_unmeasured_wire_gets_text_only_and_loses_nothing_else() -> None:
    base = [{"role": "user", "content": "salut", "_images": ["QUJD"]}]
    out = request_messages(base, wire="something-else")
    assert out[0]["content"] == "salut"
    assert "images" not in out[0] and "_images" not in out[0]


def test_with_images_works_on_a_copy_so_the_history_stays_storable(tmp_path: Path) -> None:
    picture = tmp_path / "x.jpg"
    picture.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 200)

    history = [{"role": "user", "content": "lis ça"}]
    outgoing = _with_images(history, [str(picture)])

    assert "_images" in outgoing[0], "the request carries the bytes"
    assert "_images" not in history[0], "the stored message does not"
    json.dumps(history)  # still serialisable into SQLite


# --------------------------------------------------------------------------- #
# files without words
# --------------------------------------------------------------------------- #
def test_files_without_words_become_a_question(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    session_id = "20261119-1200-aaaa"
    folder = attachments.session_dir(session_id)
    folder.mkdir(parents=True)
    image = make_image(folder / "capture.png", size=(1600, 900))

    text = attachments.compose(attachments.AUTO_ANALYSIS, [str(image)], session_id)

    assert text.startswith("Analyse les pièces jointes")
    assert "capture.png" in text, "the message still names the file it talks about"


def test_media_for_prepares_an_uploaded_image(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    session_id = "20261119-1200-bbbb"
    folder = attachments.session_dir(session_id)
    folder.mkdir(parents=True)
    image = make_image(folder / "shot.png", size=(2400, 1350))

    prepared = attachments.media_for([str(image)], session_id)

    assert len(prepared["images"]) == 1
    assert Path(prepared["images"][0]).stat().st_size <= media.IMAGE_BUDGET
    assert prepared["items"][0]["kind"] == "image"
