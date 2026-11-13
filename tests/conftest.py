"""Suite-wide guards.

The autouse fixture below exists because a test once wrote a fake provider key
into the developer's real ``~/.uh/keys.toml``: it created a router provider
without isolating ``UH_HOME``. Isolation is now the default, and the session
fixture fails the run if anything escapes it.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _snapshot(directory: Path) -> dict[str, tuple[int, int]]:
    if not directory.is_dir():
        return {}
    return {
        item.name: (item.stat().st_mtime_ns, item.stat().st_size)
        for item in directory.iterdir()
        if item.is_file() and not item.name.startswith("uh.db")
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
    return target


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
    """Fail the whole run if a test wrote credentials or memory into ``~/.uh``.

    The store is excluded from the snapshot on purpose: a live ``uh`` session in
    another window keeps writing its WAL, and a guard that trips on the
    developer's own chat window is one everybody learns to ignore. Store leaks
    are caught precisely by :func:`no_real_store` instead.
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
