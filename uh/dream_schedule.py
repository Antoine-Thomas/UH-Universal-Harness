"""Install the nightly pass: Task Scheduler when allowed, Startup when not.

Two mechanisms, in this order, and the second one always works:

* **schtasks** - survives a reboot, survives a logoff, needs an admin right that
  a standard account does not have. Attempted first; failure is expected, not an
  error;
* **Startup folder** - a ``.vbs`` in ``Start Menu/Programs/Startup`` runs at
  logon, waits for the target hour, and re-arms itself for the next day.

The script the contract suggested used ``Hour(Now) = 3 And Minute(Now) = 0``,
which only matches inside one 60-second window: a slow tick, a sleep, or a
resume at 03:01 and the night is silently skipped for 24 hours. This one compares
a target ``Date`` computed with ``DateAdd`` and sleeps 30 s, so a missed minute is
retried rather than lost. It also passes a budget and keeps the report - a pass
with ``--no-write`` produces a nightly job that leaves no trace, which is the
opposite of what a dream journal is for.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

from .dream import dreams_dir

#: The task name in Task Scheduler, and the script in the Startup folder.
TASK_NAME = "UH Dream"
VBS_NAME = "uh-dream.vbs"
#: A pass that runs unattended gets a smaller budget than a manual one.
DEFAULT_BUDGET = 40000


class ScheduleError(RuntimeError):
    """The scheduler could not be installed, removed or read."""


def parse_at(at: str) -> Tuple[int, int]:
    """``"03:00"`` -> ``(3, 0)``. Rejects anything that is not a wall time."""
    raw = (at or "").strip()
    parts = raw.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ScheduleError(f"--at expects HH:MM, got {at!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError(f"--at expects HH:MM, got {at!r}")
    return hour, minute


def startup_dir() -> Optional[Path]:
    """The per-user Startup folder, or ``None`` off Windows."""
    if os.name != "nt":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


#: A venv belongs to whoever created it: the Hermes agent rebuilds its own on
#: every update, and a task pointing into it dies silently (`0x80070002`). Only
#: an interpreter named here is allowed to be a venv.
ENV_INTERPRETER = "UH_PYTHON"
#: System Pythons on Windows, newest first (version order, not install order).
_PYTHON_GLOB = "Python3*/python.exe"


def _venv_root(executable: Path) -> Optional[Path]:
    """The venv a ``python.exe`` belongs to, or ``None`` for a system interpreter."""
    root = executable.parent.parent
    return root if (root / "pyvenv.cfg").is_file() else None


def _has_uh(executable: str) -> bool:
    """True when this interpreter can actually ``import uh``."""
    try:
        result = subprocess.run(
            [executable, "-c", "import uh"], capture_output=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _system_pythons() -> List[str]:
    """``%LOCALAPPDATA%\\Programs\\Python\\Python3xx\\python.exe``, newest first."""
    if os.name != "nt":
        return []
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    root = Path(local) / "Programs" / "Python"
    return [str(path) for path in sorted(root.glob(_PYTHON_GLOB), reverse=True)]


def resolve_interpreter(
    candidates: Optional[Sequence[str]] = None,
    probe=None,
) -> str:
    """The interpreter a scheduled run must use: a real Python, with ``uh``.

    ``sys.executable`` is the agent's answer, not the user's: when ``uh dream
    --schedule`` is run from a tool venv, that venv is what got written into the
    task, and the next rebuild of the tool leaves a task that fails with
    ``0x80070002``. So a venv is refused unless ``UH_PYTHON`` names it
    explicitly, and the winner is the first candidate that imports ``uh`` -
    which is the only property the task actually needs.

    ``candidates`` and ``probe`` exist for the tests: resolution shells out.
    """
    configured = (os.environ.get(ENV_INTERPRETER) or "").strip()
    if candidates is not None:
        ordered = list(candidates)
        if configured and configured not in ordered:
            ordered.insert(0, configured)
    else:
        ordered = [configured, *_system_pythons(), sys.executable]
    check = probe or _has_uh
    rejected: List[str] = []
    for raw in ordered:
        if not raw or not Path(raw).is_file():
            continue
        private = _venv_root(Path(raw))
        if private is not None and os.path.normcase(raw) != os.path.normcase(configured):
            rejected.append(f"{raw} (venv {private})")
            continue
        if check(raw):
            return raw
        rejected.append(f"{raw} (cannot import uh)")
    tried = f" (tried {', '.join(rejected)})" if rejected else ""
    raise ScheduleError(
        "no interpreter outside a venv can import uh; install it "
        f"(`python -m pip install -e .`) or set {ENV_INTERPRETER}{tried}"
    )


def python_command() -> List[str]:
    """How to start UH from a script that has no shell: an absolute interpreter."""
    return [resolve_interpreter(), "-m", "uh.cli"]


def pass_arguments(budget: int = DEFAULT_BUDGET) -> List[str]:
    """The ``uh.cli`` arguments one nightly pass runs."""
    # --start-uhb: at 03:00 nothing else is running, and a pass that needs UHB
    # while `uh web` is closed would be skipped every night.
    return ["dream", "--now", "--budget", str(budget), "--start-uhb", "--yes"]


def pass_command(budget: int = DEFAULT_BUDGET) -> str:
    """The command a scheduled run executes, quoted for cmd.exe."""
    return subprocess.list2cmdline(python_command() + pass_arguments(budget))


def next_run(at: str, now: Optional[datetime] = None) -> str:
    """ISO timestamp of the next occurrence of ``at``."""
    hour, minute = parse_at(at)
    current = now or datetime.now()
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return target.isoformat(timespec="seconds")


def vbs_text(command: str, at: str, log: Path) -> str:
    """The launcher. ASCII only: VBScript reads no BOM as the system codepage."""
    hour, minute = parse_at(at)
    return "\r\n".join(
        [
            "' UH Dream - generated by `uh dream --schedule`; delete to uninstall.",
            "' Waits for the target hour, runs one pass, then re-arms for tomorrow.",
            "Option Explicit",
            "Dim sh, target, stamp, rc",
            'Set sh = CreateObject("WScript.Shell")',
            "Do",
            f"  target = DateAdd(\"d\", 0, DateSerial(Year(Now), Month(Now), Day(Now)) + TimeSerial({hour}, {minute}, 0))",
            "  If target <= Now Then target = DateAdd(\"d\", 1, target)",
            "  Do While Now < target",
            "    WScript.Sleep 30000",
            "  Loop",
            "  stamp = Year(Now) & Right(\"0\" & Month(Now), 2) & Right(\"0\" & Day(Now), 2)",
            f"  rc = sh.Run(\"cmd.exe /c {command} >> \"\"{log}\"\" 2>&1\", 0, True)",
            f"  sh.Run \"cmd.exe /c echo %DATE% %TIME% rc=\" & rc >> \"\"{log}\"\"\", 0, True",
            "  ' The loop recomputes tomorrow's target: no blind 24h sleep, so a",
            "  ' resume or a clock change cannot cost a night.",
            "Loop",
            "",
        ]
    )


def _run(argv: List[str]) -> subprocess.CompletedProcess:
    """Run schtasks without decoding its output.

    On a French Windows, schtasks writes in the OEM code page (or UTF-16), and
    ``text=True`` decodes as UTF-8 in a reader thread - a UnicodeDecodeError that
    does not even reach the caller. Only the return code matters here.
    """
    return subprocess.run(argv, capture_output=True, timeout=60)


def task_exists() -> bool:
    if os.name != "nt":
        return False
    try:
        result = _run(["schtasks", "/query", "/tn", TASK_NAME])
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _task_user() -> str:
    """The current user, as the SID Task Scheduler exports (``DOMAIN\\user`` fails)."""
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, timeout=30
        )
        raw = result.stdout
        text = raw.decode("mbcs", "replace") if isinstance(raw, bytes) else str(raw)
        for field in text.replace('"', "").strip().split(","):
            value = field.strip()
            if value.upper().startswith("S-1-5-"):
                return value
    except (OSError, subprocess.SubprocessError):
        pass
    domain = os.environ.get("USERDOMAIN")
    user = os.environ.get("USERNAME") or getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def task_xml(interpreter: str, arguments: Sequence[str], at: str) -> str:
    """The task definition, because XML carries what `schtasks` cannot express.

    ``/tr`` has no switch for *wake the computer* or *run on battery*: a task
    registered that way keeps ``DisallowStartIfOnBatteries`` and never fires on a
    laptop. ``WakeToRun`` says *wake*, and needs the two battery flags off - a
    machine woken at 03:00 would otherwise start, see a battery, and stop.
    """
    hour, minute = parse_at(at)
    start = f"{datetime.now():%Y-%m-%d}T{hour:02d}:{minute:02d}:00"
    command = escape(interpreter)
    args = escape(subprocess.list2cmdline(list(arguments)))
    working = escape(str(Path.home()))
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-16"?>',
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">',
            "  <RegistrationInfo>",
            "    <Description>UH dream: one read-only pass, nightly; wakes the machine.</Description>",
            "  </RegistrationInfo>",
            "  <Triggers>",
            "    <CalendarTrigger>",
            f"      <StartBoundary>{start}</StartBoundary>",
            "      <Enabled>true</Enabled>",
            "      <ScheduleByDay>",
            "        <DaysInterval>1</DaysInterval>",
            "      </ScheduleByDay>",
            "    </CalendarTrigger>",
            "  </Triggers>",
            "  <Principals>",
            '    <Principal id="Author">',
            f"      <UserId>{escape(_task_user())}</UserId>",
            "      <LogonType>InteractiveToken</LogonType>",
            "      <RunLevel>LeastPrivilege</RunLevel>",
            "    </Principal>",
            "  </Principals>",
            "  <Settings>",
            "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
            "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
            "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
            "    <AllowHardTerminate>true</AllowHardTerminate>",
            "    <StartWhenAvailable>true</StartWhenAvailable>",
            "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>",
            "    <IdleSettings>",
            "      <StopOnIdleEnd>false</StopOnIdleEnd>",
            "      <RestartOnIdle>false</RestartOnIdle>",
            "    </IdleSettings>",
            "    <AllowStartOnDemand>true</AllowStartOnDemand>",
            "    <Enabled>true</Enabled>",
            "    <Hidden>false</Hidden>",
            "    <RunOnlyIfIdle>false</RunOnlyIfIdle>",
            "    <WakeToRun>true</WakeToRun>",
            "    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>",
            "    <Priority>7</Priority>",
            "  </Settings>",
            '  <Actions Context="Author">',
            "    <Exec>",
            f"      <Command>{command}</Command>",
            f"      <Arguments>{args}</Arguments>",
            f"      <WorkingDirectory>{working}</WorkingDirectory>",
            "    </Exec>",
            "  </Actions>",
            "</Task>",
            "",
        ]
    )


def _install_schtasks(interpreter: str, arguments: Sequence[str], at: str) -> bool:
    """Register the daily task from XML; False means "no right", not "no try"."""
    if os.name != "nt":
        return False
    # A leftover task may still point at an old interpreter: `/create /f` keeps
    # the previous action in some versions, so delete first. Then one XML file,
    # which is the only path that carries WakeToRun and the battery flags.
    try:
        _run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])
    except (OSError, subprocess.SubprocessError):
        pass
    xml_path: Optional[Path] = None
    try:
        fd, name = tempfile.mkstemp(suffix=".xml", prefix="uh-dream-")
        os.close(fd)
        xml_path = Path(name)
        xml_path.write_text(task_xml(interpreter, arguments, at), encoding="utf-16")
        result = _run(
            ["schtasks", "/create", "/tn", TASK_NAME, "/xml", str(xml_path), "/f"]
        )
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if xml_path is not None:
            try:
                xml_path.unlink()
            except OSError:
                pass
    return result.returncode == 0


def _install_startup(command: str, at: str) -> Path:
    folder = startup_dir()
    if folder is None:
        raise ScheduleError("no Startup folder here: Windows only, and APPDATA must be set")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / VBS_NAME
        log = dreams_dir() / "schedule.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(vbs_text(command, at, log), encoding="ascii", errors="replace")
    except OSError as exc:
        raise ScheduleError(f"cannot write the Startup script: {exc}") from exc
    return target


def install(at: str = "03:00", budget: int = DEFAULT_BUDGET) -> dict:
    """Install the nightly pass. Task Scheduler if allowed, Startup otherwise."""
    parse_at(at)
    exe = resolve_interpreter()
    arguments = pass_arguments(budget)
    command = subprocess.list2cmdline([exe, "-m", "uh.cli", *arguments])
    if _install_schtasks(exe, arguments, at):
        return {
            "mechanism": "schtasks",
            "detail": (
                f"daily task '{TASK_NAME}' at {at} "
                "(wakes the machine, runs on battery)"
            ),
            "command": command,
            "interpreter": exe,
            "path": None,
        }
    script = _install_startup(command, at)
    return {
        "mechanism": "startup",
        "detail": (
            "Task Scheduler requires admin; used the Startup fallback instead "
            f"(runs at logon, then daily at {at} while the session is open)"
        ),
        "command": command,
        "interpreter": exe,
        "path": str(script),
    }


def uninstall() -> dict:
    """Remove both mechanisms; report what was actually there."""
    removed: List[str] = []
    if os.name == "nt":
        try:
            result = _run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])
            if result.returncode == 0:
                removed.append(f"task '{TASK_NAME}'")
        except (OSError, subprocess.SubprocessError):
            pass
    folder = startup_dir()
    if folder is not None:
        script = folder / VBS_NAME
        if script.is_file():
            try:
                script.unlink()
                removed.append(str(script))
            except OSError as exc:
                raise ScheduleError(f"cannot remove {script}: {exc}") from exc
    return {"removed": removed}


def last_pass() -> Optional[str]:
    """The newest pass folder, if any - the only honest "it ran" signal."""
    folder = dreams_dir() / "passes"
    if not folder.is_dir():
        return None
    runs = sorted((path for path in folder.iterdir() if path.is_dir()), reverse=True)
    return runs[0].name if runs else None


def status(at: str = "03:00") -> Dict[str, object]:
    """What is installed, when it last ran, when it will run again."""
    folder = startup_dir()
    script = (folder / VBS_NAME) if folder is not None else None
    has_task = task_exists()
    has_startup = bool(script and script.is_file())
    mechanism = "schtasks" if has_task else ("startup" if has_startup else None)
    return {
        "installed": mechanism is not None,
        "mechanism": mechanism,
        "task": has_task,
        "startup": str(script) if has_startup else None,
        "last_pass": last_pass(),
        "next_run": next_run(at) if mechanism else None,
        "platform": "windows" if os.name == "nt" else os.name,
    }


__all__ = [
    "DEFAULT_BUDGET",
    "ENV_INTERPRETER",
    "ScheduleError",
    "TASK_NAME",
    "VBS_NAME",
    "install",
    "last_pass",
    "next_run",
    "parse_at",
    "pass_arguments",
    "pass_command",
    "resolve_interpreter",
    "startup_dir",
    "status",
    "task_xml",
    "uninstall",
    "vbs_text",
]
