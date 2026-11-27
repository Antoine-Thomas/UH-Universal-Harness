"""Suite-wide guards.

The autouse fixture below exists because a test once wrote a fake provider key
into the developer's real ``~/.uh/keys.toml``: it created a router provider
without isolating ``UH_HOME``. Isolation is now the default, and the session
fixture fails the run if anything escapes it.

``UH_OLLAMA_URL`` is pinned for a second, measured reason (2026-11-20): a test
that builds a real ``ChatSession`` probes the model's capabilities at the default
``http://127.0.0.1:11434``, so the suite talked to the developer's own Ollama.
Nothing was written by the test, but ``ollama serve`` had been started by their
``uh web`` window with its stdout redirected to ``~/.uh/uhb.log`` - so every
probe appended a line to that file, and the push gate read it as a test leaking
into the real home. A closed loopback port makes the probe fail at once instead.
"""

from __future__ import annotations

import os
import re
import socket
import sys
from pathlib import Path

import pytest


def _closed_url() -> str:
    """A loopback URL nothing listens on: a stray probe is refused instantly."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


#: The names a live process of the developer's rewrites, **name for name**.
#: `uh-web.log.1` is the product rotating its own journal (a numeric suffix the
#: product adds), and `uh.db-wal`/`uh.db-shm` are SQLite's sidecars of a WAL store.
#: Everything else is matched exactly: `uhb.pid.bak` is an ordinary file and it is
#: seen. A prefix match (`startswith`) exempted anything that began with one of
#: these names, which is how a backup of the pid slipped through (review of #79).
_VOLATILE_NAME = re.compile(
    r"(?:uh\.db(?:-(?:wal|shm|journal))?"
    r"|dream-daemon\.status"
    r"|uh-web\.log(?:\.\d+)?"
    r"|uhb\.log(?:\.\d+)?"
    r"|uhb\.pid"
    r"|uh-web\.ps1"
    r"|uh-web\.ico)"
)


def _snapshot(directory: Path) -> dict[str, tuple[int, int]]:
    if not directory.is_dir():
        return {}
    # Written by a live process of the developer's, not by a test: the session
    # store (any `uh` window), the dream daemon's heartbeat, the two logs, the pid
    # a live `uh web` creates and deletes on every start, and the launcher/icon any
    # `uh web` start regenerates when it was built by an older version. The guard is
    # about credentials and memory landing in the real home; a file the product
    # itself rewrites on start would make it fire on the user's own window, which
    # teaches everybody to ignore it. The list lives in `_VOLATILE_NAME`, matched
    # exactly (see the comment there for why not a prefix).
    return {
        item.name: (item.stat().st_mtime_ns, item.stat().st_size)
        for item in directory.iterdir()
        if item.is_file() and not _VOLATILE_NAME.fullmatch(item.name)
    }


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own ``$UH_HOME``; nothing touches the real one."""
    target = tmp_path / "uh-home"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UH_HOME", str(target))
    monkeypatch.delenv("UH_PROVIDER_KEY", raising=False)
    # No test probes a real UHB: the uhb toolset is opt-in per test
    # (it sets UH_UHB_URL itself and stubs the probe).
    monkeypatch.setenv("UH_UHB_URL", "")
    # No test probes the developer's own Ollama either - see the module
    # docstring: that probe is what the push gate caught writing into
    # ~/.uh/uhb.log, through a log file their live `uh web` owns.
    monkeypatch.setenv("UH_OLLAMA_URL", _closed_url())
    return target


@pytest.fixture(scope="session", autouse=True)
def real_home_is_forbidden() -> None:
    """Refuse, at the call, any write into the developer's real ``~/.uh``.

    The snapshot in :func:`real_home_untouched` only reports at the end of the
    session, cannot say *which* test did it, and cannot tell a deletion from a
    file that a live window cleaned up. An audit hook answers the question the
    push gate actually asks - "did the suite touch my folder?" - before the
    call succeeds, and it catches paths the guard never enumerated.
    """
    real = (Path.home() / ".uh").resolve()
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

    def offending(value: object) -> str | None:
        try:
            candidate = Path(os.fspath(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if not candidate.is_absolute():
            return None
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if resolved == real or real in resolved.parents:
            return str(resolved)
        return None

    def writable(mode: object, flags: object) -> bool:
        if isinstance(mode, str) and mode:
            return any(flag in mode for flag in "wax+")
        return isinstance(flags, int) and bool(flags & write_flags)

    def hook(event: str, args: tuple) -> None:
        if event == "open":
            path, mode, flags = (tuple(args) + (None, None, None))[:3]
            if not writable(mode, flags):
                return
            targets = [path]
        elif event in {"os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.truncate"}:
            targets = list(args)[:2] if event == "os.rename" else list(args)[:1]
        elif event == "shutil.rmtree":
            targets = list(args)[:1]
        else:
            return
        for target in targets:
            found = offending(target)
            if found is not None:
                raise AssertionError(
                    f"a test reached the real ~/.uh ({event}, {found}); "
                    "isolation leak - use tmp_path"
                )

    sys.addaudithook(hook)
    yield


@pytest.fixture(autouse=True)
def pinned_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin what ``uh dream --schedule`` resolves as the task's interpreter.

    Resolution probes the real machine - correct in production, unusable in a
    test: CI has no ``Python310``, and the answer changes with whatever this
    laptop has installed. Tests that exercise the resolution itself pass
    ``candidates``/``probe`` explicitly and still run the real function.
    """
    from uh import dream_schedule

    path = tmp_path / "Programs" / "Python" / "Python310" / "python.exe"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    monkeypatch.setattr(dream_schedule, "_system_pythons", lambda: [str(path)])
    monkeypatch.setattr(dream_schedule, "_has_uh", lambda exe: True)
    monkeypatch.delenv(dream_schedule.ENV_INTERPRETER, raising=False)
    return str(path)


@pytest.fixture(scope="session", autouse=True)
def real_home_untouched():
    """Fail the whole run if a test wrote a top-level file into ``~/.uh``.

    The store is excluded from the snapshot on purpose: a live ``uh`` session in
    another window keeps writing its WAL, and a guard that trips on the
    developer's own chat window is one everybody learns to ignore. Store leaks
    are caught precisely by :func:`no_real_store` instead.

    What it sees is the first level of ``~/.uh`` and nothing deeper: a file
    dropped under ``memory/`` does not change the snapshot (measured in
    ``tests/test_real_home_snapshot.py``). Without that limit a live session
    writing its own memory during a push gate would trip it - the very false
    positive this guard exists to remove.
    """
    real = Path.home() / ".uh"
    before = _snapshot(real)
    yield
    after = _snapshot(real)
    assert before == after, (
        "a test wrote into the real ~/.uh (isolation leak): "
        f"before={sorted(before)} after={sorted(after)}"
    )


@pytest.fixture(scope="session", autouse=True)
def no_real_store():
    """Fail the whole run if a test opens the developer's real session database.

    A filesystem snapshot cannot answer that question - the developer's own
    running ``uh`` writes the same files - so the constructor is wrapped: every
    ``Store`` built during the run must resolve under an isolated ``UH_HOME``.
    """
    from uh.store import Store

    real = (Path.home() / ".uh").resolve()
    opened: list[str] = []
    original = Store.__init__

    def guarded(self, path=None, *args, **kwargs) -> None:
        try:
            from uh.store import default_store_path

            candidate = Path(path).expanduser().resolve() if path else default_store_path()
        except (OSError, RuntimeError):
            candidate = None
        if candidate is not None and (candidate == real or real in candidate.parents):
            opened.append(str(candidate))
        original(self, path, *args, **kwargs)

    Store.__init__ = guarded  # type: ignore[method-assign]
    try:
        yield opened
    finally:
        Store.__init__ = original  # type: ignore[method-assign]
        assert not opened, f"a test opened the real store: {sorted(set(opened))[:3]}"
