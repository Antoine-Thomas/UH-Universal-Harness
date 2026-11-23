"""Le plan vocal : une décision pure, et l'interdiction de sortir sans clé.

Les cas testés sont ceux du terrain : Voxtral 429, faster-whisper absent, machine
8 Go. Aucun de ces tests ne parle au réseau ni n'installe quoi que ce soit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import voice_plan
from uh.audio import AudioUnavailable

KEY = {"voxtral": "sk-" + "x" * 40}
NO_KEY = {"voxtral": ""}
STT = {"faster_whisper": "1.2.1"}
BOTH = {"faster_whisper": "1.2.1", "xtts": True, "piper": True}
GPU8 = {
    "tier": "xl",
    "gpu": "RTX 3070 Ti",
    "vram_gb": 8.0,
    "free_vram_mb": 3072,
    "ram_gb": 32.0,
}
CPU16 = {"tier": "xl", "gpu": "", "vram_gb": 0.0, "ram_gb": 16.0}


def test_local_profile_never_picks_the_cloud_engine() -> None:
    plan = voice_plan.choose_voice_engines("local", KEY, BOTH, GPU8)
    assert plan["stt"]["engine"] == "faster-whisper"
    assert plan["tts"]["engine"] in {"xtts", "piper"}
    assert plan["network_allowed"] is False


def test_a_missing_key_means_no_cloud_engine_at_all() -> None:
    plan = voice_plan.choose_voice_engines("cloud", NO_KEY, BOTH, GPU8)
    assert plan["stt"]["engine"] != "voxtral"
    assert plan["tts"]["engine"] != "voxtral"
    assert plan["network_allowed"] is False


def test_without_any_engine_the_plan_says_so_and_gives_the_command() -> None:
    plan = voice_plan.choose_voice_engines("auto", NO_KEY, {}, GPU8)
    assert plan["stt"]["engine"] is None
    assert "faster-whisper" in plan["stt"]["reason"]
    targets = {item["target"] for item in plan["install"]}
    assert "faster-whisper" in targets
    command = next(
        item["command"] for item in plan["install"] if item["target"] == "faster-whisper"
    )
    assert "pip install" in command and "faster-whisper" in command


def test_auto_prefers_the_local_engine_when_it_is_installed() -> None:
    """Dictée locale dès qu'elle existe ; la voix, elle, suit ce qui est installé."""
    plan = voice_plan.choose_voice_engines("auto", KEY, STT, GPU8)
    assert plan["stt"]["engine"] == "faster-whisper"
    # Aucune voix locale provisionnée : le TTS reste au cloud, avec une clé qui existe.
    assert plan["tts"]["kind"] == "cloud"
    assert plan["network_allowed"] is True

    everything_local = voice_plan.choose_voice_engines("auto", KEY, BOTH, GPU8)
    assert everything_local["stt"]["kind"] == "local"
    assert everything_local["tts"]["kind"] == "local"
    assert everything_local["network_allowed"] is False


def test_cloud_profile_uses_the_cloud_only_with_a_key() -> None:
    plan = voice_plan.choose_voice_engines("cloud", KEY, {}, GPU8)
    assert plan["stt"]["engine"] == "voxtral"
    assert plan["network_allowed"] is True


def test_local_profile_without_local_engines_refuses_explicitly() -> None:
    plan = voice_plan.choose_voice_engines("local", KEY, {}, GPU8)
    assert plan["stt"]["engine"] is None
    assert "locale" in plan["stt"]["reason"]
    assert plan["network_allowed"] is False


def test_the_model_size_follows_the_measured_vram_when_cuda_really_works() -> None:
    """8 Go moins les 5 Go du chat 7B : `small`, pas `medium`."""
    winning = {"gpu_wins": True}
    assert voice_plan.local_stt_model(GPU8, cuda=True, bench=winning) == ("small", "int8_float16")
    big = {"tier": "xl", "gpu": "RTX 4090", "vram_gb": 24.0, "free_vram_mb": 12288, "ram_gb": 64.0}
    assert voice_plan.local_stt_model(big, cuda=True, bench=winning) == (
        "medium",
        "int8_float16",
    )
    assert voice_plan.local_stt_model(CPU16, cuda=False) == ("base", "int8")
    small = {"tier": "medium", "gpu": "", "ram_gb": 8.0}
    assert voice_plan.local_stt_model(small, cuda=False) == ("tiny", "int8")


def test_a_measured_vram_under_the_threshold_keeps_the_dictation_on_cpu() -> None:
    """La règle du panneau est celle de la dictée : sous 1,5 Go, CPU — et le chiffre est dit."""
    tight = dict(GPU8, free_vram_mb=900)
    plan = voice_plan.choose_voice_engines(
        "auto", NO_KEY, dict(STT, cuda=True), tight, {"gpu_wins": True}
    )
    assert plan["stt"]["compute"] == "int8"
    assert "0,9 Go libres" in plan["stt"]["reason"]
    assert "1,5 Go" in plan["stt"]["reason"], plan["stt"]["reason"]
    assert plan["machine"]["free_vram_mb"] == 900


def test_with_just_enough_vram_the_gpu_takes_the_smallest_model() -> None:
    """Entre 1,5 et 2,5 Go : la carte reste utilisable, pour le plus petit modèle."""
    just_enough = dict(GPU8, free_vram_mb=2008)
    assert voice_plan.local_stt_model(just_enough, cuda=True, bench={"gpu_wins": True}) == (
        "base",
        "int8_float16",
    )


def test_an_absent_vram_measure_refuses_the_gpu_in_the_plan_too() -> None:
    """Même règle que `whisper_device` : une mesure absente vaut refus, jamais une promesse."""
    unmeasured = {"tier": "xl", "gpu": "RTX 3070 Ti", "vram_gb": 8.0, "ram_gb": 32.0}
    plan = voice_plan.choose_voice_engines(
        "auto", NO_KEY, dict(STT, cuda=True), unmeasured, {"gpu_wins": True}
    )
    assert plan["stt"]["compute"] == "int8"
    assert "non mesurée" in plan["stt"]["reason"]


def test_a_visible_gpu_without_cuda_stays_on_cpu() -> None:
    """Mesuré ici : `cublas64_12.dll` absent, donc promettre du GPU ferait échouer."""
    assert voice_plan.local_stt_model(GPU8, cuda=False) == ("base", "int8")
    plan = voice_plan.choose_voice_engines("auto", NO_KEY, STT, GPU8)
    assert plan["stt"]["compute"] == "int8"
    assert "CPU" in plan["stt"]["reason"]
    assert "CUDA" in plan["stt"]["reason"]


def test_the_chat_model_vram_is_deducted_not_ignored() -> None:
    assert voice_plan.chat_vram_gb(GPU8) == 5.0
    assert voice_plan.free_vram_gb(GPU8) == 3.0
    assert voice_plan.chat_vram_gb(CPU16) == 0.0


def test_the_plan_never_carries_the_key() -> None:
    plan = voice_plan.choose_voice_engines("cloud", KEY, BOTH, GPU8)
    blob = voice_plan.dump(plan)
    assert "sk-" not in blob
    assert "x" * 40 not in blob


def test_the_installation_report_states_each_missing_piece() -> None:
    versions = {
        "python": "3.14.9",
        "uh": "1.9.2",
        "uhb": None,
        "ollama": "ollama version 0.12.3",
        "faster_whisper": None,
        "xtts": False,
        "piper": False,
        "ffmpeg": True,
        "browser": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "browser_name": "Edge",
    }
    rows = {row["name"]: row for row in voice_plan.installation_report(versions)}
    assert rows["Python"]["state"] == "ok"
    assert rows["UH"]["state"] == "ok"
    assert rows["UHB"]["state"] == "missing" and rows["UHB"]["command"]
    assert rows["faster-whisper"]["state"] == "missing"
    assert "pip install" in rows["faster-whisper"]["command"]
    assert rows["Edge"]["state"] == "ok"


def test_an_old_python_is_flagged_for_update() -> None:
    rows = {row["name"]: row for row in voice_plan.installation_report({"python": "3.8.10"})}
    assert rows["Python"]["state"] == "update"


def test_no_voice_call_leaves_the_machine_without_a_key(monkeypatch, tmp_path) -> None:
    """Sans clé, `transcribe` ne doit appeler aucun moteur cloud — même pour essayer."""
    from uh import audio

    monkeypatch.setenv("UH_HOME", str(tmp_path))
    monkeypatch.setattr(audio, "mistral_key", lambda: "")
    monkeypatch.setattr(audio.local, "whisper_available", lambda: False)
    called: list[str] = []

    def forbidden(*args, **kwargs):
        called.append("voxtral")
        raise AssertionError("aucun appel réseau ne doit partir sans clé")

    monkeypatch.setattr(audio.voxtral, "transcribe", forbidden)
    with pytest.raises(AudioUnavailable) as error:
        audio.transcribe(b"\x00" * 2048, filename="clip.webm", session_id="t")
    assert called == []
    assert "aucun moteur" in str(error.value)


def test_the_install_command_names_the_interpreter_running_the_server(monkeypatch) -> None:
    """Un `pip` du PATH installerait dans un autre Python que celui du serveur."""
    monkeypatch.setattr(voice_plan.sys, "executable", r"C:\autre\venv\Scripts\python.exe")
    command = voice_plan._install_stt_command()
    assert command.startswith('"C:\\autre\\venv\\Scripts\\python.exe" -m pip install'), command
    assert "faster-whisper" in command


def test_a_server_started_from_another_venv_names_that_venv(tmp_path) -> None:
    """Preuve réelle : un second interpréteur affiche SA commande, pas la nôtre."""
    import os
    import subprocess
    import sys
    import sysconfig

    repo = Path(__file__).resolve().parents[1]
    venv_dir = tmp_path / "autre-venv"
    made = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if made.returncode != 0:
        pytest.skip(f"venv secondaire impossible ici : {made.stderr.strip()[:120]}")
    other = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not other.exists():
        pytest.skip("interpréteur du venv secondaire introuvable")
    # Ce venv est nu : on lui rend visibles les paquets du venv du dépôt, pour que
    # `import uh` fonctionne tel quel, sans rien installer.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(repo), sysconfig.get_paths()["purelib"]])
    code = (
        "import sys; from uh import voice_plan; "
        "print(sys.executable); print(voice_plan._install_stt_command())"
    )
    run = subprocess.run(
        [str(other), "-c", code], env=env, capture_output=True, text=True, encoding="utf-8"
    )
    if run.returncode != 0:
        pytest.skip(f"le second interpréteur n'importe pas le module : {run.stderr.strip()[:160]}")
    lines = [line.strip() for line in run.stdout.strip().splitlines()]
    assert lines[0] == str(other)
    assert lines[1].startswith(f'"{other}" -m pip install'), lines[1]


def test_the_plan_names_this_interpreter_and_no_path_pip() -> None:
    """Invariant : toute commande `pip` du plan vise CE python, jamais le PATH."""
    import sys

    plan = voice_plan.plan_for_home("auto")
    assert plan["python_executable"] == sys.executable
    for item in plan["install"]:
        if "pip install" in item["command"]:
            assert item["command"].startswith(f'"{sys.executable}"'), item["command"]
    for row in plan["installation"]:
        if "pip install" in row["command"]:
            assert row["command"].startswith(f'"{sys.executable}"'), row["command"]
