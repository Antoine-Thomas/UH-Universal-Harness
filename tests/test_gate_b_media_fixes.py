"""Gate B's two real defects, and the guard that keeps them fixed.

1. Ollama runs a small context by default (8192 here) whatever the model card says:
   three 1536-pixel images overflow it and the turn dies on
   ``request (9641 tokens) exceeds the available context size``. The provider now asks
   for the *measured* window, bounded.
2. A video's soundtrack was sent to the vision model as if it were an image, and
   Ollama answered ``Failed to load image or audio file``. Only keyframes travel now;
   the soundtrack has its own key, for dictation.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from uh import capabilities, media
from uh.providers import ollama as ollama_provider


def test_the_context_asked_is_the_measured_one_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        capabilities, "describe", lambda provider, model: {"context": 131072, "verified": True}
    )
    assert ollama_provider.context_window("anything") == ollama_provider.NUM_CTX_CAP

    monkeypatch.setattr(
        capabilities, "describe", lambda provider, model: {"context": 8192, "verified": True}
    )
    assert ollama_provider.context_window("anything") == 8192


def test_an_unknown_model_gets_no_num_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    """No measurement, no invented window: the previous behaviour is kept."""
    monkeypatch.setattr(capabilities, "describe", lambda provider, model: {"context": None})
    assert ollama_provider.context_window("inconnu") == 0
    assert "num_ctx" not in ollama_provider.chat_options({}, model="inconnu")


def test_num_ctx_reaches_the_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        capabilities, "describe", lambda provider, model: {"context": 32768, "verified": True}
    )
    options = ollama_provider.chat_options({"temperature": 0.2}, model="qwen3.5:9b")
    assert options["num_ctx"] == 32768 and options["temperature"] == 0.2


def test_a_video_sends_frames_and_keeps_the_soundtrack_apart(tmp_path: Path) -> None:
    if not media.ffmpeg_path():
        pytest.skip("ffmpeg absent")
    import subprocess

    clip = tmp_path / "clip.mp4"
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
        pytest.skip("ffmpeg cannot build this clip here")

    record = media.prepare(clip)

    assert record["kind"] == "video" and record["sent"], "keyframes are what the model sees"
    assert all(Path(item).suffix == ".jpg" for item in record["sent"]), (
        "a WAV sent as an image is the 400 gate B measured"
    )
    if record["audio"]:
        assert record["soundtrack"].endswith(".wav")
        assert Path(record["soundtrack"]) not in [Path(item) for item in record["sent"]]


def test_the_soundtrack_is_still_extracted(tmp_path: Path) -> None:
    if not media.ffmpeg_path():
        pytest.skip("ffmpeg absent")
    import subprocess

    clip = tmp_path / "sound.mp4"
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
        pytest.skip("ffmpeg cannot build this clip here")
    track = media.video_audio(clip)
    assert track is not None
    with wave.open(str(track), "rb") as handle:
        assert handle.getnchannels() == 1 and handle.getframerate() == 16000
