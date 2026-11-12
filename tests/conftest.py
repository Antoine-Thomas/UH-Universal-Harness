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
        if item.is_file()
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


@pytest.fixture(scope="session", autouse=True)
def real_home_untouched():
    """Fail the whole run if a test wrote into the developer's ``~/.uh``."""
    real = Path.home() / ".uh"
    before = _snapshot(real)
    yield
    after = _snapshot(real)
    assert before == after, (
        "a test wrote into the real ~/.uh (isolation leak): "
        f"before={sorted(before)} after={sorted(after)}"
    )
