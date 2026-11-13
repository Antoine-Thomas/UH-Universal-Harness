"""`uh web` starting UHB: URL policy, one child, readiness, cleanup.

No real UHB, no real subprocess: the child is a fake process and readiness is a
stub, so the suite stays fast and offline. The one case that touches the system
is a missing binary, which is exactly how a broken spawn fails in practice.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from uh import cli, commands, uhb_spawn
from uh import config as uh_config
from uh import console as uh_console

URL = "http://127.0.0.1:8101"


@pytest.fixture(autouse=True)
def uhb_url(monkeypatch: pytest.MonkeyPatch) -> str:
    """conftest disables UHB globally; these tests name the loopback URL."""
    monkeypatch.setenv("UH_UHB_URL", URL)
    monkeypatch.setattr(uhb_spawn, "_RUNNING", None)
    return URL


class FakeProc:
    """The slice of ``Popen`` the module uses, with a settable exit code."""

    def __init__(self, returncode: int | None = None, pid: int = 4242) -> None:
        self._returncode = returncode
        self.pid = pid
        self.terminated = False
        self.killed = False
        self.waits = 0

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = 0

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self, timeout: float | None = None) -> int | None:
        self.waits += 1
        return self._returncode


def checkout(tmp_path: Path) -> Path:
    """A folder that looks like a UHB checkout (the marker file exists)."""
    root = tmp_path / "uhb-checkout"
    (root / "uhb" / "entrypoints").mkdir(parents=True)
    (root / "uhb" / "entrypoints" / "server.py").write_text("", encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# which URL, which checkout
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value",
    ["http://127.0.0.1:8101", "http://localhost:8101", "http://[::1]:8101"],
)
def test_loopback_urls_are_accepted(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_UHB_URL", value)
    assert uhb_spawn.url() == value


@pytest.mark.parametrize(
    "value",
    ["", "http://192.168.1.10:8101", "http://uhb.example.com", "ftp://127.0.0.1:8101"],
)
def test_anything_but_loopback_disables_the_spawn(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_UHB_URL", value)
    assert uhb_spawn.url() is None
    assert uhb_spawn.ensure() is None, "no checkout, no child, and no exception"


def test_the_env_path_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = checkout(tmp_path)
    monkeypatch.setenv(uhb_spawn.ENV_PATH, str(root))
    assert uhb_spawn.find_checkout() == root


def test_a_folder_without_the_server_module_is_not_a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(uhb_spawn.ENV_PATH, str(tmp_path))
    monkeypatch.setattr(uhb_spawn.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert uhb_spawn.find_checkout() is None


# --------------------------------------------------------------------------- #
# ensure(): when not to start anything
# --------------------------------------------------------------------------- #
def test_disabled_means_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    monkeypatch.setattr(uhb_spawn, "find_checkout", lambda: calls.append("looked") or None)

    assert uhb_spawn.ensure(enabled=False) is None
    assert calls == [], "enabled=False must not even look for a checkout"


def test_a_listening_port_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uhb_spawn, "port_open", lambda *a, **k: True)
    calls: list = []
    monkeypatch.setattr(uhb_spawn, "find_checkout", lambda: calls.append("looked") or None)

    assert uhb_spawn.ensure() is None
    assert calls == [], "a UHB is already there: starting a second one is the bug"


# --------------------------------------------------------------------------- #
# ensure(): the real attempt
# --------------------------------------------------------------------------- #
def test_a_missing_checkout_is_one_readable_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(uhb_spawn, "port_open", lambda *a, **k: False)
    monkeypatch.setattr(uhb_spawn.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.delenv(uhb_spawn.ENV_PATH, raising=False)

    with pytest.raises(uhb_spawn.SpawnError) as caught:
        uhb_spawn.ensure()

    assert uhb_spawn.ENV_PATH in str(caught.value)
    assert "Traceback" not in str(caught.value)


def test_the_child_is_started_with_the_right_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path)
    monkeypatch.setenv(uhb_spawn.ENV_PATH, str(root))
    monkeypatch.setattr(uhb_spawn, "port_open", lambda *a, **k: False)
    seen: list = []
    proc = FakeProc()

    def runner(command, cwd, log):
        seen.append((command, cwd, log))
        return proc

    started = uhb_spawn.ensure(runner=runner, checker=lambda url, timeout: True)

    assert started is not None
    assert started.pid == 4242
    assert started.url == URL
    assert started.alive()
    command, cwd, log = seen[0]
    # The interpreter is the one that can import UHB, not necessarily the one
    # running UH: a 3.10 parent used to spawn a 3.10 UHB that died on
    # datetime.UTC before it could open the port.
    assert command[0] == uhb_spawn.interpreter(root)
    assert command[1:] == ["-m", "uhb.entrypoints.server", "--port", "8101", "--mode", "dev"]
    if sys.version_info >= (3, 11):
        assert uhb_spawn._supports_uhb(command[0]), "the spawned interpreter must import UHB"
    assert cwd == root
    assert log.name == uhb_spawn.LOG_NAME
    assert uhb_spawn.running() is started


def test_one_child_per_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = checkout(tmp_path)
    monkeypatch.setenv(uhb_spawn.ENV_PATH, str(root))
    monkeypatch.setattr(uhb_spawn, "port_open", lambda *a, **k: False)
    launches: list = []

    def runner(command, cwd, log):
        launches.append(command)
        return FakeProc()

    first = uhb_spawn.ensure(runner=runner, checker=lambda url, timeout: True)
    again = uhb_spawn.ensure(runner=runner, checker=lambda url, timeout: True)

    assert first is again
    assert len(launches) == 1, "a second /web must not start a second UHB"


def test_a_child_that_never_answers_is_stopped_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path)
    monkeypatch.setenv(uhb_spawn.ENV_PATH, str(root))
    monkeypatch.setattr(uhb_spawn, "port_open", lambda *a, **k: False)
    proc = FakeProc()

    with pytest.raises(uhb_spawn.SpawnError) as caught:
        uhb_spawn.ensure(runner=lambda *a: proc, checker=lambda url, timeout: False, timeout=0.2)

    assert proc.terminated is True, "a UHB that never answers must not be left running"
    assert "did not answer" in str(caught.value)
    assert uhb_spawn.running() is None


def test_a_child_that_dies_during_startup_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path)
    monkeypatch.setenv(uhb_spawn.ENV_PATH, str(root))
    monkeypatch.setattr(uhb_spawn, "port_open", lambda *a, **k: False)

    with pytest.raises(uhb_spawn.SpawnError) as caught:
        uhb_spawn.ensure(
            runner=lambda *a: FakeProc(returncode=3),
            checker=lambda url, timeout: False,
            timeout=0.5,
        )

    assert "exited during startup" in str(caught.value)
    assert "uhb.log" in str(caught.value)


def test_stop_running_kills_the_child_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = checkout(tmp_path)
    monkeypatch.setenv(uhb_spawn.ENV_PATH, str(root))
    monkeypatch.setattr(uhb_spawn, "port_open", lambda *a, **k: False)
    proc = FakeProc()
    uhb_spawn.ensure(runner=lambda *a: proc, checker=lambda url, timeout: True)

    assert uhb_spawn.stop_running() is True
    assert proc.terminated is True
    assert uhb_spawn.running() is None
    assert uhb_spawn.stop_running() is False, "second call: nothing to stop"


def test_a_stubborn_child_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = checkout(tmp_path)
    monkeypatch.setenv(uhb_spawn.ENV_PATH, str(root))
    monkeypatch.setattr(uhb_spawn, "port_open", lambda *a, **k: False)
    proc = FakeProc()
    uhb_spawn.ensure(runner=lambda *a: proc, checker=lambda url, timeout: True)

    def refuses(timeout=None):
        raise subprocess.TimeoutExpired(cmd="uhb", timeout=timeout or 0)

    monkeypatch.setattr(proc, "wait", refuses)
    uhb_spawn.stop_running(timeout=0.01)

    assert proc.killed is True


def test_a_real_spawn_of_a_missing_binary_is_a_spawn_error(tmp_path: Path) -> None:
    log = tmp_path / "uhb.log"
    with pytest.raises(uhb_spawn.SpawnError) as caught:
        uhb_spawn._spawn(["definitely-not-a-binary-uh"], tmp_path, log)

    assert "cannot start UHB" in str(caught.value)
    assert log.exists(), "the log is opened before the spawn, so a failure leaves a trace"


# --------------------------------------------------------------------------- #
# the two callers
# --------------------------------------------------------------------------- #
def _web_args(**overrides) -> argparse.Namespace:
    base = dict(
        host=None,
        port=8765,
        no_open=True,
        allow_terminal=False,
        workspace=None,
        static_dir=None,
        no_uhb=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_uh_web_starts_uhb_by_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import uh.web.app as app_module

    monkeypatch.setattr(app_module, "run_server", lambda **kwargs: 0)
    started = uhb_spawn.Spawned(
        process=FakeProc(), url=URL, log=Path("C:/tmp/uhb.log"), checkout=Path("C:/tmp")
    )
    calls: list = []

    def fake_ensure():
        calls.append("ensure")
        return started

    monkeypatch.setattr(uhb_spawn, "ensure", fake_ensure)
    stopped: list = []
    monkeypatch.setattr(uhb_spawn, "stop_running", lambda: stopped.append(True) or True)

    assert cli.cmd_web(_web_args()) == 0

    assert calls == ["ensure"]
    assert stopped == [True], "the child stops with the server, whatever ended it"
    assert "UHB started" in capsys.readouterr().out


def test_no_uhb_flag_never_touches_uhb(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import uh.web.app as app_module

    monkeypatch.setattr(app_module, "run_server", lambda **kwargs: 0)
    calls: list = []
    monkeypatch.setattr(uhb_spawn, "ensure", lambda: calls.append("ensure") or None)

    assert cli.cmd_web(_web_args(no_uhb=True)) == 0

    assert calls == []
    assert "UHB" not in capsys.readouterr().out


def test_a_failed_spawn_is_a_warning_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import uh.web.app as app_module

    monkeypatch.setattr(app_module, "run_server", lambda **kwargs: 0)

    def boom():
        raise uhb_spawn.SpawnError("no UHB checkout found")

    monkeypatch.setattr(uhb_spawn, "ensure", boom)

    assert cli.cmd_web(_web_args()) == 0, "the UI still starts: UHB is optional"
    assert "UHB not started" in capsys.readouterr().err


def _session(tmp_path: Path) -> uh_console.ChatSession:
    class Stub:
        model = "stub:1b"

        def chat(self, messages, tools, **kwargs):
            return {"role": "assistant", "content": "ok", "tool_calls": []}

    return uh_console.ChatSession(
        provider_factory=lambda kind, model: Stub(),
        provider_kind="stub",
        chain=[("stub", Stub())],
        model="stub:1b",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=True,
        store=None,
        max_iterations=2,
    )


def test_slash_web_reports_the_started_uhb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh.web import embedded

    class FakeServer:
        url = "http://127.0.0.1:8765/?token=t"
        alive = True

    monkeypatch.setattr(embedded, "start_in_background", lambda **kw: (FakeServer(), True))
    started = uhb_spawn.Spawned(
        process=FakeProc(), url=URL, log=Path("C:/tmp/uhb.log"), checkout=Path("C:/tmp")
    )
    calls: list = []

    def fake_ensure():
        calls.append("ensure")
        return started

    monkeypatch.setattr(uhb_spawn, "ensure", fake_ensure)

    result = commands.dispatch(_session(tmp_path), "/web")

    assert calls == ["ensure"]
    assert "UHB: started (pid 4242)" in result.out
    assert result.err == ""


def test_slash_web_says_why_uhb_is_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from uh.web import embedded

    class FakeServer:
        url = "http://127.0.0.1:8765/?token=t"
        alive = True

    monkeypatch.setattr(embedded, "start_in_background", lambda **kw: (FakeServer(), True))

    def boom():
        raise uhb_spawn.SpawnError("no UHB checkout found")

    monkeypatch.setattr(uhb_spawn, "ensure", boom)

    result = commands.dispatch(_session(tmp_path), "/web")

    assert "web UI: http://127.0.0.1:8765/?token=t" in result.out
    assert "UHB: not started (no UHB checkout found)" in result.out
    assert result.err == "", "the UI still runs: this is a note, not an error"


# --------------------------------------------------------------------------- #
# real subprocesses (Windows-only): the job object and a clean kill
# --------------------------------------------------------------------------- #
def test_job_for_never_raises_on_a_bogus_process() -> None:
    assert uhb_spawn._job_for(object()) is None  # type: ignore[arg-type]


@pytest.mark.skipif(__import__("os").name != "nt", reason="the job object is a Windows API")
def test_a_real_child_is_killed_and_leaves_no_orphan(tmp_path: Path) -> None:
    """A real process, a real job object, a real stop: no fake Popen in sight."""
    import time

    log = tmp_path / "child.log"
    process = uhb_spawn._spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, log
    )
    try:
        assert process.poll() is None, "the child must be alive right after the spawn"
        assert process.pid in uhb_spawn._JOBS, "the child is in the kill-on-close job"
        child = uhb_spawn.Spawned(
            process=process, url=URL, log=log, checkout=tmp_path
        )
        child.stop(timeout=5.0)
        assert process.pid not in uhb_spawn._JOBS, "the job handle is closed with the child"
        deadline = time.monotonic() + 5.0
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert process.poll() is not None, "terminate() must really end the child"
    finally:
        if process.poll() is None:  # never leave a sleep behind
            process.kill()
