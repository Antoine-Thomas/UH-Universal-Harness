"""La dictée sur GPU : ce qu'on annonce avant le clic, et ce qu'on vérifie après.

Aucun de ces tests n'installe, ne télécharge ni ne charge un modèle : les parties qui
touchent la machine sont remplacées, et les décisions sont vérifiées sur des entrées
connues. Le seul test qui parle à l'index PyPI est écarté du réseau par un faux
``urlopen``.
"""

from __future__ import annotations

import sys

from uh import voice_gpu, voice_plan

GPU8 = {"tier": "xl", "gpu": "NVIDIA GeForce RTX 3070 Ti", "vram_gb": 8.0, "ram_gb": 64.0}


def test_the_three_required_libraries_are_probed_not_assumed() -> None:
    """Une carte visible n'est pas une DLL chargée : les trois sont essayées."""
    found = voice_gpu.libs()
    assert set(found) == set(voice_gpu.GPU_LIBS)
    assert all(isinstance(value, bool) for value in found.values())


def test_the_install_command_names_the_server_interpreter_and_pinned_wheels() -> None:
    command = voice_gpu.install_command()
    assert command.startswith(f'"{sys.executable}" -m pip install --no-deps')
    for package in voice_gpu.GPU_PACKAGES:
        assert package in command
        assert "==" in package, "un paquet non épinglé n'a pas sa place ici"


def test_the_step_does_not_exist_without_a_card() -> None:
    assert voice_gpu.setup(None) is None
    assert voice_gpu.setup({}) is None
    assert voice_gpu.setup({"gpu": ""}) is None


def test_the_step_disappears_once_the_three_libraries_load(monkeypatch) -> None:
    monkeypatch.setattr(voice_gpu, "libs", lambda: dict.fromkeys(voice_gpu.GPU_LIBS, True))
    assert voice_gpu.setup(GPU8) is None


def test_the_step_announces_the_source_the_size_and_the_command(monkeypatch) -> None:
    """Tout ce qui doit être lisible AVANT le clic : source, taille, disque, commande."""
    partial = dict.fromkeys(voice_gpu.GPU_LIBS, True)
    partial["cudnn64_9.dll"] = False
    monkeypatch.setattr(voice_gpu, "libs", lambda: partial)
    monkeypatch.setattr(voice_gpu, "driver_version", lambda: "572.16")
    monkeypatch.setattr(voice_gpu, "wheel_size", lambda package, version: "500 Mo")

    step = voice_gpu.setup(GPU8)
    assert step is not None
    assert step["show"] is True
    assert step["label"] == "Activer la dictée sur GPU"
    assert step["missing"] == ["cudnn64_9.dll"]
    assert step["driver"] == "572.16"
    assert "officiels" in step["source"] and "tiers" in step["source"]
    assert step["download"] == "500 Mo + 500 Mo"
    assert "Go" in step["disk"]
    assert step["command"].startswith(f'"{sys.executable}" -m pip install')
    assert step["packages"] == list(voice_gpu.GPU_PACKAGES)
    # L'archive tierce est proposée, mais étiquetée comme telle.
    assert "tiers" in step["third_party"]["note"]


def test_an_unreachable_index_gives_a_honest_size_and_no_exception(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("hors ligne")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    voice_gpu._WHEEL_SIZES.clear()
    assert voice_gpu.wheel_size("nvidia-cublas-cu12", "1.0") == ""
    voice_gpu._WHEEL_SIZES.clear()


def test_verify_reports_a_failure_instead_of_raising(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("interpréteur introuvable")

    monkeypatch.setattr(voice_gpu.subprocess, "run", boom)
    report = voice_gpu.verify()
    assert report["ok"] is False
    assert report["cuda"] is False
    assert report["steps"] and "introuvable" in report["steps"][0]["detail"]


def test_the_bench_never_lies_when_no_sample_can_be_made(monkeypatch) -> None:
    monkeypatch.setattr(voice_gpu, "sample", lambda seconds=10.0: (None, "échantillon impossible"))
    result = voice_gpu.bench()
    assert result["ok"] is False
    assert result["reason"] == "échantillon impossible"


def test_the_bench_verdict_is_written_and_reread(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    voice_gpu.write_bench({"ok": True, "gpu_wins": True, "reason": "GPU gagne"})
    assert (tmp_path / voice_gpu.BENCH_NAME).exists()
    assert voice_gpu.read_bench() == {"ok": True, "gpu_wins": True, "reason": "GPU gagne"}


def test_the_gpu_needs_a_winning_measurement_before_it_takes_over() -> None:
    """Sans mesure, le GPU ne prend pas la main : on ne promet pas une vitesse non constatée."""
    assert voice_plan.local_stt_model(GPU8, cuda=True) == ("base", "int8")
    assert voice_plan.local_stt_model(GPU8, cuda=True, bench={"gpu_wins": False}) == (
        "base",
        "int8",
    )
    assert voice_plan.local_stt_model(GPU8, cuda=True, bench={"gpu_wins": True}) == (
        "small",
        "int8_float16",
    )


def test_the_reason_says_the_gpu_is_ready_but_unmeasured() -> None:
    versions = {
        "faster_whisper": True,
        "xtts": True,
        "piper": False,
        "cuda": True,
        "voxtral_key": False,
    }
    plan = voice_plan.choose_voice_engines("auto", {"voxtral": ""}, versions, GPU8)
    assert plan["stt"]["compute"] == "int8"
    assert "aucune mesure gagnante" in plan["stt"]["reason"]
