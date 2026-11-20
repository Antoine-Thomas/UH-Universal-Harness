"""The isolation guards, measured on their effect.

A test that reads the guard's source would protect it for as long as the source
looks right. These call the guarded operations and assert the refusal: the real
``~/.uh`` is off limits to the suite, and the module under test is what says so.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from uh import config as uh_config


def _real_home() -> Path:
    return Path.home() / ".uh"


def test_the_home_in_use_is_never_the_real_one(tmp_path: Path) -> None:
    resolved = uh_config.uh_home()
    assert resolved != _real_home().resolve()
    assert _real_home().resolve() not in resolved.parents


def test_a_file_write_into_the_real_home_is_refused() -> None:
    target = _real_home() / "isolation-proof.tmp"
    with pytest.raises(AssertionError, match=r"real ~/\.uh"):
        target.write_text("x", encoding="utf-8")
    assert not target.exists(), "the guard must refuse before the write lands"


def test_a_deletion_in_the_real_home_is_refused() -> None:
    # The path need not exist: the guard runs before the syscall, which is the
    # point - a teardown that deletes the operator's file is stopped there.
    target = _real_home() / "isolation-proof-absent.tmp"
    with pytest.raises(AssertionError, match=r"real ~/\.uh"):
        os.remove(target)
    with pytest.raises(AssertionError, match=r"real ~/\.uh"):
        Path(target).unlink(missing_ok=True)


def test_the_ollama_url_is_pinned_away_from_the_developers_server() -> None:
    url = os.environ["UH_OLLAMA_URL"]
    assert url != "http://127.0.0.1:11434"
    port = int(url.rsplit(":", 1)[1])
    with socket.socket() as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex(("127.0.0.1", port)) != 0, "the pinned port must be closed"
