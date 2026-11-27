"""Le délai XTTS se calcule, et un timeout tue l'ARBRE — par le chemin réel.

Mesures du 27/11 (RTX 3070 Ti, texte neutre) : ~25 s de chargement, ~35 ms par
caractère, 2 000 caractères ≈ 100 s. `subprocess.run(timeout=)` tue le seul
processus direct : un petit-enfant laissé vivant garde la VRAM. Ces tests passent
par ``xtts_speak`` lui-même, avec un faux script Python qui lance un enfant dormant.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from uh.audio import local

FAKE_SCRIPT = """
import os, subprocess, sys, time
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
Path(os.environ["UH_TEST_CHILD_FILE"]).write_text(str(child.pid), encoding="utf-8")
Path(sys.argv[2]).write_text("pas un wav", encoding="utf-8")
time.sleep(120)
"""


def test_the_delay_comes_from_the_measurements() -> None:
    assert local.speech_timeout("") == pytest.approx(60.0)
    assert local.speech_timeout("x" * 2000) == pytest.approx(180.0)
    assert local.speech_timeout("x" * 100) == pytest.approx(66.0)


def test_the_delay_is_capped_and_never_out_of_order() -> None:
    assert local.speech_timeout("x" * 100_000) == pytest.approx(local.XTTS_TIMEOUT_CAP)
    values = [local.speech_timeout("x" * size) for size in (0, 10, 500, 2000, 9000)]
    assert values == sorted(values)


def _alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/fi", f"PID eq {pid}", "/nh"], capture_output=True, timeout=30
    ).stdout.decode("cp850", "replace")
    return str(pid) in out


def _fake_xtts(monkeypatch: pytest.MonkeyPatch, script: str, delay: float = 30.0) -> None:
    monkeypatch.setattr(local, "_XTTS_SCRIPT", script)
    monkeypatch.setattr(local, "xtts_python", lambda: sys.executable)
    monkeypatch.setattr(local, "xtts_model_dir", lambda: None)
    monkeypatch.setattr(local, "speech_timeout", lambda text: delay)


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill /T est la preuve Windows")
def test_a_timeout_kills_the_whole_tree_and_names_the_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le chemin réel : ``xtts_speak`` → Popen → dépassement → arbre tué."""
    child_file = tmp_path / "child.pid"
    monkeypatch.setenv("UH_TEST_CHILD_FILE", str(child_file))
    _fake_xtts(monkeypatch, FAKE_SCRIPT, delay=3.0)

    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match=r"dépassé 3 s"):
            local.xtts_speak("bonjour")
        elapsed = time.monotonic() - started

        assert elapsed < 60, f"le timeout doit couper court, mesuré {elapsed:.1f} s"
        assert child_file.is_file(), "le faux script n'a pas démarré"
        child = int(child_file.read_text(encoding="utf-8").strip())
        assert not _alive(child), "un petit-enfant survivant garderait la VRAM"
    finally:
        # Si l'assertion échoue, le produit n'a pas tué l'enfant : le test le tue
        # lui-même, sinon un dormeur de 120 s survivrait à la suite.
        if child_file.is_file():
            survivor = int(child_file.read_text(encoding="utf-8").strip())
            if _alive(survivor):
                local.kill_tree(survivor)


def test_a_failure_names_the_stderr_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    failing = """
import sys
sys.stderr.write("CUDA out of memory (simulation)")
sys.exit(3)
"""
    _fake_xtts(monkeypatch, failing)

    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        local.xtts_speak("bonjour")


def test_a_success_returns_the_wav(monkeypatch: pytest.MonkeyPatch) -> None:
    good = """
import sys
from pathlib import Path
Path(sys.argv[2]).write_bytes(b"RIFF0000WAVE")
"""
    _fake_xtts(monkeypatch, good)

    assert local.xtts_speak("bonjour").startswith(b"RIFF")


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill /T est la preuve Windows")
def test_killing_a_tree_leaves_no_child_behind() -> None:
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    child_pid = int(parent.stdout.readline().strip())

    assert _alive(parent.pid) and _alive(child_pid), (
        "les deux processus doivent vivre avant le test"
    )

    local.kill_tree(parent.pid)
    parent.wait(timeout=30)

    assert not _alive(parent.pid)
    assert not _alive(child_pid), "un enfant survivant garderait la VRAM"
