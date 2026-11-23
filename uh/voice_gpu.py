"""La dictée sur GPU : ce qu'il faut, ce que ça coûte, et la preuve que ça marche.

Trois faits mesurés sur le poste de référence commandent ce module :

1. un GPU visible ne suffit pas — faster-whisper exige ``cublas64_12.dll``,
   ``cublasLt64_12.dll`` et ``cudnn64_9.dll``, et **aucune** n'est présente après
   une installation normale ; ``ctranslate2`` lève alors au chargement du modèle ;
2. les paquets NVIDIA de PyPI publient bien des wheels Windows — mais pas à toutes
   les versions (``nvidia-cublas-cu12==12.9.3.13`` n'en a plus) : on épingle donc
   deux versions dont le wheel Windows existe, vérifié sur l'index ;
3. installer les DLL ne suffit pas encore : il faut les **ajouter au chemin de
   chargement** du processus, sinon Windows ne les trouve pas. C'est le rôle de
   ``add_dll_directories()`` et c'est ce qui rend la vérification honnête.

Rien ici n'installe ni ne télécharge quoi que ce soit : ``install_command()`` rend la
commande, l'utilisateur clique, et ``verify()`` dit ensuite si CUDA se charge
vraiment. Un échec n'est pas une exception : c'est une réponse avec sa raison.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Les trois DLL que faster-whisper exige côté Windows pour parler à CUDA.
GPU_LIBS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")

#: Paquets NVIDIA **officiels** de PyPI, épinglés : ce sont les derniers qui
#: publient un wheel ``win_amd64`` (cuBLAS 12.9.3.13 n'en publie plus).
GPU_PACKAGES = ("nvidia-cublas-cu12==12.9.2.10", "nvidia-cudnn-cu12==9.28.1.4")

#: Option secondaire, jamais proposée par défaut : binaires tiers.
GPU_THIRD_PARTY = {
    "label": "archive « libs » de whisper-standalone-win (Purfview)",
    "note": "binaire tiers, à vérifier avant usage",
    "url": "https://github.com/Purfview/whisper-standalone-win/releases",
}

#: Le banc CPU/GPU écrit son verdict ici, et le plan le relit à chaque ouverture.
BENCH_NAME = "voice-bench.json"

#: Une mesure n'a de valeur que si le GPU gagne nettement : en dessous, on garde CPU.
GPU_MUST_WIN_BY = 0.75

_WHEEL_SIZES: Dict[str, str] = {}


def home() -> Path:
    """Le ``UH_HOME`` effectif, sans créer quoi que ce soit."""
    return Path(os.environ.get("UH_HOME") or (Path.home() / ".uh"))


def libs() -> Dict[str, bool]:
    """Chaque DLL, **chargée pour de vrai** : un fichier présent ne prouve rien."""
    import ctypes

    # ``ctypes.WinDLL`` n'existe que sous Windows : typeshed ne le déclare pas
    # ailleurs, et mypy sur Linux refuse l'accès direct (mesuré en CI le 23/11 :
    # `Module has no attribute "WinDLL" [attr-defined]`, qui rendait #50 à #55
    # rouges). Les clés restent celles du catalogue, toutes à False hors Windows.
    windll = getattr(ctypes, "WinDLL", None)
    if windll is None:
        return {name: False for name in GPU_LIBS}
    found: Dict[str, bool] = {}
    for name in GPU_LIBS:
        try:
            windll(name)
            found[name] = True
        except (OSError, AttributeError):
            found[name] = False
    return found


def cuda_available() -> bool:
    """Vrai seulement si les trois DLL se chargent **après** avoir déclaré leurs dossiers.

    Windows ne voit pas les DLL d'un wheel installé : il faut ``os.add_dll_directory``
    sur ``nvidia/cublas/bin`` et ``nvidia/cudnn/bin``. Sans cet appel, une machine
    correctement équipée répondrait « CUDA absent » — mesuré sur ce poste.
    """
    try:
        add_dll_directories()
        return all(libs().values())
    except Exception:  # pragma: no cover - ctypes absent (POSIX)
        return False


def add_dll_directories() -> List[str]:
    """Ajoute les dossiers ``bin`` des wheels NVIDIA au chemin de chargement.

    Sur Windows, un paquet installé ne rend pas ses DLL visibles : il faut
    ``os.add_dll_directory``. On ne touche ni au ``PATH`` de la machine ni au
    Python système — seulement à ce processus.
    """
    added: List[str] = []
    add = getattr(os, "add_dll_directory", None)
    if add is None:
        return added
    roots = []
    try:
        import site

        roots.extend(site.getsitepackages())
    except Exception:  # pragma: no cover - environnement inhabituel
        pass
    if sys.prefix not in roots:
        roots.append(str(Path(sys.prefix) / "Lib" / "site-packages"))
    for root in roots:
        base = Path(root) / "nvidia"
        if not base.is_dir():
            continue
        for candidate in sorted(base.glob("*/bin")):
            try:
                add(str(candidate))
                added.append(str(candidate))
            except OSError:
                continue
    return added


def driver_version() -> str:
    """La version du pilote NVIDIA, ou une chaîne vide si ``nvidia-smi`` est absent."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in (result.stdout or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def wheel_size(package: str, version: str) -> str:
    """Taille du wheel Windows lue sur l'index PyPI ; vide si le réseau ne répond pas.

    Afficher une taille vaut mieux que ne rien dire ; l'annoncer fausse serait pire,
    donc l'échec rend une chaîne vide et l'appelant écrit « taille inconnue ».
    """
    key = f"{package}=={version}"
    if key in _WHEEL_SIZES:
        return _WHEEL_SIZES[key]
    text = ""
    try:
        import urllib.request

        url = f"https://pypi.org/pypi/{package}/{version}/json"
        with urllib.request.urlopen(url, timeout=4) as response:  # noqa: S310 - index public fixe
            data = json.load(response)
        wins = [
            item for item in data.get("urls", []) if "win_amd64" in str(item.get("filename") or "")
        ]
        if wins:
            text = f"{wins[0]['size'] / 1e6:.0f} Mo"
    except Exception:
        text = ""
    _WHEEL_SIZES[key] = text
    return text


def install_command() -> str:
    """La commande exacte, avec l'interpréteur qui fait tourner le serveur."""
    return f'"{sys.executable}" -m pip install --no-deps ' + " ".join(GPU_PACKAGES)


def setup(machine: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """L'étape « Activer la dictée sur GPU », ou ``None`` quand elle n'a pas lieu d'être.

    Elle n'apparaît que si une carte est visible **et** qu'au moins une des trois
    DLL manque : sinon il n'y a rien à proposer.
    """
    machine = machine or {}
    if not machine.get("gpu"):
        return None
    found = libs()
    if all(found.values()):
        return None
    sizes = [wheel_size(*pkg.split("==")) for pkg in GPU_PACKAGES]
    download = " + ".join(size for size in sizes if size) or "taille inconnue (index injoignable)"
    free = ""
    try:
        import shutil

        free = f"{shutil.disk_usage(home()).free / 1e9:.0f} Go libres sur le disque de UH"
    except OSError:
        free = "espace libre inconnu"
    return {
        "show": True,
        "label": "Activer la dictée sur GPU",
        "missing": [name for name, ok in found.items() if not ok],
        "driver": driver_version() or "inconnu",
        "source": "paquets NVIDIA officiels de PyPI (aucun binaire tiers)",
        "packages": list(GPU_PACKAGES),
        "download": download,
        "disk": f"{free} ; environ 3,5 Go seront pris par l'installation",
        "command": install_command(),
        "verify": (
            "après l'installation, UH ajoute les DLL au chemin de chargement, puis vérifie "
            "par un chargement réel (ctranslate2.get_cuda_device_count()) et une transcription "
            "courte ; si l'un des deux échoue, la dictée reste sur CPU et le panneau dit pourquoi"
        ),
        "third_party": GPU_THIRD_PARTY,
    }


_VERIFY_SCRIPT = r"""
import json, sys, wave, tempfile, os

sys.path.insert(0, r"%(repo)s")
report = {"steps": [], "cuda": False}

def step(name, ok, detail=""):
    report["steps"].append({"name": name, "ok": bool(ok), "detail": detail})
    return ok

from uh import voice_gpu
dirs = voice_gpu.add_dll_directories()
step("chemin de chargement", bool(dirs), ", ".join(dirs[-2:]) if dirs else "aucun dossier nvidia/*/bin")

found = voice_gpu.libs()
step("DLL NVIDIA", all(found.values()), ", ".join(n for n, ok in found.items() if not ok) or "les trois se chargent")

try:
    import ctranslate2
    count = ctranslate2.get_cuda_device_count()
except Exception as exc:
    step("ctranslate2", False, "%%s: %%s" %% (type(exc).__name__, exc))
    count = 0
else:
    step("ctranslate2", count > 0, "%%d carte(s) CUDA visible(s)" %% count)
report["cuda"] = count > 0

if count:
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        step("transcription", False, "faster-whisper indisponible: %%s" %% exc)
    else:
        import time as _t
        path = os.path.join(tempfile.gettempdir(), "uh-cuda-probe.wav")
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 16000)
        try:
            started = _t.perf_counter()
            model = WhisperModel("base", device="cuda", compute_type="int8_float16")
            list(model.transcribe(path, beam_size=1)[0])
            step("transcription", True, "%%.1f s sur GPU" %% (_t.perf_counter() - started))
        except Exception as exc:
            step("transcription", False, "%%s: %%s" %% (type(exc).__name__, exc))

print(json.dumps(report))
"""


def verify(timeout: int = 600) -> Dict[str, Any]:
    """Charge CUDA pour de vrai, dans un sous-processus.

    Les DLL se chargent une fois par processus : le serveur qui a déjà échoué ne peut
    pas se rattraper sans redémarrer. Le sous-processus, lui, part propre.
    """
    repo = str(Path(__file__).resolve().parents[1])
    script = _VERIFY_SCRIPT % {"repo": repo}
    before = libs()
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "cuda": False,
            "steps": [
                {"name": "vérification", "ok": False, "detail": f"{type(exc).__name__}: {exc}"}
            ],
        }
    payload: Dict[str, Any] = {}
    for line in reversed((result.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
            except ValueError:
                payload = {}
            break
    steps = payload.get("steps") or [
        {
            "name": "vérification",
            "ok": False,
            "detail": ((result.stderr or "").strip().splitlines() or ["sortie vide"])[-1][:300],
        }
    ]
    return {
        "ok": all(step.get("ok") for step in steps),
        "cuda": bool(payload.get("cuda")),
        "steps": steps,
        "before": before,
    }


def read_bench() -> Optional[Dict[str, Any]]:
    """Le dernier verdict de la mesure CPU/GPU, ou ``None``."""
    try:
        data = json.loads((home() / BENCH_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_bench(payload: Dict[str, Any]) -> None:
    path = home() / BENCH_NAME
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def vram_used_mb() -> Optional[int]:
    """La VRAM utilisée, lue par ``nvidia-smi`` ; ``None`` si indisponible."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (result.stdout or "").splitlines():
        try:
            return int(line.strip())
        except ValueError:
            continue
    return None


def sample(seconds: float = 10.0) -> tuple[Optional[Path], str]:
    """Un échantillon de 10 s : parole locale si possible, sinon un signal muet.

    Le second cas est annoncé comme tel : il mesure le débit, pas la qualité.
    """
    target = home() / "voice-bench-10s.wav"
    try:
        from . import audio as uh_audio

        wav = uh_audio.speak(
            "La dictée locale doit être rapide, fidèle, et rester sur cet ordinateur.",
            engine="xtts",
        )
        wav_path = Path(str(wav)) if wav else None
        if wav_path is not None and wav_path.exists():
            return wav_path, "parole synthétisée localement (XTTS)"
    except Exception:
        pass
    import math
    import struct
    import wave

    frames = int(16000 * seconds)
    try:
        with wave.open(str(target), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            data = b"".join(
                struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * i / 16000)))
                for i in range(frames)
            )
            wav_file.writeframes(data)
    except OSError:
        return None, "échantillon impossible à écrire"
    return target, f"{seconds:.0f} s de tonalité : mesure de débit, pas de qualité"


def bench(sample_path: Optional[Path] = None) -> Dict[str, Any]:
    """Mesure CPU ``base/int8`` contre GPU ``base/int8_float16`` sur le même échantillon.

    Le GPU ne gagne que s'il va **nettement** plus vite et s'il ne coûte pas la place
    du modèle de chat : la VRAM libre avant/après est relevée, jamais devinée.
    """
    import time

    path, origin = (sample_path, "échantillon fourni") if sample_path else sample()
    if path is None:
        return {"ok": False, "reason": origin, "origin": origin}
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        return {"ok": False, "reason": f"faster-whisper indisponible : {exc}", "origin": origin}

    results: Dict[str, Any] = {"origin": origin, "sample": str(path)}
    timings: Dict[str, float] = {}
    for label, device, compute in (
        ("cpu", "cpu", "int8"),
        ("gpu", "cuda", "int8_float16"),
    ):
        if device == "cuda":
            if not cuda_available():
                results["gpu"] = {"ok": False, "reason": "CUDA ne se charge pas : DLL absentes"}
                continue
            add_dll_directories()
        before = vram_used_mb() if device == "cuda" else None
        try:
            started = time.perf_counter()
            model = WhisperModel("base", device=device, compute_type=compute)
            loaded = time.perf_counter()
            list(model.transcribe(str(path), beam_size=1)[0])
            finished = time.perf_counter()
        except Exception as exc:
            results[label] = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            continue
        after = vram_used_mb() if device == "cuda" else None
        timings[label] = finished - started
        results[label] = {
            "ok": True,
            "load_s": round(loaded - started, 2),
            "audio_s": round(finished - loaded, 2),
            "total_s": round(finished - started, 2),
            "vram_used_mb": after,
            "vram_delta_mb": (after - before)
            if (after is not None and before is not None)
            else None,
        }
    cpu = timings.get("cpu")
    gpu = timings.get("gpu")
    gpu_wins = bool(cpu and gpu and gpu <= cpu * GPU_MUST_WIN_BY)
    results["gpu_wins"] = gpu_wins
    if not cpu:
        results["ok"] = False
    elif not gpu:
        results["ok"] = False
        results["reason"] = "GPU non mesuré : la dictée reste sur CPU"
    elif not gpu_wins:
        results["reason"] = (
            f"GPU {gpu:.1f} s contre CPU {cpu:.1f} s : le gain n'est pas net, la dictée reste sur CPU"
        )
    else:
        results["reason"] = f"GPU {gpu:.1f} s contre CPU {cpu:.1f} s : le GPU gagne"
    results.setdefault("ok", True)
    write_bench(results)
    return results
