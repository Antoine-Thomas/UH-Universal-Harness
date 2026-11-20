"""Tâches planifiées internes — un registre, une liste blanche, deux mécanismes Windows.

Ce que ce module accepte de lancer est une **liste blanche**, pas une commande : le nom
d'une tâche est une étiquette, la commande est une CLÉ du catalogue ci-dessous, et l'argv
est construit ici. Aucune chaîne venue du web ne touche une ligne de commande, aucun shell
n'est invoqué, et il n'existe pas de champ « arguments ».

Deux mécanismes, dans cet ordre, comme `uh dream --schedule` :

* **Planificateur Windows** (`schtasks`) — survit à une déconnexion et à un redémarrage ;
  demande un droit que le compte standard n'a pas toujours : l'échec est attendu, pas une
  erreur, et il n'y a **aucune demande d'élévation** ;
* **Repli Startup** — un `.vbs` dans le dossier Démarrage, qui lance la tâche à
  l'ouverture de session puis attend l'intervalle avant de recommencer. Il ne tourne que
  pendant qu'une session est ouverte : c'est écrit dans l'interface, pas caché.

`UH_SCHEDULE_DISABLE_SYSTEM=1` tient le registre sans installer de déclencheur (utile
quand l'opérateur ne veut rien dans son planificateur) ; `UH_SCHEDULE_STARTUP_DIR` choisit
le dossier du repli, pour un profil non standard ou une vérification isolée.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import uh_home

REGISTRY = "schedules.json"
#: Bornes de cadence : sous 5 minutes on martèle la machine, au-delà d'une semaine la
#: tâche n'est plus une tâche. Les deux sont refusés explicitement.
MIN_EVERY = 5
MAX_EVERY = 7 * 24 * 60
NAME_LIMIT = 60
DETAIL_LIMIT = 400
RUN_TIMEOUT = 120.0
#: Marge ajoutée au budget de la passe de nuit : le budget compte le travail, pas le
#: démarrage de l'interpréteur ni l'écriture finale du journal.
DREAM_MARGIN = 120.0
VBS_PREFIX = "uh-schedule-"


class ScheduleError(RuntimeError):
    """Une demande refusée : rien n'a été installé, rien n'a été lancé."""


# --------------------------------------------------------------------------- #
# le catalogue : la seule source d'argv
# --------------------------------------------------------------------------- #
def _run_cli(arguments: List[str], timeout: float = RUN_TIMEOUT) -> Dict[str, Any]:
    """Lance ``python -m uh.cli <arguments>`` — argv fixé par le catalogue, jamais par l'UI."""
    command = [sys.executable, "-m", "uh.cli", *arguments]
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": f"délai dépassé ({timeout:.0f} s)"}
    except OSError as exc:
        return {"ok": False, "detail": f"lancement impossible : {type(exc).__name__}"}
    output = (done.stdout or "") + (done.stderr or "")
    tail = " ".join(output.split())[-DETAIL_LIMIT:]
    return {"ok": done.returncode == 0, "detail": tail or f"code {done.returncode}"}


def _run_prune() -> Dict[str, Any]:
    """Nettoyage des runs : la SIMULATION de UHB, jamais l'application.

    UH ne supprime pas de fichiers : une tâche planifiée qui effacerait des runs sans
    personne pour la regarder serait exactement l'action que ce projet refuse.
    """
    from . import uhb_runs

    try:
        report = uhb_runs.prune(apply=False)
    except Exception as exc:  # noqa: BLE001 - UHB arrêté est un statut, pas un crash
        return {"ok": False, "detail": f"UHB injoignable : {type(exc).__name__}"}
    if isinstance(report, dict) and report.get("error"):
        return {"ok": False, "detail": str(report["error"])[:DETAIL_LIMIT]}
    removable = report.get("removable") if isinstance(report, dict) else None
    count = len(removable) if isinstance(removable, list) else report.get("count")
    return {"ok": True, "detail": f"simulation : {count if count is not None else '?'} run(s) supprimable(s)"}


#: clé -> (libellé, description, action, écrit sur le disque ?)
CATALOG: Dict[str, Dict[str, Any]] = {
    "doctor": {
        "label": "Diagnostic (doctor)",
        "description": "vérifie python, httpx, git, ollama et le store — ne modifie rien",
        "arguments": ["doctor"],
        "mutating": False,
    },
    "dream": {
        "label": "Passe de nuit (dream)",
        "description": "écrit le journal de rêve et ses instantanés (appelle un modèle)",
        "arguments": ["dream", "--now"],
        "budget": "dream",
        "mutating": True,
    },
    "runs-prune": {
        "label": "Nettoyage des runs (simulation)",
        "description": "demande à UHB ce qui serait supprimé — n'efface aucun run",
        "action": "prune",
        "mutating": False,
    },
}


def catalog() -> List[Dict[str, Any]]:
    """Le catalogue tel que l'interface le montre : la clé, ce qu'elle fait, son coût."""
    return [
        {
            "key": key,
            "label": entry["label"],
            "description": entry["description"],
            "mutating": bool(entry["mutating"]),
        }
        for key, entry in CATALOG.items()
    ]


def dream_timeout() -> float:
    """Le temps qu'on laisse à une passe de nuit : son budget, pas une constante à nous.

    `DreamConfig.budget_wall_time_s` (1800 s par défaut) est la durée que la passe s'est
    donnée pour finir son travail. La couper à 120 s produirait un échec qui ne dit rien
    du travail lui-même, seulement de notre patience.
    """
    try:
        from .dream_pass import DreamConfig

        budget = float(getattr(DreamConfig(), "budget_wall_time_s", 1800) or 1800)
    except Exception:  # noqa: BLE001 - une config illisible ne doit pas bloquer la tâche
        budget = 1800.0
    return budget + DREAM_MARGIN


def _timeout_for(entry: Dict[str, Any]) -> float:
    """La patience dépend de l'action : une passe de nuit n'est pas un diagnostic."""
    return dream_timeout() if entry.get("budget") == "dream" else RUN_TIMEOUT


def run_command(key: str, timeout: Optional[float] = None) -> Dict[str, Any]:
    """Exécute une clé du catalogue. Une clé inconnue est refusée, pas interprétée."""
    entry = CATALOG.get(str(key or "").strip())
    if entry is None:
        raise ScheduleError(f"commande non autorisée : {key!r} (catalogue : {', '.join(CATALOG)})")
    if entry.get("action") == "prune":
        return _run_prune()
    return _run_cli(list(entry.get("arguments") or []), timeout=timeout or _timeout_for(entry))


# --------------------------------------------------------------------------- #
# le registre
# --------------------------------------------------------------------------- #
def registry_path() -> Path:
    return uh_home() / REGISTRY


def load_registry() -> Dict[str, Any]:
    path = registry_path()
    if not path.is_file():
        return {"tasks": [], "mechanism": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScheduleError(f"registre illisible ({type(exc).__name__}) : {path}") from exc
    if not isinstance(data, dict):
        raise ScheduleError(f"registre inattendu : {path}")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
    return {"tasks": [task for task in tasks if isinstance(task, dict) and task.get("id")], "mechanism": str(data.get("mechanism") or "")}


def save_registry(data: Dict[str, Any]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _clean_name(raw: str) -> str:
    text = " ".join(str(raw or "").split())
    if not text:
        raise ScheduleError("un nom de tâche est obligatoire")
    if len(text) > NAME_LIMIT:
        raise ScheduleError(f"nom trop long ({len(text)} caractères, maximum {NAME_LIMIT})")
    return text


def _check_every(raw: Any) -> int:
    try:
        minutes = int(raw)
    except (TypeError, ValueError) as exc:
        raise ScheduleError(f"cadence invalide : {raw!r}") from exc
    if minutes < MIN_EVERY or minutes > MAX_EVERY:
        raise ScheduleError(
            f"cadence hors bornes : {minutes} min (minimum {MIN_EVERY}, maximum {MAX_EVERY})"
        )
    return minutes


# --------------------------------------------------------------------------- #
# Windows : déclencheur
# --------------------------------------------------------------------------- #
def _windows() -> bool:
    return os.name == "nt"


def startup_dir() -> Optional[Path]:
    """Le dossier Démarrage de l'utilisateur, ou ``UH_SCHEDULE_STARTUP_DIR`` s'il est posé."""
    override = (os.environ.get("UH_SCHEDULE_STARTUP_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _system_disabled() -> bool:
    return (os.environ.get("UH_SCHEDULE_DISABLE_SYSTEM") or "").strip() in {"1", "true", "yes"}


def task_name(task: Dict[str, Any]) -> str:
    """Le nom côté Windows : préfixé, borné, et dérivé de l'identifiant (jamais du libellé)."""
    return f"UH {task['id']}"


def command_line(task: Dict[str, Any]) -> str:
    """La ligne de commande du déclencheur : elle ne contient que l'id, pas la commande."""
    return subprocess.list2cmdline(
        [sys.executable, "-m", "uh.cli", "schedule", "run", str(task["id"])]
    )


def _run(command: List[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _install_schtasks(task: Dict[str, Any]) -> bool:
    """Tente le Planificateur Windows. Un refus n'est pas une erreur : on replie."""
    if not _windows():
        return False
    try:
        done = _run(
            [
                "schtasks", "/create",
                "/tn", task_name(task),
                "/tr", command_line(task),
                "/sc", "MINUTE",
                "/mo", str(int(task["every_minutes"])),
                "/f",
            ]
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _install_startup(task: Dict[str, Any]) -> Optional[Path]:
    """Écrit le ``.vbs`` du repli : lance la tâche, attend l'intervalle, recommence."""
    folder = startup_dir()
    if folder is None:
        return None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{VBS_PREFIX}{task['id']}.vbs"
        minutes = int(task["every_minutes"])
        body = "\r\n".join(
            [
                "' UH - tache planifiee (repli Startup). Ecrit par UH, supprimable depuis le cockpit.",
                'Set sh = CreateObject("WScript.Shell")',
                f'cmd = "{command_line(task).replace(chr(34), chr(34) * 2)}"',
                "Do",
                "  sh.Run cmd, 0, False",
                f"  WScript.Sleep {minutes} * 60 * 1000",
                "Loop",
                "",
            ]
        )
        path.write_text(body, encoding="utf-8")
        return path
    except OSError:
        return None


def _remove_triggers(task: Dict[str, Any]) -> List[str]:
    """Retire ce que nous avons installé, et seulement ce que nous avons installé."""
    removed: List[str] = []
    if _windows() and not _system_disabled():
        try:
            done = _run(["schtasks", "/delete", "/tn", task_name(task), "/f"])
            if done.returncode == 0:
                removed.append("planificateur")
        except (OSError, subprocess.SubprocessError):
            pass
    folder = startup_dir()
    if folder is not None:
        script = folder / f"{VBS_PREFIX}{task['id']}.vbs"
        try:
            if script.is_file():
                script.unlink()
                removed.append(".vbs Startup")
        except OSError:
            pass
    return removed


def _install_trigger(task: Dict[str, Any]) -> Dict[str, Any]:
    """Installe le déclencheur et rend ce qui a réellement été posé."""
    if _system_disabled():
        return {"mechanism": "registre", "path": "", "detail": "aucun déclencheur (UH_SCHEDULE_DISABLE_SYSTEM)"}
    if _install_schtasks(task):
        return {"mechanism": "schtasks", "path": "", "detail": "tâche du Planificateur Windows"}
    script = _install_startup(task)
    if script is not None:
        return {
            "mechanism": "startup",
            "path": str(script),
            "detail": "repli Startup (.vbs) : tourne à l'ouverture de session, puis à l'intervalle",
        }
    return {
        "mechanism": "registre",
        "path": "",
        "detail": "aucun déclencheur installable sur cette machine : la tâche reste dans le registre",
    }


# --------------------------------------------------------------------------- #
# API du module
# --------------------------------------------------------------------------- #
def _find(data: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    tasks: List[Dict[str, Any]] = data["tasks"]
    for task in tasks:
        if task.get("id") == task_id:
            return task
    raise ScheduleError(f"tâche inconnue : {task_id}")


def listing() -> Dict[str, Any]:
    data = load_registry()
    mechanism = data.get("mechanism") or ""
    labels = {
        "schtasks": "Planificateur Windows",
        "startup": "Repli Startup (.vbs)",
        "registre": "Registre seulement (aucun déclencheur)",
        "": "à déterminer au premier ajout",
    }
    return {
        "tasks": data["tasks"],
        "catalog": catalog(),
        "mechanism": mechanism,
        "mechanism_label": labels.get(mechanism, mechanism),
        "platform": "windows" if _windows() else "autre",
        "system_disabled": _system_disabled(),
        "limits": {"min_every": MIN_EVERY, "max_every": MAX_EVERY, "name_limit": NAME_LIMIT},
    }


def create(name: str, command: str, every_minutes: Any, enabled: bool = True) -> Dict[str, Any]:
    """Crée (ou remplace, par nom) une tâche. La commande doit être une clé du catalogue."""
    label = _clean_name(name)
    key = str(command or "").strip()
    if key not in CATALOG:
        raise ScheduleError(f"commande non autorisée : {key!r} (catalogue : {', '.join(CATALOG)})")
    minutes = _check_every(every_minutes)
    data = load_registry()
    existing = next((task for task in data["tasks"] if task.get("name") == label), None)
    if existing is not None:
        _remove_triggers(existing)
        data["tasks"] = [task for task in data["tasks"] if task.get("id") != existing["id"]]
    task: Dict[str, Any] = {
        "id": f"sch-{uuid.uuid4().hex[:8]}",
        "name": label,
        "command": key,
        "every_minutes": minutes,
        "enabled": bool(enabled),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_run": "",
        "last_status": "",
        "last_detail": "",
    }
    trigger = _install_trigger(task) if task["enabled"] else {"mechanism": "", "path": "", "detail": "désactivée : aucun déclencheur"}
    task["mechanism"] = trigger["mechanism"]
    task["trigger_path"] = trigger["path"]
    data["tasks"].insert(0, task)
    if trigger["mechanism"]:
        data["mechanism"] = trigger["mechanism"]
    save_registry(data)
    return {"task": task, "trigger": trigger}


def toggle(task_id: str) -> Dict[str, Any]:
    data = load_registry()
    task = _find(data, task_id)
    task["enabled"] = not bool(task.get("enabled"))
    if task["enabled"]:
        trigger = _install_trigger(task)
        task["mechanism"] = trigger["mechanism"]
        task["trigger_path"] = trigger["path"]
        if trigger["mechanism"]:
            data["mechanism"] = trigger["mechanism"]
    else:
        _remove_triggers(task)
        task["mechanism"] = ""
        task["trigger_path"] = ""
    save_registry(data)
    return {"task": task}


def remove(task_id: str) -> Dict[str, Any]:
    data = load_registry()
    task = _find(data, task_id)
    removed = _remove_triggers(task)
    data["tasks"] = [item for item in data["tasks"] if item.get("id") != task_id]
    save_registry(data)
    return {"removed": task_id, "triggers": removed}


def run_now(task_id: str) -> Dict[str, Any]:
    """Exécute une tâche tout de suite, en bornant le résultat à ce qui s'est passé."""
    data = load_registry()
    task = _find(data, task_id)
    outcome = run_command(str(task.get("command") or ""))
    task["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    task["last_status"] = "ok" if outcome.get("ok") else "erreur"
    task["last_detail"] = str(outcome.get("detail") or "")[:DETAIL_LIMIT]
    save_registry(data)
    return {"task": task, "ok": bool(outcome.get("ok")), "detail": task["last_detail"]}


def run_by_id(task_id: str) -> int:
    """Point d'entrée du déclencheur Windows (``uh schedule run <id>``)."""
    try:
        data = load_registry()
        task = _find(data, task_id)
        outcome = run_command(str(task.get("command") or ""))
        task["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        task["last_status"] = "ok" if outcome.get("ok") else "erreur"
        task["last_detail"] = str(outcome.get("detail") or "")[:DETAIL_LIMIT]
        save_registry(data)
    except ScheduleError as exc:
        print(f"uh schedule : {exc}", file=sys.stderr)
        return 2
    return 0 if outcome.get("ok") else 1


__all__ = [
    "CATALOG",
    "MAX_EVERY",
    "MIN_EVERY",
    "REGISTRY",
    "ScheduleError",
    "catalog",
    "command_line",
    "create",
    "listing",
    "load_registry",
    "registry_path",
    "remove",
    "run_by_id",
    "run_command",
    "run_now",
    "save_registry",
    "startup_dir",
    "task_name",
    "toggle",
]
