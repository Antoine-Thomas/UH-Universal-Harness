"""Supervision de l'infrastructure locale — en LECTURE SEULE.

Rien ici n'arrête un service, n'en redémarre un, ni ne supprime un fichier : le module
lit des ports, des tailles et des fins de journal. C'est la règle du projet - un
processus qu'on n'a pas lancé n'est pas à nous - écrite dans le code plutôt que dans une
consigne, et c'est ce qui rend cette vue sûre à ouvrir pendant un tour.

Les secrets ne sortent pas : un journal contient des URL `?token=…` et des en-têtes
`Authorization`, donc chaque ligne est masquée avant de quitter ce module.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

#: On ne lit jamais un journal entier : les journaux de UH et de UHB grossissent vite
#: (le log d'Ollama a dépassé 2,5 Mo en une session), et la vue n'a besoin que de la fin.
MAX_LOG_BYTES = 64 * 1024
DEFAULT_LOG_LINES = 20
MAX_LOG_LINES = 200
#: Une mesure de dossier qui prend plus de deux secondes rend un MINIMUM, et le dit.
DISK_BUDGET_S = 2.0
PROBE_TIMEOUT = 0.4
FREE_WARN_BYTES = 5 * 1024**3
FREE_ERROR_BYTES = 1 * 1024**3
VRAM_WARN_PERCENT = 85.0

_SECRETS = (
    re.compile(r"(token=)[^&\s\"']+", re.I),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{6,}", re.I),
    re.compile(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._\-]{6,}", re.I),
    re.compile(r"(?:sk-|hf_|gsk_|xai-|or-)[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}\b"),
)


def _mask(match: "re.Match[str]") -> str:
    return (match.group(1) + "***") if match.groups() else "***"


#: Les services que la roadmap demande en plus de UH/UHB/Ollama. Sondés par TCP
#: uniquement : la vue ne parle pas à leur API, donc aucune authentification à porter,
#: aucun effet de bord, et un service qui exige un jeton reste mesurable.
EXTRA_SERVICES = (
    {"name": "RAG", "env": "UH_RAG_URL", "default": "http://127.0.0.1:8200"},
    {"name": "SiYuan", "env": "SIYUAN_URL", "default": "http://127.0.0.1:6806"},
    {"name": "MARI", "env": "UH_MARI_URL", "default": "http://127.0.0.1:55553"},
)


def extra_services() -> List[Dict[str, str]]:
    """Les URL des services complémentaires, variables d'environnement d'abord."""
    answer: List[Dict[str, str]] = []
    for entry in EXTRA_SERVICES:
        url = (os.environ.get(entry["env"]) or "").strip() or entry["default"]
        answer.append({"name": entry["name"], "url": url})
    return answer


def docker_status(timeout: float = 2.0) -> Dict[str, Any]:
    """L'état du démon Docker, sans rien démarrer : ``docker version`` est en lecture seule.

    Le démon arrêté est un cas normal sur un poste de développement - c'est même la preuve
    D-7 (un service injoignable, le panneau reste lisible). On rapporte donc la première
    ligne de l'erreur au lieu d'un « indisponible » muet.
    """
    blank = {"name": "Docker", "url": "", "port": None, "listening": False}
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {
            **blank,
            "state": "down",
            "detail": "docker absent du PATH",
            "reason": "binaire introuvable",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            **blank,
            "state": "down",
            "detail": "démon injoignable",
            "reason": f"{type(exc).__name__}: {exc}",
        }
    version = (done.stdout or "").strip()
    if done.returncode == 0 and version:
        return {
            **blank,
            "state": "up",
            "detail": f"moteur v{version}",
            "reason": "",
            "listening": True,
        }
    reason = " ".join((done.stderr or done.stdout or "").split())[:200] or f"code {done.returncode}"
    return {**blank, "state": "down", "detail": "démon arrêté ou injoignable", "reason": reason}


def _join_reason(headline: str, probe_report: Dict[str, Any]) -> str:
    """La raison d'un service arrêté : ce que la sonde a dit, et sur quel port.

    « uhb unreachable » ne se corrige pas ; « port 8101 : ConnectionRefusedError » oui.
    """
    parts = [headline] if headline else []
    if not probe_report.get("listening"):
        port = probe_report.get("port")
        detail = str(probe_report.get("reason") or "")
        if port and detail:
            parts.append(f"port {port} : {detail}")
        elif detail:
            parts.append(detail)
    return " · ".join(part for part in parts if part)


def redact(text: str) -> str:
    """Masque jetons, clés et en-têtes d'autorisation avant toute sortie HTTP."""
    out = str(text or "")
    for pattern in _SECRETS:
        out = pattern.sub(_mask, out)
    return out


def probe(url: str, timeout: float = PROBE_TIMEOUT) -> Dict[str, Any]:
    """Un port répond-il ? Un simple connect, aucune requête, aucun effet de bord."""
    parts = urlsplit(url or "")
    host = parts.hostname or "127.0.0.1"
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        # `urlsplit(...).port` lève sur un port non numérique ou hors bornes (« :99999 »,
        # « :abc »). C'est une URL fausse à AFFICHER, pas une exception à remonter en 500 :
        # la vue Infra doit rester lisible pour diagnostiquer précisément ça.
        return {
            "host": host,
            "port": None,
            "listening": False,
            "ms": 0,
            "reason": f"port invalide dans {url!r}",
        }
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {
                "host": host,
                "port": port,
                "listening": True,
                "ms": int((time.monotonic() - started) * 1000),
                "reason": "",
            }
    except OSError as exc:
        return {
            "host": host,
            "port": port,
            "listening": False,
            "ms": None,
            "reason": f"{type(exc).__name__}: {exc}",
        }


def tail_lines(path: Path, count: int = DEFAULT_LOG_LINES) -> Dict[str, Any]:
    """Les dernières lignes d'un journal, bornées en octets, masquées, jamais devinées."""
    wanted = max(1, min(int(count or DEFAULT_LOG_LINES), MAX_LOG_LINES))
    answer: Dict[str, Any] = {
        "name": path.name,
        "path": str(path),
        "exists": path.is_file(),
        "lines": [],
        "returned": 0,
        "bytes": 0,
        "head_cut": False,
        "reason": "",
    }
    if not path.is_file():
        answer["reason"] = "absent"
        return answer
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            start = max(0, size - MAX_LOG_BYTES)
            handle.seek(start)
            chunk = handle.read()
    except OSError as exc:
        answer["reason"] = f"illisible : {type(exc).__name__}"
        return answer
    lines = chunk.decode("utf-8", "replace").splitlines()
    if start > 0 and lines:
        # Le premier morceau est presque toujours une ligne coupée : la jeter est plus
        # honnête que d'afficher une ligne tronquée comme si elle était entière.
        lines = lines[1:]
        answer["head_cut"] = True
    answer["bytes"] = size
    answer["lines"] = [redact(line) for line in lines[-wanted:]]
    answer["returned"] = len(answer["lines"])
    return answer


def directory_size(path: Path, budget_s: float = DISK_BUDGET_S) -> Dict[str, Any]:
    """Taille réelle d'une arborescence, avec un budget de temps et un aveu s'il est dépassé."""
    answer: Dict[str, Any] = {
        "path": str(path),
        "exists": path.is_dir(),
        "bytes": 0,
        "files": 0,
        "complete": True,
        "reason": "",
    }
    if not path.is_dir():
        answer["reason"] = "absent"
        return answer
    deadline = time.monotonic() + max(0.1, budget_s)
    total = 0
    files = 0
    stack: List[Path] = [path]
    try:
        while stack:
            if time.monotonic() > deadline:
                answer["complete"] = False
                answer["reason"] = (
                    f"mesure interrompue après {budget_s:g} s : la taille affichée est un minimum"
                )
                break
            current = stack.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue  # un fichier verrouillé ne rend pas la mesure fausse
    except OSError as exc:
        answer["complete"] = False
        answer["reason"] = f"illisible : {type(exc).__name__}"
    answer["bytes"] = total
    answer["files"] = files
    return answer


def _free_space(path: Path) -> Dict[str, Any]:
    target = path if path.exists() else path.parent
    try:
        usage = shutil.disk_usage(str(target))
    except OSError as exc:
        return {"path": str(target), "reason": f"illisible : {type(exc).__name__}"}
    return {
        "path": str(target),
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "used_percent": round(100.0 * usage.used / usage.total, 1) if usage.total else 0.0,
    }


def collect(
    *,
    version: str,
    port: int,
    started_at: float,
    install: Dict[str, Any],
    uhb_url: Optional[str],
    uhb_status: Optional[Dict[str, Any]],
    ollama_url: str,
    ollama_up: bool,
    home: Path,
    runs_root: Path,
    log_paths: List[Path],
    log_lines: int = DEFAULT_LOG_LINES,
    vram: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Un instantané complet. Aucun appel ne peut lever : une panne devient une raison."""
    now = time.time()
    uhb = uhb_status or {"error": "second cerveau désactivé (UH_UHB_URL vide ou hors loopback)"}
    uhb_error = str(uhb.get("error") or "") if isinstance(uhb, dict) else ""
    uhb_probe = (
        probe(uhb_url) if uhb_url else {"listening": False, "reason": "non configuré", "port": None}
    )
    ollama_probe = probe(ollama_url)

    services = [
        {
            "name": "UH",
            "state": "up",
            "detail": f"v{version} · port {port}",
            "reason": "",
            "url": f"http://127.0.0.1:{port}",
            "port": port,
            "uptime_s": max(0, int(now - started_at)) if started_at else 0,
        },
        {
            "name": "UHB",
            "state": "up" if (uhb_url and not uhb_error) else "down",
            "detail": (
                f"mode {uhb.get('mode') or '?'} · v{uhb.get('version') or '?'} · port {uhb_probe.get('port')}"
                if uhb_url and not uhb_error
                else "arrêté ou injoignable"
            ),
            # La raison dit le PORT et l'erreur système, pas seulement « unreachable » :
            # « port 8101 : ConnectionRefusedError » se corrige, « unreachable » se devine.
            "reason": _join_reason(uhb_error, uhb_probe),
            "url": uhb_url or "",
            "port": uhb_probe.get("port"),
            "owned": bool(uhb.get("owned")) if isinstance(uhb, dict) else False,
            "listening": bool(uhb_probe.get("listening")),
        },
        {
            "name": "Ollama",
            "state": "up" if ollama_up else "down",
            "detail": f"port {ollama_probe.get('port')}"
            if ollama_up
            else "aucune réponse sur /api/tags",
            "reason": "" if ollama_up else _join_reason("", ollama_probe) or "muet",
            "url": ollama_url,
            "port": ollama_probe.get("port"),
            "listening": bool(ollama_probe.get("listening")),
        },
    ]

    # Les services complémentaires de la roadmap (RAG, SiYuan, MARI) sont sondés par TCP
    # seul, puis Docker qui n'est pas un port. Une ligne en plus, jamais une ligne en moins :
    # un service arrêté ne retire rien du panneau (critère D-7).
    for entry in extra_services():
        report = probe(entry["url"])
        services.append(
            {
                "name": entry["name"],
                "state": "up" if report.get("listening") else "down",
                "detail": f"port {report.get('port')}"
                if report.get("listening")
                else "aucune écoute",
                "reason": "" if report.get("listening") else _join_reason("", report) or "muet",
                "url": entry["url"],
                "port": report.get("port"),
                "listening": bool(report.get("listening")),
            }
        )
    services.append(docker_status())

    disk = {
        "home": directory_size(home),
        "runs": directory_size(runs_root),
        "free": _free_space(home),
    }
    logs = [tail_lines(path, log_lines) for path in log_paths]

    alerts: List[Dict[str, str]] = []

    def add(level: str, label: str, detail: str) -> None:
        alerts.append({"level": level, "label": label, "detail": detail})

    if services[1]["state"] != "up":
        add("error", "Second cerveau arrêté", str(services[1]["reason"] or "aucune réponse"))
    if not ollama_up:
        add("warn", "Ollama ne répond pas", f"{ollama_url} · {services[2]['reason']}")
    # Les complémentaires sont optionnels : leur absence est signalée en « warn », pas en
    # « error ». Les confondre avec le second cerveau ferait crier une vue saine.
    for service in services[3:]:
        if service.get("state") != "up":
            add(
                "warn",
                f"{service['name']} injoignable",
                str(service.get("reason") or service.get("detail") or "aucune réponse"),
            )
    free = disk["free"].get("free_bytes")
    if isinstance(free, int):
        if free < FREE_ERROR_BYTES:
            add("error", "Disque presque plein", f"{free // (1024**2)} Mo libres")
        elif free < FREE_WARN_BYTES:
            add("warn", "Espace disque faible", f"{free // (1024**2)} Mo libres")
    if vram and float(vram.get("percent") or 0) >= VRAM_WARN_PERCENT:
        add(
            "warn",
            "VRAM saturée",
            f"{vram.get('used_mb')} / {vram.get('total_mb')} Mo ({vram.get('percent')} %)",
        )
    if not install.get("editable", True):
        add(
            "warn",
            "Le serveur ne tourne pas depuis le dépôt",
            f"code chargé : {install.get('package', '?')} · l'interface peut être plus récente que le serveur",
        )
    if install.get("dirty"):
        add(
            "warn",
            "Code servi modifié",
            f"des fichiers suivis diffèrent du commit {install.get('commit') or '?'}",
        )
    if not alerts:
        add("ok", "Rien à signaler", "services, disque et VRAM dans les seuils")

    return {
        "ok": True,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "version": version,
        "install": install,
        "services": services,
        "disk": disk,
        "logs": logs,
        "vram": vram,
        "alerts": alerts,
        # La vue est en lecture seule : le contrat est dit ici, pour qu'aucune version
        # future ne l'oublie en ajoutant un bouton.
        "read_only": True,
        "actions_available": [],
    }


__all__ = [
    "DEFAULT_LOG_LINES",
    "MAX_LOG_LINES",
    "collect",
    "directory_size",
    "probe",
    "redact",
    "tail_lines",
]
