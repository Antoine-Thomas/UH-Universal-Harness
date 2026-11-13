"""Starting UHB from ``uh web``: one entry point, two processes, one window.

The cockpit is not useful without the second brain, and asking a user to open a
terminal and type ``python -m uhb.entrypoints.server`` defeats the desktop
shortcut. So ``uh web`` starts UHB itself when it can find a checkout.

Guard rails, because this spawns a process the user did not name:

* **loopback only** - the URL comes from ``UH_UHB_URL`` (default
  ``http://127.0.0.1:8101``) and is refused when it is not loopback, exactly as
  the ``uhb`` toolset does. No spawn for a remote control plane;
* **never twice** - if something already answers on the port (the user's own
  UHB, a leftover), nothing is started. A second server on one port is an error;
* **invisible** - ``CREATE_NO_WINDOW`` on Windows: the shortcut already hides its
  console, a child window would undo that;
* **not fatal** - a missing checkout, a UHB too old to boot, a port taken: one
  warning line, and the UI runs without it. The tools are simply absent, which
  the console already reports as ``UHB: …`` or nothing;
* **no orphan** - :func:`stop_running` is called from the same ``finally`` that
  stops the web server, including on Ctrl+C.

The child writes to ``$UH_HOME/uhb.log``: a hidden window cannot show a
traceback, and an empty log would make a failed spawn impossible to diagnose.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlsplit

ENV_PATH = "UH_UHB_PATH"
DEFAULT_URL = "http://127.0.0.1:8101"
MODULE = "uhb.entrypoints.server"
MODE = "dev"
LOG_NAME = "uhb.log"
START_TIMEOUT = 5.0
STOP_TIMEOUT = 5.0
_MARKER = Path("uhb") / "entrypoints" / "server.py"

#: ``CREATE_NO_WINDOW``: no console for the child (Windows only, 0 elsewhere).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

StatusCheck = Callable[[str, float], bool]


class SpawnError(RuntimeError):
    """UHB could not be started. The message is user-facing, never a traceback."""


def _loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").strip("[]").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def url() -> Optional[str]:
    """The UHB URL to serve next to the UI, or ``None`` when it must not be served.

    ``None`` for three different reasons, all normal: the variable is unset (the
    default applies), the variable is empty (the user disabled UHB), or the value
    is not an http(s) loopback URL - refusing costs one line, contacting a remote
    "UHB" costs the local-first promise.
    """
    raw = os.environ.get("UH_UHB_URL")
    value = (DEFAULT_URL if raw is None else raw).strip()
    if not value:
        return None
    if urlsplit(value).scheme not in {"http", "https"}:
        return None
    return value.rstrip("/") if _loopback(value) else None


def _port(url_value: str) -> int:
    parts = urlsplit(url_value)
    return parts.port or (443 if parts.scheme == "https" else 80)


def port_open(url_value: str, timeout: float = 0.3) -> bool:
    """True when something listens on the host:port — no HTTP, just a connect."""
    parts = urlsplit(url_value)
    host = (parts.hostname or "127.0.0.1").strip("[]")
    try:
        with socket.create_connection((host, _port(url_value)), timeout=timeout):
            return True
    except OSError:
        return False


def status_ok(url_value: str, timeout: float = 1.0) -> bool:
    """True when UHB answers ``GET /status`` with 200."""
    import httpx

    try:
        response = httpx.get(f"{url_value}/status", timeout=timeout)
    except Exception:  # noqa: BLE001 - readiness is a state, not an exception
        return False
    return response.status_code == 200


def candidate_checkouts() -> List[Path]:
    """Where a UHB checkout may be, most explicit first.

    ``UH_UHB_PATH`` wins and is the only way to name an unusual location. The
    rest are the layouts this machine actually uses: the public repository next
    to the home directory, and the private one inside ``hermes-home-vision``.
    """
    found: List[Path] = []
    configured = (os.environ.get(ENV_PATH) or "").strip()
    if configured:
        found.append(Path(configured).expanduser())
    home = Path.home()
    found.extend(
        [
            home / "UHB-Universal-Harness-Backend",
            home / "hermes-home-vision" / "uhb",
            home / "uhb",
        ]
    )
    return found


def find_checkout() -> Optional[Path]:
    """The first candidate that actually contains the server module."""
    for candidate in candidate_checkouts():
        if (candidate / _MARKER).is_file():
            return candidate
    return None


def _log_path() -> Path:
    from .toolsets.memory import uh_home

    home = uh_home()
    home.mkdir(parents=True, exist_ok=True)
    return home / LOG_NAME


def build_command(port: int) -> List[str]:
    """The child command. Same interpreter as the parent: same environment, same UHB."""
    return [sys.executable, "-m", MODULE, "--port", str(port), "--mode", MODE]


@dataclass
class Spawned:
    """A UHB this process started: its child, its log, the way to stop it."""

    process: subprocess.Popen
    url: str
    log: Path
    checkout: Path

    @property
    def pid(self) -> int:
        return int(self.process.pid)

    def alive(self) -> bool:
        return self.process.poll() is None

    def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        """Terminate the whole tree, then kill if it will not go. Never raises.

        Closing the job handle is what actually stops UHB: the launcher process
        in ``sys.executable`` is often a shim that re-execs the real interpreter,
        so terminating the child we hold leaves the *grandchild* - the one that
        binds the port - running. ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` kills
        every member, which is the whole tree.
        """
        job = _JOBS.pop(self.process.pid, None)
        if job:
            _close_job(job)
        if self.process.poll() is not None:
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:  # pragma: no cover - unkillable child
                pass
        except OSError:  # pragma: no cover - already gone
            pass


# --------------------------------------------------------------------------- #
# the one child per process
# --------------------------------------------------------------------------- #
_RUNNING: Optional[Spawned] = None
#: pid -> job handle. Held for the life of the window: closing it kills the child.
_JOBS: dict = {}


def running() -> Optional[Spawned]:
    return _RUNNING


def ensure(
    enabled: bool = True,
    timeout: float = START_TIMEOUT,
    runner: Optional[Callable[[List[str], Path, Path], subprocess.Popen]] = None,
    checker: Optional[StatusCheck] = None,
) -> Optional[Spawned]:
    """Start UHB if it is neither running nor reachable. ``None`` means "not mine".

    ``None`` is returned - not an error - in three normal cases: the feature is
    disabled (``--no-uhb``), ``UH_UHB_URL`` points somewhere that is not loopback,
    or something already listens on the port. ``checker`` answers the *other*
    question - "is the child ready yet" - and is never used as the pre-check:
    a slow UHB must not be mistaken for a running one. Only a real attempt that fails raises
    :class:`SpawnError`, so the caller can print one line and keep going.
    """
    global _RUNNING
    if not enabled:
        return None
    target = url()
    if target is None:
        return None
    if _RUNNING is not None and _RUNNING.alive():
        return _RUNNING
    check = checker or status_ok
    if port_open(target):
        return None  # someone else's UHB, or a leftover: do not fight it

    checkout = find_checkout()
    if checkout is None:
        raise SpawnError(
            "no UHB checkout found (looked for uhb/entrypoints/server.py); "
            f"set {ENV_PATH} to its folder to start it with the UI"
        )

    log = _log_path()
    command = build_command(_port(target))
    spawn = runner or _spawn
    process = spawn(command, checkout, log)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SpawnError(
                f"UHB exited during startup (code {process.returncode}); see {log}"
            )
        if check(target, 1.0):
            _RUNNING = Spawned(process=process, url=target, log=log, checkout=checkout)
            return _RUNNING
        time.sleep(0.1)

    # A UHB that never answered is not a UHB we keep running.
    Spawned(process=process, url=target, log=log, checkout=checkout).stop()
    raise SpawnError(f"UHB did not answer on {target} within {timeout:.0f}s; see {log}")


def _job_for(process: subprocess.Popen) -> Optional[int]:
    """Put the child in a job object that kills it when this process dies.

    ``finally`` handles Ctrl+C and a normal exit, but clicking the window's close
    button terminates the parent abruptly - Windows does not unwind Python. A job
    object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` closes that hole: the
    handle lives exactly as long as this process, and the kernel does the rest.

    Best effort: on a system that refuses the job (an old Windows, a restrictive
    policy), the child is simply not protected and the earlier ``finally`` still
    runs. Nothing here raises.
    """
    if os.name != "nt":  # pragma: no cover - Windows-only API
        return None
    try:
        import ctypes
        from ctypes import wintypes

        windll = getattr(ctypes, "WinDLL", None)  # absent hors Windows
        if windll is None:
            return None
        kernel32 = windll("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION, with the one flag that matters.
        class Limits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Limits),
                ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info)  # 9 = ExtendedLimitInformation
        ):
            kernel32.CloseHandle(job)
            return None
        # ``_handle`` is the only way Popen exposes its process handle; the
        # attribute is private but stable across CPython versions on Windows.
        raw_handle = getattr(process, "_handle", None)
        if raw_handle is None or not kernel32.AssignProcessToJobObject(
            job, wintypes.HANDLE(raw_handle)
        ):
            kernel32.CloseHandle(job)
            return None
        return int(job)
    except Exception:  # noqa: BLE001 - the job is a belt, not the braces
        return None


def _close_job(handle: int) -> None:
    """Close a job handle: ``KILL_ON_JOB_CLOSE`` ends every process in it."""
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)  # absent hors Windows
        if windll is None:
            return
        windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:  # noqa: BLE001 - best effort, the terminate() path follows
        pass


def _spawn(command: List[str], checkout: Path, log: Path) -> subprocess.Popen:
    handle = open(log, "ab")
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            command,
            cwd=str(checkout),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
            close_fds=True,
        )
    except OSError as exc:
        handle.close()
        raise SpawnError(f"cannot start UHB ({type(exc).__name__}: {exc})") from exc
    job = _job_for(process)
    if job:
        # Keep the handle for the lifetime of the child: closing it kills the job.
        _JOBS[process.pid] = job
    handle.close()
    return process


def stop_running(timeout: float = STOP_TIMEOUT) -> bool:
    """Stop the UHB this process started. True when there was one."""
    global _RUNNING
    child, _RUNNING = _RUNNING, None
    if child is None:
        return False
    child.stop(timeout)
    return True


__all__ = [
    "DEFAULT_URL",
    "ENV_PATH",
    "SpawnError",
    "Spawned",
    "build_command",
    "candidate_checkouts",
    "ensure",
    "find_checkout",
    "port_open",
    "running",
    "status_ok",
    "stop_running",
    "url",
]
