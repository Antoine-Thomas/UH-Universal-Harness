"""The local engines: XTTS-v2 in its own venv, Piper on CPU, whisper for dictation.

Each one is looked for at a fixed, documented place, and its absence is a *reason*
the page can show - never an exception that reaches the chat:

* XTTS-v2: ``~/.uhb/xtts/Scripts/python.exe`` (same pattern as the atelier venv),
  invoked as a subprocess so its torch never has to coexist with UH's packages;
* Piper: ``~/.uh/bin/piper.exe`` plus a voice under ``~/.uh/bin/voices/`` - the
  licence-clean floor (MIT, CPU, deterministic), and it does not clone;
* faster-whisper: imported in-process when installed in UH's own venv (CPU int8,
  the setting that is known to work on this machine; GPU needs cuBLAS 12 and fails).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

#: Where the installer is expected to put things (roadmap, "Installation unique").
XTTS_VENV = Path.home() / ".uhb" / "xtts"
PIPER_DIR = Path(os.environ.get("UH_HOME") or (Path.home() / ".uh")) / "bin"
#: Existing installs this machine already owns, in the order they are tried after
#: the canonical one. Reusing a provisioned XTTS beats downloading 1.8 GB again.
XTTS_CANDIDATES = (
    Path.home() / "xtts2_api" / ".pixi" / "envs" / "default" / "python.exe",
)
XTTS_MODEL_HINTS = (
    Path.home() / ".uhb" / "xtts" / "models" / "XTTS_2.0.2",
    Path.home() / "xtts2_api" / "models" / "XTTS_2.0.2",
)

_XTTS_SCRIPT = """
import os
import sys
from pathlib import Path
import torch
from TTS.api import TTS

text = Path(sys.argv[1]).read_text(encoding="utf-8")
out = sys.argv[2]
reference = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
model_dir = os.environ.get("UH_XTTS_MODEL_DIR") or ""
if model_dir and (Path(model_dir) / "model.pth").is_file():
    tts = TTS(model_path=str(Path(model_dir) / "model.pth"),
              config_path=str(Path(model_dir) / "config.json"))
else:
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
tts.to("cuda" if torch.cuda.is_available() else "cpu")
kwargs = dict(language="fr", file_path=out, temperature=0.75, speed=1.0)
if reference:
    kwargs["speaker_wav"] = reference
else:
    kwargs["speaker"] = tts.speakers[0]
tts.tts_to_file(text=text, **kwargs)
print(out)
"""


def xtts_python() -> Optional[str]:
    """The interpreter that can run XTTS-v2: explicit override, canonical venv, then
    an install this machine already has. ``None`` means "no local clone engine"."""
    override = str(os.environ.get("UH_XTTS_PYTHON") or "").strip()
    if override and Path(override).is_file():
        return override
    for name in ("python.exe", "python"):
        candidate = XTTS_VENV / ("Scripts" if os.name == "nt" else "bin") / name
        if candidate.is_file():
            return str(candidate)
    for candidate in XTTS_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None


def xtts_model_dir() -> Optional[str]:
    override = str(os.environ.get("UH_XTTS_MODEL_DIR") or "").strip()
    if override and (Path(override) / "model.pth").is_file():
        return override
    for candidate in XTTS_MODEL_HINTS:
        if (candidate / "model.pth").is_file():
            return str(candidate)
    return None


def xtts_speak(text: str, reference_wav: Optional[Path] = None) -> bytes:
    """One WAV out of the XTTS-v2 venv, or an exception the chain catches."""
    python = xtts_python()
    if python is None:
        raise RuntimeError("~/.uhb/xtts absent")
    with tempfile.TemporaryDirectory(prefix="uh-xtts-") as folder:
        script = Path(folder) / "speak.py"
        script.write_text(_XTTS_SCRIPT, encoding="utf-8")
        text_file = Path(folder) / "text.txt"
        text_file.write_text(text, encoding="utf-8")
        out = Path(folder) / "out.wav"
        args = [python, str(script), str(text_file), str(out)]
        if reference_wav is not None and Path(reference_wav).is_file():
            args.append(str(reference_wav))
        env = {**os.environ, "PYTHONPATH": "", "COQUI_TOS_AGREED": "1"}
        model_dir = xtts_model_dir()
        if model_dir:
            env["UH_XTTS_MODEL_DIR"] = model_dir
        proc = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", timeout=900, check=False, env=env,
        )
        if proc.returncode != 0 or not out.is_file():
            raise RuntimeError(f"xtts a échoué : {(proc.stderr or proc.stdout)[-300:]}")
        return out.read_bytes()


def piper() -> Optional[str]:
    for name in ("piper.exe", "piper"):
        candidate = PIPER_DIR / name
        if candidate.is_file():
            return str(candidate)
    return None


def piper_voice() -> Optional[Path]:
    folder = PIPER_DIR / "voices"
    if not folder.is_dir():
        return None
    for candidate in sorted(folder.glob("*.onnx")):
        return candidate
    return None


def piper_speak(text: str) -> bytes:
    binary = piper()
    voice = piper_voice()
    if binary is None or voice is None:
        raise RuntimeError("piper ou sa voix manque dans ~/.uh/bin")
    with tempfile.TemporaryDirectory(prefix="uh-piper-") as folder:
        out = Path(folder) / "out.wav"
        proc = subprocess.run(
            [binary, "--model", str(voice), "--output_file", str(out)],
            input=text, capture_output=True, text=True, encoding="utf-8",
            timeout=300, check=False,
        )
        if proc.returncode != 0 or not out.is_file():
            raise RuntimeError(f"piper a échoué : {(proc.stderr or '')[-300:]}")
        return out.read_bytes()


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:  # noqa: BLE001 - an absent optional dependency is a fact, not an error
        return False
    return True


_MODEL = None  # loaded once: a dictation is short, reloading costs more than it saved


def whisper_transcribe(clip: Path, *, language: str = "fr") -> str:
    global _MODEL
    from faster_whisper import WhisperModel  # imported here: optional dependency

    if _MODEL is None:
        _MODEL = WhisperModel(
            str(os.environ.get("UH_WHISPER_MODEL") or "base"),
            device="cpu",
            compute_type="int8",
        )
    segments, _info = _MODEL.transcribe(str(clip), language=language or None, vad_filter=True)
    return " ".join(segment.text.strip() for segment in segments).strip()


__all__ = [
    "PIPER_DIR",
    "XTTS_VENV",
    "piper",
    "piper_speak",
    "piper_voice",
    "whisper_available",
    "whisper_transcribe",
    "xtts_python",
    "xtts_speak",
]
