"""P1-7, measured on the effect: quit UH, and the real UHB is gone.

The unit tests prove the path - a 202, a refusal, a missing token. This one proves
the *outcome*: a real `uhb run` in a subprocess, none of it owned by
`uhb_spawn`, stopped only because the route accepted the request. If the route
were not wired, the process would still be serving and the port still open.

Skips when the UHB checkout is not on this machine: the test needs both halves.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest

from uh import uhb_spawn

UHB = uhb_spawn.find_checkout()
pytestmark = pytest.mark.skipif(UHB is None, reason="UHB checkout not found on this machine")

#: Known to the test and to the child through the environment - the first of the
#: three names `shutdown_token()` resolves. It is not a secret of the operator's.
TOKEN = "p1-7-integration-token"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait(predicate, *, timeout: float = 60.0, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_quitting_stops_a_real_uhb_through_its_route(monkeypatch, capsys) -> None:
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("UH_UHB_URL", url)
    monkeypatch.setenv(uhb_spawn.SHUTDOWN_TOKEN_ENV, TOKEN)
    # The test must not touch the operator's pid file: a recorded UHB there belongs
    # to their session, not to this proof.
    monkeypatch.setattr(uhb_spawn, "stop_recorded", lambda *a, **k: False)
    monkeypatch.setattr(uhb_spawn, "_RUNNING", None)

    child = subprocess.Popen(
        [sys.executable, "-m", "uhb.entrypoints.cli", "run", "--mode", "dev", "--port", str(port)],
        cwd=str(UHB),
        env={**os.environ, uhb_spawn.SHUTDOWN_TOKEN_ENV: TOKEN},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert wait(lambda: uhb_spawn.port_open(url)), "UHB never came up on its port"

        # The act under test. Nothing local will kill the child: `_RUNNING` is None,
        # so only the route can end it.
        assert uhb_spawn.stop_running() is True, "stop_running reported nothing to stop"
        assert wait(lambda: not uhb_spawn.port_open(url), timeout=15), (
            "the port is still served: the route did not stop the server"
        )
        assert wait(lambda: child.poll() is not None, timeout=15), (
            "the server stopped listening but the process is still alive"
        )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)

    captured = capsys.readouterr()
    written = captured.out + captured.err
    assert TOKEN not in written, "the token reached a log"
    assert "token=" not in written, "a query string carrying a token reached a log"


def test_without_the_route_the_server_keeps_serving(monkeypatch) -> None:
    """The control: same setup, route declined - and UHB stays up.

    Without this, the test above could pass because *something else* ended the
    process. Here nothing else can: the child is not `_RUNNING`, the pid file is
    ignored, and `request_shutdown` answers False.
    """
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("UH_UHB_URL", url)
    monkeypatch.setenv(uhb_spawn.SHUTDOWN_TOKEN_ENV, TOKEN)
    monkeypatch.setattr(uhb_spawn, "stop_recorded", lambda *a, **k: False)
    monkeypatch.setattr(uhb_spawn, "_RUNNING", None)
    monkeypatch.setattr(uhb_spawn, "request_shutdown", lambda *a, **k: False)

    child = subprocess.Popen(
        [sys.executable, "-m", "uhb.entrypoints.cli", "run", "--mode", "dev", "--port", str(port)],
        cwd=str(UHB),
        env={**os.environ, uhb_spawn.SHUTDOWN_TOKEN_ENV: TOKEN},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert wait(lambda: uhb_spawn.port_open(url)), "UHB never came up on its port"

        assert uhb_spawn.stop_running() is False, "nothing to stop, so: False"
        assert wait(lambda: uhb_spawn.port_open(url), timeout=3), (
            "the server stopped without the route: something else killed it"
        )
        assert child.poll() is None, "the process ended without being asked"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
