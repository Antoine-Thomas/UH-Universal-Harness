"""Le délai d'un rendu XTTS se calcule, et un timeout tue l'arbre.

Mesures du 27/11 (RTX 3070 Ti, texte neutre) : ~25 s de chargement, ~35 ms par
caractère, 2 000 caractères ≈ 100 s. Les 900 s d'avant ne disaient rien et
bloquaient un quart d'heure ; un moteur laissé vivant garde la VRAM (un plantage
du harnais de mesure a laissé un runner et son enfant XTTS en vie).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from uh.audio import local


def test_the_delay_comes_from_the_measurements() -> None:
    assert local.speech_timeout("") == pytest.approx(60.0)
    assert local.speech_timeout("x" * 2000) == pytest.approx(180.0)
    assert local.speech_timeout("x" * 100) == pytest.approx(66.0)


def test_the_delay_is_capped_and_never_out_of_order() -> None:
    assert local.speech_timeout("x" * 100_000) == pytest.approx(local.XTTS_TIMEOUT_CAP)
    values = [local.speech_timeout("x" * size) for size in (0, 10, 500, 2000, 9000)]
    assert values == sorted(values)


def test_the_run_uses_the_computed_delay_not_nine_hundred_seconds(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    seen: dict = {}

    def boom(*args, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 0))

    monkeypatch.setattr(local, "subprocess", subprocess)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(local, "xtts_python", lambda: sys.executable)
    monkeypatch.setattr(local, "xtts_model_dir", lambda: None)

    text = "x" * 2000
    with pytest.raises(RuntimeError, match=r"dépassé 180 s"):
        local.xtts_speak(text)

    assert seen["timeout"] == pytest.approx(180.0)


def _alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/fi", f"PID eq {pid}", "/nh"], capture_output=True, timeout=30
    ).stdout.decode("cp850", "replace")
    return str(pid) in out


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill /T est la preuve Windows")
def test_killing_a_tree_leaves_no_child_behind() -> None:
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        child_pid = int(parent.stdout.readline().strip())
    finally:
        pass

    assert _alive(parent.pid) and _alive(child_pid), (
        "les deux processus doivent vivre avant le test"
    )

    local.kill_tree(parent.pid)
    parent.wait(timeout=30)

    assert not _alive(parent.pid)
    assert not _alive(child_pid), "un enfant survivant garderait la VRAM"
