"""Quel moteur vocal, et pourquoi — une décision pure, testable sans rien installer.

Le terrain du 23/11 : la dictée échoue (Voxtral 429, faster-whisper absent) et rien
dans l'interface ne dit quel moteur devrait servir, ni ce qui manque. Ce module répond
à trois questions, dans cet ordre :

1. **quel moteur** pour la dictée (STT) et pour la voix (TTS), selon le profil choisi
   (`local`, `cloud`, `auto`), les clés réellement saisies et les versions installées ;
2. **pourquoi** — chaque choix porte une phrase, et un refus porte la sienne ;
3. **ce qui manque** — la commande exacte à afficher, jamais exécutée ici.

Règles tenues par les tests, pas par la bonne volonté :

* une clé Voxtral absente ou vide **n'autorise aucun appel réseau** : le moteur cloud
  n'est même pas candidat ;
* en profil `local`, Voxtral n'est jamais choisi, même avec une clé valide ;
* sans aucun moteur, le plan dit `None` **et** donne la commande d'installation ;
* la taille du modèle local se déduit de la machine mesurée, VRAM du modèle de chat
  déduite — jamais un modèle codé en dur qui ne tiendrait pas.

Aucun import réseau, aucun sous-processus dans `choose_voice_engines` : c'est une
fonction pure, et c'est ce qui permet de la tester sur des cas que la machine ne
produit pas.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

#: Profils proposés dans le panneau Voix.
PROFILES = ("auto", "local", "cloud")

#: Sous ce seuil, une clé n'est pas une clé : c'est un reste de collage.
MIN_KEY_CHARS = 16

#: Ce que chaque moteur local coûte, en Go de VRAM, quand on le fait tourner sur GPU.
#: Valeurs rondes, volontairement pessimistes : mieux vaut un modèle plus petit qui
#: tourne qu'un modèle qui déborde.
CHAT_VRAM_GB = {"small": 1.0, "medium": 1.5, "large": 2.5, "xl": 5.0, "remote": 0.0}

#: Paliers du modèle de dictée local, du plus petit au plus grand.
STT_STEPS = ("tiny", "base", "small", "medium")


def _key_present(keys: Dict[str, Any]) -> bool:
    value = str((keys or {}).get("voxtral") or "").strip()
    return len(value) >= MIN_KEY_CHARS


def chat_vram_gb(machine: Dict[str, Any]) -> float:
    """Ce que le modèle de chat occupe déjà sur la carte, en Go."""
    tier = str((machine or {}).get("tier") or "")
    if not machine or not machine.get("gpu"):
        return 0.0
    return CHAT_VRAM_GB.get(tier, 0.0)


def free_vram_gb(machine: Dict[str, Any]) -> float:
    """VRAM restante pour la dictée, une fois le chat chargé."""
    vram = float((machine or {}).get("vram_gb") or 0.0)
    return max(0.0, vram - chat_vram_gb(machine))


def local_stt_model(machine: Optional[Dict[str, Any]], cuda: bool = False) -> Tuple[str, str]:
    """Le plus gros modèle de dictée qui tient, et sur quoi il tourne.

    ``cuda`` n'est vrai que si les bibliothèques CUDA se chargent réellement : un GPU
    visible sans ``cublas64_12.dll`` fait échouer faster-whisper sur carte (mesuré ici),
    donc on préfère le CPU à un modèle qui ne tournera pas.

    Un GPU avec de la VRAM libre après le chat fait tourner `small`/`medium` en
    `int8_float16` ; sinon on reste sur CPU en `int8`, plus lent mais sûr. Le modèle
    n'est jamais plus gros que ce que la machine peut charger : c'est la règle du
    dépôt (paliers mesurés, pas de plafond inventé).
    """
    machine = machine or {}
    free = free_vram_gb(machine)
    if cuda and machine.get("gpu") and free >= 5.0:
        return "medium", "int8_float16"
    if cuda and machine.get("gpu") and free >= 2.5:
        return "small", "int8_float16"
    ram = float(machine.get("ram_gb") or 0.0)
    if ram >= 16.0:
        return "base", "int8"
    return "tiny", "int8"


def _install_stt_command() -> str:
    """La commande exacte, celle que l'utilisateur peut copier telle quelle."""
    return f'"{sys.executable}" -m pip install "faster-whisper>=1.2" "huggingface-hub<1" "filelock<3.17"'


def choose_voice_engines(
    profile: str,
    keys: Dict[str, Any],
    versions: Dict[str, Any],
    machine: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Le plan vocal complet : moteurs, raisons, réseau autorisé, ce qui manque.

    ``profile`` : ``local`` (rien ne sort), ``cloud`` (cloud d'abord) ou ``auto``
    (local d'abord, cloud en secours). ``keys`` ne contient jamais qu'un booléen
    déguisé : la valeur brute n'est utilisée que pour sa longueur, et elle n'est
    jamais renvoyée dans le plan.
    """
    profile = profile if profile in PROFILES else "auto"
    versions = versions or {}
    machine = machine or {}
    has_key = _key_present(keys)
    stt_installed = bool(versions.get("faster_whisper"))
    xtts = bool(versions.get("xtts"))
    piper = bool(versions.get("piper"))

    install: List[Dict[str, str]] = []
    if not stt_installed:
        install.append(
            {
                "target": "faster-whisper",
                "label": "Installer la dictée locale (faster-whisper)",
                "command": _install_stt_command(),
                "why": "sans lui, la dictée dépend d'un service cloud et de son quota",
            }
        )

    model, compute = local_stt_model(machine, bool(versions.get("cuda")))

    def local_stt() -> Dict[str, Any]:
        return {
            "engine": "faster-whisper",
            "kind": "local",
            "label": "faster-whisper (local)",
            "model": model,
            "compute": compute,
            "reason": (
                f"modèle {model} sur GPU, {compute}"
                if compute.startswith("int8_float16")
                else f"modèle {model} sur CPU, {compute}"
                + (" (CUDA non chargeable sur ce poste)" if machine.get("gpu") else "")
            ),
        }

    def cloud_stt() -> Dict[str, Any]:
        return {
            "engine": "voxtral",
            "kind": "cloud",
            "label": "Voxtral 3 (Mistral)",
            "model": "",
            "compute": "",
            "reason": "clé Voxtral valide saisie",
        }

    def none_stt(reason: str) -> Dict[str, Any]:
        return {
            "engine": None,
            "kind": None,
            "label": "aucun",
            "model": "",
            "compute": "",
            "reason": reason,
        }

    # --- dictée -----------------------------------------------------------------
    if profile == "local":
        stt = (
            local_stt()
            if stt_installed
            else none_stt("IA locale seulement : faster-whisper n'est pas installé")
        )
    elif profile == "cloud":
        if has_key:
            stt = cloud_stt()
        elif stt_installed:
            stt = local_stt()
            stt["reason"] = "aucune clé Voxtral : dictée locale"
        else:
            stt = none_stt("aucune clé Voxtral et faster-whisper absent")
    else:  # auto : le local d'abord, c'est la règle du produit
        if stt_installed:
            stt = local_stt()
        elif has_key:
            stt = cloud_stt()
            stt["reason"] = "faster-whisper absent : dictée via Voxtral"
        else:
            stt = none_stt("ni faster-whisper local ni clé Voxtral")

    # --- voix -------------------------------------------------------------------
    def local_tts() -> Dict[str, Any]:
        if xtts:
            return {
                "engine": "xtts",
                "kind": "local",
                "label": "XTTS-v2 (local)",
                "clones": True,
                "reason": "XTTS-v2 provisionné : synthèse et clonage locaux",
            }
        return {
            "engine": "piper",
            "kind": "local",
            "label": "Piper (CPU)",
            "clones": False,
            "reason": "Piper installé : voix locale, sans clonage (annoncé comme telle)",
        }

    def cloud_tts() -> Dict[str, Any]:
        return {
            "engine": "voxtral",
            "kind": "cloud",
            "label": "Voxtral 3 (Mistral)",
            "clones": True,
            "reason": "clé Voxtral valide saisie",
        }

    def none_tts(reason: str) -> Dict[str, Any]:
        return {"engine": None, "kind": None, "label": "aucun", "clones": False, "reason": reason}

    local_voice_ready = xtts or piper
    if profile == "local":
        tts = (
            local_tts()
            if local_voice_ready
            else none_tts("IA locale seulement : ni XTTS-v2 ni Piper ne sont installés")
        )
    elif profile == "cloud":
        tts = (
            cloud_tts()
            if has_key
            else (
                local_tts()
                if local_voice_ready
                else none_tts("aucune clé Voxtral et aucune voix locale")
            )
        )
    else:
        tts = (
            local_tts()
            if local_voice_ready
            else (cloud_tts() if has_key else none_tts("ni voix locale ni clé Voxtral"))
        )

    if not local_voice_ready:
        install.append(
            {
                "target": "piper",
                "label": "Installer une voix locale (Piper)",
                "command": "voir docs/INSTALL.md — voix fr_FR + binaire piper dans ~/.uh/bin",
                "why": "sans voix locale, « Écouter » dépend d'un service cloud",
            }
        )

    # Le réseau n'est autorisé que si un moteur choisi est réellement cloud.
    network_allowed = any((part or {}).get("kind") == "cloud" for part in (stt, tts)) and has_key

    return {
        "profile": profile,
        "stt": stt,
        "tts": tts,
        "network_allowed": network_allowed,
        "install": install,
        "machine": {
            "tier": machine.get("tier") or "",
            "gpu": machine.get("gpu") or "",
            "vram_gb": float(machine.get("vram_gb") or 0.0),
            "free_vram_gb": round(free_vram_gb(machine), 1),
            "ram_gb": float(machine.get("ram_gb") or 0.0),
        },
    }


# --------------------------------------------------------------------------- #
# détection des versions (impure, mais sans effet de bord)
# --------------------------------------------------------------------------- #
def _run_version(command: List[str], timeout: float = 4.0) -> Optional[str]:
    """Première ligne d'une sortie de version, ou ``None``. Jamais d'exception."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout or "").strip() or (result.stderr or "").strip()
    if not text:
        return None
    return text.splitlines()[0].strip()[:80]


def _package_version(name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def cuda_available() -> bool:
    """Vrai seulement si une bibliothèque CUDA se charge vraiment.

    « Un GPU existe » n'est pas « faster-whisper peut s'en servir » : sans
    ``cublas64_12.dll`` il échoue au chargement du modèle. Le plan doit le savoir.
    """
    try:
        import ctypes

        ctypes.WinDLL("cublas64_12.dll")
        return True
    except (OSError, AttributeError):
        return False


def detect_versions() -> Dict[str, Any]:
    """Ce qui est installé, avec les commandes qui ont servi — pour le rapport.

    Aucun réseau : des binaires locaux et les métadonnées des paquets. Un ``None``
    veut dire « absent », et c'est une réponse, pas une erreur.
    """
    from . import audio as uh_audio
    from .web import browser as uh_browser

    versions: Dict[str, Any] = {
        "python": platform.python_version(),
        "executable": sys.executable,
        "uh": _package_version("uh-harness"),
        "uhb": _package_version("uhb-harness"),
        "ollama": _run_version(["ollama", "--version"]),
        "faster_whisper": _package_version("faster-whisper"),
        "xtts": bool(uh_audio.local.xtts_python()),
        "piper": bool(uh_audio.local.piper()),
        "ffmpeg": bool(uh_audio.ffmpeg_path()),
        "browser": str(uh_browser.find_chromium() or ""),
        "cuda": cuda_available(),
    }
    versions["browser_name"] = (
        "Edge"
        if "Edge" in versions["browser"]
        else ("Chrome" if "Chrome" in versions["browser"] else "")
    )
    return versions


def installation_report(versions: Dict[str, Any]) -> List[Dict[str, str]]:
    """La liste « Vérifier mon installation » : état, version, commande, jamais exécutée."""
    rows: List[Dict[str, str]] = []

    def add(name: str, state: str, version: str, command: str = "") -> None:
        rows.append({"name": name, "state": state, "version": version, "command": command})

    python = str(versions.get("python") or "")
    try:
        major, minor = (int(part) for part in python.split(".")[:2])
    except ValueError:
        major, minor = (0, 0)
    add(
        "Python",
        "ok" if (major, minor) >= (3, 10) else "update",
        python or "inconnu",
        "" if (major, minor) >= (3, 10) else "installer Python 3.10 ou plus récent",
    )
    add(
        "UH",
        "ok" if versions.get("uh") else "missing",
        str(versions.get("uh") or "absent"),
        "" if versions.get("uh") else f'"{sys.executable}" -m pip install -e ".[web]"',
    )
    add(
        "UHB",
        "ok" if versions.get("uhb") else "missing",
        str(versions.get("uhb") or "absent"),
        "" if versions.get("uhb") else "uhb install",
    )
    add(
        "Ollama",
        "ok" if versions.get("ollama") else "missing",
        str(versions.get("ollama") or "absent"),
        "" if versions.get("ollama") else "https://ollama.com/download (installation manuelle)",
    )
    add(
        "faster-whisper",
        "ok" if versions.get("faster_whisper") else "missing",
        str(versions.get("faster_whisper") or "absent"),
        "" if versions.get("faster_whisper") else _install_stt_command(),
    )
    add(
        "XTTS-v2",
        "ok" if versions.get("xtts") else "missing",
        "installé" if versions.get("xtts") else "absent",
        "" if versions.get("xtts") else "voir docs/INSTALL.md (voix locale)",
    )
    add(
        "Piper",
        "ok" if versions.get("piper") else "missing",
        "installé" if versions.get("piper") else "absent",
        "" if versions.get("piper") else "voir docs/INSTALL.md (voix locale)",
    )
    add(
        "ffmpeg",
        "ok" if versions.get("ffmpeg") else "missing",
        "trouvé" if versions.get("ffmpeg") else "absent",
        "" if versions.get("ffmpeg") else "winget install Gyan.FFmpeg",
    )
    add(
        versions.get("browser_name") or "Edge / Chrome",
        "ok" if versions.get("browser") else "missing",
        str(versions.get("browser") or "aucun Chromium trouvé"),
        "" if versions.get("browser") else "installer Microsoft Edge",
    )
    return rows


def plan_for_home(profile: str = "auto") -> Dict[str, Any]:
    """Le plan complet pour cette machine, clés lues sans jamais les renvoyer."""
    from . import audio as uh_audio
    from .hardware import detect as detect_hardware

    versions = detect_versions()
    hardware = detect_hardware()
    machine = {
        "tier": hardware.tier,
        "gpu": hardware.gpu or "",
        "ram_gb": hardware.ram_gb,
        "vram_gb": float(getattr(hardware, "vram_gb", 0.0) or 0.0),
    }
    keys = {"voxtral": uh_audio.mistral_key()}
    machine["cuda"] = bool(versions.get("cuda"))
    plan = choose_voice_engines(profile, keys, versions, machine)
    plan["versions"] = versions
    plan["installation"] = installation_report(versions)
    # L'interpréteur qui fait tourner CE serveur : les commandes d'installation le
    # nomment, sinon un `pip` pris au hasard dans le PATH installe ailleurs.
    plan["python_executable"] = sys.executable
    return plan


def dump(plan: Dict[str, Any]) -> str:
    """Pour les journaux : le plan sans aucune valeur secrète."""
    safe = json.loads(json.dumps(plan))
    safe.get("versions", {}).pop("voxtral_key", None)
    return json.dumps(safe, ensure_ascii=False, sort_keys=True)
