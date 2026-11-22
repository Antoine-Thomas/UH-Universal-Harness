"""P1-15: the audio chain, its refusals, and the promise that it stays out of the way.

The anti-regression rule of this lot is architectural, so it is tested architecturally:
`uh/agent.py` and `uh/providers/` must not import the audio package at all. If they
never see it, no key, codec or engine failure can reach a text turn, and the SSE
frames stay exactly what they were.

The chain itself is tested with fake engines rather than real ones: what matters is
the ORDER, the fallback on a 429, and that a total failure names every attempt
instead of saying "audio failed".
"""

from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from uh import artifacts, audio
from uh.audio import local as audio_local
from uh.audio import voxtral as audio_voxtral

ROOT = Path(__file__).resolve().parents[1]


def wav_bytes(seconds: float = 0.4, rate: int = 24_000) -> bytes:
    """A real, tiny WAV: 24 kHz mono PCM16, like the engines write."""
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x01" * int(rate * seconds))
    return buffer.getvalue()


@pytest.fixture()
def uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(audio, "uploads_dir", lambda session_id: _folder(tmp_path, session_id))
    return tmp_path / "home" / "uploads"


def _folder(tmp: Path, session_id: str) -> Path:
    folder = tmp / "home" / "uploads" / (session_id or "audio") / "audio"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# --------------------------------------------------------------------------- #
# the architectural promise
# --------------------------------------------------------------------------- #
def test_the_chat_turn_cannot_see_audio() -> None:
    """No import in the turn path - the whole anti-regression rule in one test."""
    for path in [
        ROOT / "uh" / "agent.py",
        ROOT / "uh" / "console.py",
        *sorted((ROOT / "uh" / "providers").glob("*.py")),
    ]:
        source = path.read_text(encoding="utf-8")
        assert "import audio" not in source, f"{path.name} imports the audio package"
        assert "from .. import audio" not in source
        assert "from .audio" not in source


def test_importing_the_agent_does_not_load_the_audio_package() -> None:
    """Same rule, measured at import time rather than by grep - and in a clean process."""
    code = "import sys, uh.agent, uh.console\nprint('uh.audio' in sys.modules)\n"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr[-400:]
    assert result.stdout.strip() == "False", "importing a turn must not pull the audio stack"


def test_the_routes_are_the_only_door() -> None:
    source = (ROOT / "uh" / "web" / "app.py").read_text(encoding="utf-8")
    for route in (
        "/api/audio/capabilities",
        "/api/audio/transcribe",
        "/api/audio/speak",
        "/api/audio/file",
    ):
        assert f'"{route}"' in source
    # The audio package is imported inside the handlers, never at module scope.
    assert "from .. import audio as uh_audio" in source
    assert "\nfrom ..audio import" not in source


# --------------------------------------------------------------------------- #
# the chain
# --------------------------------------------------------------------------- #
def test_the_chain_order_is_the_one_documented() -> None:
    assert audio.tts_chain() == ["voxtral", "xtts", "piper"]
    assert audio.stt_chain() == ["voxtral", "whisper"]


def test_capabilities_name_the_reason_for_every_missing_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audio, "mistral_key", lambda: "")
    monkeypatch.setattr(audio_local, "xtts_python", lambda: None)
    monkeypatch.setattr(audio_local, "piper", lambda: None)
    monkeypatch.setattr(audio_local, "whisper_available", lambda: False)

    data = audio.capabilities()

    assert [engine["name"] for engine in data["engines"]] == ["voxtral", "xtts", "piper", "whisper"]
    assert data["tts"] is False and data["stt"] is False
    for engine in data["engines"]:
        assert engine["available"] is False
        assert engine["reason"], f"{engine['name']} must say why it cannot run"


def test_speak_falls_back_to_the_local_engine_when_voxtral_refuses(
    uploads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 429 case, which is the one measured on this machine."""
    monkeypatch.setattr(audio, "mistral_key", lambda: "fake-key-for-the-test")

    def refused(*args, **kwargs):
        raise audio_voxtral.VoxtralError("synthèse HTTP 429")

    monkeypatch.setattr(audio_voxtral, "speech", refused)
    monkeypatch.setattr(audio_local, "xtts_python", lambda: "python")
    monkeypatch.setattr(audio_local, "xtts_speak", lambda text, ref=None: wav_bytes())
    monkeypatch.setattr(audio_local, "piper", lambda: None)

    clip = audio.speak("bonjour", session_id="s1")

    assert clip["engine"] == "xtts"
    assert Path(clip["path"]).is_file()
    assert clip["source"]["root"] == "uploads", "the toolbar needs the right root"
    assert clip["url"].startswith("/api/audio/file?path=")
    assert 0.3 < clip["seconds"] < 0.5


def test_speak_falls_back_to_piper_when_xtts_is_absent(
    uploads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audio, "mistral_key", lambda: "")
    monkeypatch.setattr(audio_local, "xtts_python", lambda: None)
    monkeypatch.setattr(audio_local, "piper", lambda: "piper.exe")
    monkeypatch.setattr(audio_local, "piper_speak", lambda text: wav_bytes())

    clip = audio.speak("bonjour", session_id="s1")

    assert clip["engine"] == "piper"


def test_no_engine_names_every_attempt(uploads: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "mistral_key", lambda: "")
    monkeypatch.setattr(audio_local, "xtts_python", lambda: None)
    monkeypatch.setattr(audio_local, "piper", lambda: None)

    with pytest.raises(audio.AudioUnavailable) as refusal:
        audio.speak("bonjour", session_id="s1")

    message = str(refusal.value)
    for name in ("voxtral", "xtts", "piper"):
        assert name in message, f"{name} must appear in the refusal, with its reason"


def test_transcribe_says_which_engine_is_missing(
    uploads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audio, "mistral_key", lambda: "")
    monkeypatch.setattr(audio_local, "whisper_available", lambda: False)

    with pytest.raises(audio.AudioUnavailable) as refusal:
        audio.transcribe(wav_bytes(), session_id="s1")

    assert "voxtral" in str(refusal.value) and "whisper" in str(refusal.value)


def test_transcribe_uses_the_first_engine_that_answers(
    uploads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audio, "mistral_key", lambda: "fake-key")
    monkeypatch.setattr(audio_voxtral, "transcribe", lambda clip, key, language="fr": "  bonjour  ")
    monkeypatch.setattr(audio_local, "whisper_available", lambda: True)

    result = audio.transcribe(wav_bytes(), session_id="s1")

    assert result["text"] == "bonjour"
    assert result["engine"] == "voxtral"


# --------------------------------------------------------------------------- #
# files and refusals
# --------------------------------------------------------------------------- #
def test_a_path_outside_the_uploads_root_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "uploads").mkdir(parents=True)
    outside = tmp_path / "secret.wav"
    outside.write_bytes(wav_bytes())

    with pytest.raises(audio.AudioUnavailable):
        audio.resolve_upload(str(outside))


def test_a_clip_is_referenced_not_stored_in_the_thread(uploads: Path) -> None:
    path = audio.save_clip("s1", wav_bytes(), ".wav")
    reference = audio.reference(path, engine="xtts")

    assert reference["kind"] == "speech"
    assert reference["bytes"] == path.stat().st_size
    assert "data" not in reference and "data_url" not in reference, (
        "no base64 in the thread: the record is a reference"
    )
    json.dumps(reference)  # it must be JSON-serialisable for the store


def test_higher_fidelity_conversion_refuses_rather_than_downsample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audio, "ffmpeg_path", lambda: None)
    source = tmp_path / "a.wav"
    source.write_bytes(wav_bytes())

    with pytest.raises(audio.AudioUnavailable) as refusal:
        audio.to_flac24(source)

    assert "ffmpeg" in str(refusal.value)


# --------------------------------------------------------------------------- #
# the actions a voice clip must reach (shared with images)
# --------------------------------------------------------------------------- #
def test_a_voice_clip_can_be_trashed_and_restored(tmp_path: Path, monkeypatch) -> None:
    """The same bar as an image: keep, vote, bin with undo - through the same server."""
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(artifacts, "RATINGS_FILE", tmp_path / "ratings.jsonl")
    clip = tmp_path / "home" / "uploads" / "s1" / "audio" / "clip.wav"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(wav_bytes())
    provenance = {"path": str(clip), "root": "uploads"}

    artifacts.keep(provenance)
    assert artifacts.is_kept(provenance)
    artifacts.trash(provenance)
    assert not clip.exists()
    artifacts.restore(provenance)
    assert clip.is_file(), "the undo works for a voice clip too"


def test_a_vote_without_a_file_is_still_a_vote(tmp_path: Path, monkeypatch) -> None:
    """A text answer has no file: the journal records the opinion anyway."""
    monkeypatch.setattr(artifacts, "RATINGS_FILE", tmp_path / "ratings.jsonl")

    result = artifacts.rate({}, "up", session_id="s1", message_id=42)

    assert result["rated"] == "up"
    line = json.loads(Path(artifacts.RATINGS_FILE).read_text(encoding="utf-8").splitlines()[-1])
    assert line["message"] == 42 and line["file"] == ""
