"""La dictée sur GPU : ce qu'on annonce avant le clic, et ce qu'on vérifie après.

Aucun de ces tests n'installe, ne télécharge ni ne charge un modèle : les parties qui
touchent la machine sont remplacées, et les décisions sont vérifiées sur des entrées
connues. Le seul test qui parle à l'index PyPI est écarté du réseau par un faux
``urlopen``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from uh import voice_gpu, voice_plan

GPU8 = {
    "tier": "xl",
    "gpu": "NVIDIA GeForce RTX 3070 Ti",
    "vram_gb": 8.0,
    "free_vram_mb": 3072,
    "ram_gb": 64.0,
}


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


def test_dictation_stays_on_cpu_without_a_winning_bench(monkeypatch) -> None:
    """Une carte qui se charge ne suffit pas : sans mesure gagnante, c'est le CPU."""
    from uh.audio import local as stt

    monkeypatch.setattr(voice_gpu, "read_bench", lambda: None)
    monkeypatch.setattr(voice_gpu, "cuda_available", lambda: True)
    # La VRAM, elle, est large : ce test mesure la seule cause « banc », pas la VRAM.
    monkeypatch.setattr(voice_gpu, "free_vram_mb", lambda: 8000)
    assert stt.whisper_device() == ("cpu", "int8")


def test_dictation_takes_the_gpu_only_after_declaring_the_dll_directories(monkeypatch) -> None:
    """Windows ne voit pas les DLL d'un wheel : les dossiers passent avant le modèle."""
    from uh.audio import local as stt

    order: list[str] = []
    monkeypatch.setattr(voice_gpu, "read_bench", lambda: {"gpu_wins": True})
    monkeypatch.setattr(voice_gpu, "cuda_available", lambda: True)
    monkeypatch.setattr(voice_gpu, "free_vram_mb", lambda: 2000)
    monkeypatch.setattr(
        voice_gpu, "add_dll_directories", lambda: order.append("dll") or ["nvidia/cublas/bin"]
    )
    assert stt.whisper_device() == ("cuda", "int8_float16")
    assert order == ["dll"]


def test_free_vram_is_read_from_nvidia_smi(monkeypatch) -> None:
    """La garde lit ``memory.free``, pas ``memory.used`` : deux colonnes différentes."""
    calls: list[list[str]] = []

    class Result:
        stdout = "2008\n"

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return Result()

    monkeypatch.setattr(voice_gpu.subprocess, "run", fake_run)
    assert voice_gpu.free_vram_mb() == 2008
    assert calls and "--query-gpu=memory.free" in calls[0]


def test_an_unavailable_measure_is_none_and_never_zero(monkeypatch) -> None:
    """``nvidia-smi`` absent n'est pas « 0 Mo libres » : c'est « on ne sait pas »."""

    def boom(command, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(voice_gpu.subprocess, "run", boom)
    assert voice_gpu.free_vram_mb() is None
    assert voice_gpu.vram_text(None) == "VRAM libre non mesurée"


def test_the_guard_threshold_is_one_and_a_half_gigabyte() -> None:
    assert voice_gpu.MIN_FREE_VRAM_MB == 1536
    assert voice_gpu.vram_allows_dictation(900) is False
    assert voice_gpu.vram_allows_dictation(1535) is False
    assert voice_gpu.vram_allows_dictation(1536) is True
    assert voice_gpu.vram_allows_dictation(2000) is True
    assert voice_gpu.vram_allows_dictation(None) is False


def test_dictation_refuses_the_card_when_vram_is_short(monkeypatch) -> None:
    from uh.audio import local as stt

    monkeypatch.setattr(voice_gpu, "read_bench", lambda: {"gpu_wins": True})
    monkeypatch.setattr(voice_gpu, "cuda_available", lambda: True)
    monkeypatch.setattr(voice_gpu, "free_vram_mb", lambda: 900)
    assert stt.whisper_device() == ("cpu", "int8")


def test_dictation_refuses_the_card_without_a_measure(monkeypatch) -> None:
    from uh.audio import local as stt

    monkeypatch.setattr(voice_gpu, "read_bench", lambda: {"gpu_wins": True})
    monkeypatch.setattr(voice_gpu, "cuda_available", lambda: True)
    monkeypatch.setattr(voice_gpu, "free_vram_mb", lambda: None)
    assert stt.whisper_device() == ("cpu", "int8")


def test_a_vram_drop_between_two_dictations_falls_back_to_cpu(monkeypatch) -> None:
    """La garde est RELUE à chaque dictée : 2 000 Mo puis 900 Mo, et le second tour est CPU.

    C'est la preuve que la décision n'est pas prise une fois au démarrage : sans ce test,
    une garde mémorisée passerait tous les autres.
    """
    from uh.audio import local as stt

    readings = iter([2000, 900, 900])
    monkeypatch.setattr(voice_gpu, "read_bench", lambda: {"gpu_wins": True})
    monkeypatch.setattr(voice_gpu, "cuda_available", lambda: True)
    monkeypatch.setattr(voice_gpu, "add_dll_directories", lambda: [])
    monkeypatch.setattr(voice_gpu, "free_vram_mb", lambda: next(readings))
    assert stt.whisper_device() == ("cuda", "int8_float16")
    assert stt.whisper_device() == ("cpu", "int8")


def test_the_model_is_built_with_the_device_that_was_decided(monkeypatch) -> None:
    # faster-whisper est un extra optionnel : sans lui, ce test n'a rien à remplacer.
    # Sur la CI il n'est pas installé, et l'import direct faisait échouer 12 jobs.
    faster_whisper = pytest.importorskip("faster_whisper")

    from uh.audio import local as stt

    built: list[tuple[str, str]] = []

    class FakeModel:
        def __init__(self, name, device, compute_type):
            built.append((device, compute_type))

        def transcribe(self, path, **kwargs):
            return iter([]), None

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    monkeypatch.setattr(stt, "_MODEL", None)
    monkeypatch.setattr(stt, "_MODEL_KEY", None)
    monkeypatch.setattr(voice_gpu, "read_bench", lambda: {"gpu_wins": True})
    monkeypatch.setattr(voice_gpu, "cuda_available", lambda: True)
    monkeypatch.setattr(voice_gpu, "free_vram_mb", lambda: 2000)
    monkeypatch.setattr(voice_gpu, "add_dll_directories", lambda: [])

    stt.whisper_transcribe(Path("clip.wav"))
    assert built == [("cuda", "int8_float16")]

    # Le banc suivant dit le contraire : le modèle est reconstruit sur CPU, pas réutilisé.
    monkeypatch.setattr(voice_gpu, "read_bench", lambda: {"gpu_wins": False})
    stt.whisper_transcribe(Path("clip.wav"))
    assert built == [("cuda", "int8_float16"), ("cpu", "int8")]
