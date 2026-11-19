"""Audio: dictation in, voice out - behind its own routes, never in the model path.

The rules this module exists to keep (P1-15), and which the tests hold:

* **nothing in the chat turn knows audio exists.** ``uh/agent.py`` and
  ``uh/providers/`` must never import this package: a broken engine, an absent key
  or a missing codec can then never break a text answer. A test reads the source and
  says so.
* **the chain degrades, and says why.** Voxtral 3 (needs ``MISTRAL_API_KEY``), then
  the local XTTS-v2 venv, then Piper on CPU, then "no engine" with one sentence the
  page can show. No silent failure, no preset voice passed off as a clone.
* **bytes live in files, never in the thread.** Audio is written under
  ``$UH_HOME/uploads/<session>/audio/`` and the message keeps a *reference*: path,
  mime, bytes, duration, sha256. SQLite never holds a base64 blob.

Voice cloning, in the order that actually works:

1. ``ref_audio`` (base64 of a 2-3 s WAV/MP3) - cloning for that one request, no
   account upgrade, and it is what the measured Voxtral runs used;
2. ``voice_id`` only when the account owns one (saved custom voices are refused on a
   free plan with HTTP 403 - a fact, not a guess);
3. the local engines do not clone at all unless XTTS-v2 is provisioned, in which
   case the reference speaker WAV is passed to it. Piper never clones: it is the
   "keep the feature alive" floor, and the interface labels it as a plain voice.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import local, voxtral

#: 24 kHz mono is what the measured Voxtral WAVs are; XTTS-v2 also writes 24 kHz.
SAMPLE_RATE = 24_000
#: One dictation or one answer-to-speech clip is small; this is a door, not a policy.
MAX_CLIP_BYTES = 40 * 1024 * 1024

AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".flac", ".ogg", ".m4a", ".opus", ".webm"})


class AudioUnavailable(RuntimeError):
    """No engine could serve the request - with the reason, never just "failed"."""


# --------------------------------------------------------------------------- #
# where the binaries and the keys live
# --------------------------------------------------------------------------- #
def ffmpeg_path() -> Optional[str]:
    """The ffmpeg this product uses: embedded first, then whatever the machine has.

    The roadmap commits to shipping a pinned static ffmpeg at install time (same
    renders on every machine). Until that installer exists, this resolves in the
    order that will still be right afterwards: ``~/.uh/bin/ffmpeg`` before PATH.
    """
    embedded = Path(os.environ.get("UH_HOME") or (Path.home() / ".uh")) / "bin" / "ffmpeg.exe"
    if embedded.is_file():
        return str(embedded)
    found = shutil.which("ffmpeg")
    return found


def _hermes_env_key(name: str) -> str:
    """Read one key from Hermes' env file. Never logged, never echoed."""
    candidates = [
        Path(os.environ.get("LOCALAPPDATA") or "") / "hermes" / ".env",
        Path.home() / "AppData" / "Local" / "hermes" / ".env",
    ]
    for path in candidates:
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def mistral_key() -> str:
    return str(os.environ.get(voxtral.KEY_ENV) or "").strip() or _hermes_env_key(voxtral.KEY_ENV)


# --------------------------------------------------------------------------- #
# what can actually run, here, now
# --------------------------------------------------------------------------- #
def capabilities() -> Dict[str, Any]:
    """The engines, their availability, and the reason when they are not.

    One shape for the whole product: the page draws a button only if a voice engine
    exists, and shows the reason when it does not. ``None`` availability reasons are
    sentences, not error codes.
    """
    has_key = bool(mistral_key())
    engines: List[Dict[str, Any]] = []

    engines.append(
        {
            "name": "voxtral",
            "label": "Voxtral 3 (Mistral)",
            "kind": "cloud",
            "clones": True,
            "stt": has_key,
            "tts": has_key,
            "available": has_key,
            "reason": "" if has_key else f"{voxtral.KEY_ENV} absente : rien ne part chez Mistral",
        }
    )

    xtts = local.xtts_python()
    engines.append(
        {
            "name": "xtts",
            "label": "XTTS-v2 (local)",
            "kind": "local",
            "clones": True,
            "stt": False,
            "tts": xtts is not None,
            "available": xtts is not None,
            "reason": "" if xtts else "XTTS-v2 non provisionné (~/.uhb/xtts absent)",
        }
    )

    piper = local.piper()
    engines.append(
        {
            "name": "piper",
            "label": "Piper (CPU)",
            "kind": "local",
            "clones": False,
            "stt": False,
            "tts": piper is not None,
            "available": piper is not None,
            "reason": "" if piper else "Piper absent (~/.uh/bin/piper + voix fr_FR)",
        }
    )

    whisper = local.whisper_available()
    engines.append(
        {
            "name": "whisper",
            "label": "faster-whisper (local)",
            "kind": "local",
            "clones": False,
            "stt": whisper,
            "tts": False,
            "available": whisper,
            "reason": "" if whisper else "faster-whisper non installé dans le venv UH",
        }
    )

    return {
        "engines": engines,
        "tts": any(engine["tts"] for engine in engines),
        "stt": any(engine["stt"] for engine in engines),
        "ffmpeg": ffmpeg_path(),
    }


def tts_chain() -> List[str]:
    """The TTS order, as names, independent of what is installed."""
    return ["voxtral", "xtts", "piper"]


def stt_chain() -> List[str]:
    return ["voxtral", "whisper"]


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #
def uploads_dir(session_id: str) -> Path:
    from .. import attachments

    folder = attachments.session_dir(session_id) / "audio"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_clip(session_id: str, data: bytes, suffix: str = ".wav") -> Path:
    """Write one clip under the session and return its path."""
    if not data:
        raise AudioUnavailable("aucun octet audio reçu")
    if len(data) > MAX_CLIP_BYTES:
        raise AudioUnavailable("clip audio trop lourd pour le chat (plafond 40 Mo)")
    folder = uploads_dir(session_id)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{hashlib.sha1(data).hexdigest()[:8]}{suffix}"
    path = folder / name
    path.write_bytes(data)
    return path


def resolve_upload(raw: str) -> Path:
    """A path the browser sent for an audio file, or a refusal.

    Same rule as attachments: only inside ``$UH_HOME/uploads``, symlinks resolved,
    nothing outside is ever read back.
    """
    from .. import attachments

    candidate = Path(str(raw or "")).expanduser()
    try:
        real = Path(os.path.realpath(candidate))
    except OSError as exc:
        raise AudioUnavailable("chemin audio illisible") from exc
    root = Path(os.path.realpath(attachments.uploads_root()))
    try:
        real.relative_to(root)
    except ValueError as exc:
        raise AudioUnavailable("refusé : ce fichier audio est hors du dossier des envois") from exc
    if not real.is_file():
        raise AudioUnavailable("fichier audio introuvable")
    return real


def seconds_of(path: Path) -> float:
    """Duration in seconds: the WAV header when it is a WAV, ffprobe otherwise."""
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate() or SAMPLE_RATE
                return round(frames / float(rate), 2)
        except (wave.Error, OSError):
            pass
    probe = ffmpeg_path()
    if probe:
        ffprobe = str(Path(probe).with_name("ffprobe.exe"))
        if not Path(ffprobe).is_file():
            ffprobe = shutil.which("ffprobe") or ""
        if ffprobe:
            try:
                out = subprocess.run(
                    [ffprobe, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", str(path)],
                    capture_output=True, text=True, timeout=20, check=False,
                )
                return round(float(out.stdout.strip() or 0), 2)
            except (OSError, ValueError, subprocess.SubprocessError):
                return 0.0
        del probe
    return 0.0


def reference(path: Path, *, engine: str, kind: str = "speech") -> Dict[str, Any]:
    """The record the thread keeps instead of the bytes.

    ``source`` is the same provenance shape the image toolbar uses (so keep, vote,
    trash and its undo work on a voice clip without a second implementation), with
    ``root: "uploads"`` telling the server which of the two allowed roots applies.
    ``url`` is what the player loads; it is a route, not a file path.
    """
    from urllib.parse import quote

    data_sha = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return {
        "kind": kind,
        "source": {"path": str(path), "root": "uploads"},
        "url": "/api/audio/file?path=" + quote(str(path), safe=""),
        "path": str(path),
        "name": path.name,
        "mime": "audio/wav" if path.suffix.lower() == ".wav" else "audio/mpeg",
        "bytes": path.stat().st_size,
        "seconds": seconds_of(path),
        "sha256": data_sha,
        "engine": engine,
    }


# --------------------------------------------------------------------------- #
# the chain
# --------------------------------------------------------------------------- #
def transcribe(data: bytes, *, filename: str = "clip.wav", language: str = "fr",
               session_id: str = "audio") -> Dict[str, Any]:
    """Speech to text, first engine that answers wins; the refusal says why not."""
    if not data:
        raise AudioUnavailable("aucun son reçu")
    clip = save_clip(session_id, data, Path(filename).suffix or ".wav")
    attempts: List[str] = []
    key = mistral_key()
    if key:
        try:
            text = voxtral.transcribe(clip, key, language=language)
            if text.strip():
                return {"text": text.strip(), "engine": "voxtral", "clip": str(clip)}
            attempts.append("voxtral : transcription vide")
        except Exception as exc:  # noqa: BLE001 - the chain continues, the reason does not vanish
            attempts.append(f"voxtral : {type(exc).__name__}: {exc}")
    else:
        attempts.append(f"voxtral : {voxtral.KEY_ENV} absente")

    if local.whisper_available():
        try:
            text = local.whisper_transcribe(clip, language=language)
            if text.strip():
                return {"text": text.strip(), "engine": "whisper", "clip": str(clip)}
            attempts.append("whisper : transcription vide")
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"whisper : {type(exc).__name__}: {exc}")
    else:
        attempts.append("whisper : faster-whisper non installé")

    raise AudioUnavailable("aucun moteur de dictée disponible — " + " | ".join(attempts))


def speak(text: str, *, voice: Optional[str] = None, session_id: str = "audio",
          reference_wav: Optional[Path] = None) -> Dict[str, Any]:
    """Text to a WAV file, first engine that answers wins.

    ``voice`` is a saved Voxtral voice id (paid accounts only). ``reference_wav`` is
    the operator's own sample, used as ``ref_audio`` for Voxtral and as the speaker
    for XTTS-v2: the same file, two engines, so switching engines does not switch
    voice behind their back.
    """
    body = str(text or "").strip()
    if not body:
        raise AudioUnavailable("rien à dire : le texte est vide")

    attempts: List[str] = []
    key = mistral_key()
    if key:
        try:
            wav = voxtral.speech(body, key, voice=voice, reference_wav=reference_wav)
            path = save_clip(session_id, wav, ".wav")
            return {"engine": "voxtral", **reference(path, engine="voxtral")}
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"voxtral : {type(exc).__name__}: {exc}")
    else:
        attempts.append(f"voxtral : {voxtral.KEY_ENV} absente")

    if local.xtts_python() is not None:
        try:
            wav = local.xtts_speak(body, reference_wav)
            path = save_clip(session_id, wav, ".wav")
            return {"engine": "xtts", **reference(path, engine="xtts")}
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"xtts : {type(exc).__name__}: {exc}")
    else:
        attempts.append("xtts : ~/.uhb/xtts absent")

    if local.piper() is not None:
        try:
            wav = local.piper_speak(body)
            path = save_clip(session_id, wav, ".wav")
            return {"engine": "piper", **reference(path, engine="piper")}
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"piper : {type(exc).__name__}: {exc}")
    else:
        attempts.append("piper : binaire absent")

    raise AudioUnavailable(
        "aucun moteur de voix disponible : " + " | ".join(attempts)
    )


def to_flac24(source: Path, target: Optional[Path] = None) -> Path:
    """Lossless 24-bit stereo, the retention format (P1-13).

    Kept as a function even though nothing calls it yet: the memory lot will, and the
    format decision is already written in the roadmap. ffmpeg absent is a refusal
    with a sentence, not a silent 16-bit fallback.
    """
    binary = ffmpeg_path()
    if not binary:
        raise AudioUnavailable("ffmpeg absent : conversion FLAC impossible (aucun repli 16 bits)")
    out = target or source.with_suffix(".flac")
    proc = subprocess.run(
        [binary, "-y", "-i", str(source), "-c:a", "flac", "-sample_fmt", "s32",
         "-ar", "48000", "-ac", "2", str(out)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if proc.returncode != 0 or not out.is_file():
        raise AudioUnavailable(f"ffmpeg a refusé la conversion : {proc.stderr[-200:]}")
    return out


__all__ = [
    "AUDIO_SUFFIXES",
    "AudioUnavailable",
    "MISTRAL_KEY_ENV",
    "SAMPLE_RATE",
    "MAX_CLIP_BYTES",
    "capabilities",
    "ffmpeg_path",
    "mistral_key",
    "reference",
    "resolve_upload",
    "save_clip",
    "seconds_of",
    "speak",
    "stt_chain",
    "to_flac24",
    "transcribe",
    "tts_chain",
    "uploads_dir",
]

MISTRAL_KEY_ENV = voxtral.KEY_ENV
