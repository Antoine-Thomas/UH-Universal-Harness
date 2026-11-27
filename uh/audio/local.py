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
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

#: Where the installer is expected to put things (roadmap, "Installation unique").
XTTS_VENV = Path.home() / ".uhb" / "xtts"
PIPER_DIR = Path(os.environ.get("UH_HOME") or (Path.home() / ".uh")) / "bin"
#: Existing installs this machine already owns, in the order they are tried after
#: the canonical one. Reusing a provisioned XTTS beats downloading 1.8 GB again.
XTTS_CANDIDATES = (Path.home() / "xtts2_api" / ".pixi" / "envs" / "default" / "python.exe",)
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


#: Délai d'un rendu XTTS, **calculé depuis les mesures** (27/11, RTX 3070 Ti) et non
#: deviné : ~25 s de chargement du modèle, puis ~35 ms par caractère. Les 900 s
#: d'avant bloquaient un quart d'heure sur un texte ordinaire sans jamais dire où
#: le temps passait. Plafond explicite : au-delà, on préfère un échec nommé.
XTTS_LOAD_SECONDS = 60.0
XTTS_SECONDS_PER_CHAR = 0.06
XTTS_TIMEOUT_CAP = 300.0


def speech_timeout(text: str) -> float:
    """Le délai d'un rendu, en secondes : 60 s + 0,06 s par caractère, plafonné."""
    return min(XTTS_TIMEOUT_CAP, XTTS_LOAD_SECONDS + XTTS_SECONDS_PER_CHAR * len(text or ""))


#: Pic mesuré de XTTS-v2 sur cette machine (27/11) et la marge qu'on ne franchit pas :
#: en dessous, le modèle charge puis se fait déplacer en RAM, ce qui prend des
#: minutes au lieu de secondes.
XTTS_PEAK_VRAM_GB = 4.2
XTTS_VRAM_MARGIN_GB = 0.8


def free_vram_gb() -> Optional[float]:
    """La VRAM libre en Go, mesurée par ``nvidia-smi`` ; ``None`` si elle ne se mesure pas."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    values = []
    for line in out.splitlines():
        value = line.strip().split(" ")[0]
        try:
            values.append(float(value) / 1024.0)
        except ValueError:
            continue
    return max(values) if values else None


def heavy_engine_fits(
    free_gb: Optional[float],
    *,
    needed: float = XTTS_PEAK_VRAM_GB,
    margin: float = XTTS_VRAM_MARGIN_GB,
) -> bool:
    """Le moteur lourd tient-il, marge comprise ? Une VRAM non mesurée ne tient pas."""
    return free_gb is not None and free_gb >= needed + margin


def heavy_engine_refusal(
    free_gb: Optional[float],
    *,
    needed: float = XTTS_PEAK_VRAM_GB,
    margin: float = XTTS_VRAM_MARGIN_GB,
) -> str:
    """La phrase française qui dit pourquoi le moteur lourd n'est pas proposé."""
    if free_gb is None:
        return (
            "voix locale lourde indisponible : VRAM libre non mesurable — "
            "la voix du navigateur est utilisée"
        )
    return (
        f"voix locale lourde indisponible : {free_gb:.1f} Go de VRAM libre, "
        f"{needed:.1f} Go nécessaires plus {margin:.1f} Go de marge — "
        "la voix du navigateur est utilisée"
    )


def kill_tree(pid: int) -> None:
    """Tue le processus **et ses enfants** : un moteur orphelin garde la VRAM.

    Sous Windows, `taskkill /T` est la seule façon d'emporter l'arbre : tuer le
    parent laisse le worker torch vivant, ce qui a été mesuré (un plantage du
    harnais de mesure a laissé un runner et son enfant XTTS en vie).
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=60, check=False
        )
        return
    killer = getattr(os, "killpg", None)
    getter = getattr(os, "getpgid", None)
    if killer is None or getter is None:  # pragma: no cover - Windows only
        return
    try:
        killer(getter(pid), 9)
    except (ProcessLookupError, PermissionError):
        pass


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
        limit = speech_timeout(text)
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=limit,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"xtts a dépassé {limit:.0f} s (mesure : 60 s de chargement + 0,06 s "
                f"par caractère, plafond {XTTS_TIMEOUT_CAP:.0f} s) ; texte de "
                f"{len(text)} caractères"
            ) from None
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
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
            check=False,
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
_MODEL_KEY = None


def whisper_device() -> tuple[str, str]:
    """Sur quoi transcrire, décidé par la mesure — pas par l'espoir.

    Le GPU ne sert que si ses trois DLL se chargent, qu'un banc l'a trouvé
    nettement plus rapide (``voice-bench.json``) **et** que la VRAM libre tient au
    moment de dicter (``MIN_FREE_VRAM_MB``). Cette dernière mesure est relue à
    **chaque** appel : un chat qui vient de charger la carte rebascule la dictée sur
    CPU sans redémarrer le serveur. Une mesure absente ou en erreur vaut refus.
    """
    from .. import voice_gpu

    try:
        bench = voice_gpu.read_bench() or {}
        if voice_gpu.cuda_available() and bench.get("gpu_wins"):
            if voice_gpu.vram_allows_dictation(voice_gpu.free_vram_mb()):
                # Les DLL d'un wheel vivent dans le venv : Windows ne les trouve qu'une
                # fois leurs dossiers déclarés, et c'est **avant** de charger le modèle.
                voice_gpu.add_dll_directories()
                return "cuda", "int8_float16"
    except Exception:  # noqa: BLE001 - une sonde qui échoue ne doit pas casser la dictée
        pass
    return "cpu", "int8"


def whisper_transcribe(clip: Path, *, language: str = "fr") -> str:
    global _MODEL, _MODEL_KEY
    from faster_whisper import WhisperModel  # imported here: optional dependency

    device, compute = whisper_device()
    key = (device, compute)
    if _MODEL is None or _MODEL_KEY != key:
        _MODEL = WhisperModel(
            str(os.environ.get("UH_WHISPER_MODEL") or "base"),
            device=device,
            compute_type=compute,
        )
        _MODEL_KEY = key
    segments, _info = _MODEL.transcribe(str(clip), language=language or None, vad_filter=True)
    return " ".join(segment.text.strip() for segment in segments).strip()


__all__ = [
    "PIPER_DIR",
    "whisper_device",
    "XTTS_VENV",
    "piper",
    "piper_speak",
    "piper_voice",
    "whisper_available",
    "whisper_transcribe",
    "xtts_python",
    "xtts_speak",
]
