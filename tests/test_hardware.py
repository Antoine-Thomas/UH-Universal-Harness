"""Hardware detection: the tier each machine lands in, and what follows from it.

Detection itself is probed for real once (this machine), and the classification
is tested against fixed profiles, because a test that depends on the RAM of the
laptop running it is a test that fails on someone else's laptop.
"""

from __future__ import annotations

import sys
import types

import pytest

from uh import context, hardware


def profile(ram: float, gpu: str | None = None, cores: int = 8) -> hardware.HardwareProfile:
    return hardware.HardwareProfile(
        ram_gb=ram,
        cpu_cores=cores,
        gpu=gpu,
        os_name="Windows",
        arch="AMD64",
        tier=hardware.classify(ram, gpu, cores),
    )


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "ram,gpu,tier,model,budget",
    [
        (1.0, None, "remote", None, 3000),
        (2.0, None, "small", "qwen2.5:0.5b", 3000),
        (3.5, None, "small", "qwen2.5:0.5b", 3000),
        (6.0, None, "medium", "qwen2.5:1.5b", 6000),
        (8.0, None, "large", "qwen2.5:3b", 12000),
        (15.9, None, "large", "qwen2.5:3b", 12000),
        (32.0, None, "xl", "qwen2.5:7b", 24000),
    ],
)
def test_the_four_profiles_and_their_recommendations(
    ram: float, gpu: str | None, tier: str, model: str | None, budget: int
) -> None:
    found = profile(ram, gpu)

    assert found.tier == tier
    assert found.recommended_model == model
    assert found.recommended_budget == budget


def test_a_dedicated_gpu_promotes_a_small_machine_to_xl() -> None:
    found = profile(8.0, gpu="NVIDIA GeForce RTX 3070 Ti")

    assert found.tier == "xl", "the model goes to VRAM, which is the point of the GPU"
    assert found.recommended_model == "qwen2.5:7b"


def test_a_device_below_two_gigabytes_is_remote_only() -> None:
    found = profile(1.0)

    assert found.tier == "remote"
    assert found.local_possible is False
    assert found.recommended_model is None
    assert "remote provider" in found.sentence()


def test_unknown_ram_assumes_something_workable() -> None:
    assert hardware.classify(0.0, None) == "large", "a failed probe must not cripple the default"


def test_the_sentence_names_the_machine_and_the_model() -> None:
    text = profile(6.0).sentence()

    assert "medium" in text
    assert "6 GB RAM" in text
    assert "qwen2.5:1.5b" in text
    assert "context 6k" in text
    assert "no GPU" in text


def test_the_sentence_mentions_the_gpu_when_there_is_one() -> None:
    assert "RTX 3070" in profile(32.0, gpu="NVIDIA RTX 3070").sentence()


def test_a_remote_tier_says_there_is_no_local_model() -> None:
    text = profile(1.0).sentence()

    assert "too small" in text
    assert "remote provider" in text


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
def test_detection_returns_a_usable_profile() -> None:
    found = hardware.detect()

    assert found.cpu_cores >= 1
    assert found.ram_gb >= 0
    assert found.tier in hardware.TIERS
    assert found.os_name


def test_the_profile_is_cached() -> None:
    assert hardware.profile() is hardware.profile()


def test_psutil_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """No psutil must still yield RAM, through the platform fallback."""
    monkeypatch.setitem(sys.modules, "psutil", None)
    value = hardware.total_ram_gb()

    assert value > 0, "the ctypes / sysconf fallback has to answer on a machine with RAM"


def test_a_broken_psutil_is_survived(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = types.SimpleNamespace(virtual_memory=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setitem(sys.modules, "psutil", broken)

    assert hardware.total_ram_gb() > 0


def test_no_gpu_tool_means_no_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware.shutil, "which", lambda name: None)
    assert hardware.detect_gpu() is None


# --------------------------------------------------------------------------- #
# the budget now follows the machine
# --------------------------------------------------------------------------- #
def test_the_tier_sets_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UH_CONTEXT_BUDGET", raising=False)
    monkeypatch.setattr(hardware, "budget_for_tier", lambda: 6000)

    assert context.default_budget("qwen2.5:1.5b", native=None) == 6000


def test_the_model_window_caps_the_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 24k budget on a 4k model is how a turn fails."""
    monkeypatch.delenv("UH_CONTEXT_BUDGET", raising=False)
    monkeypatch.setattr(hardware, "budget_for_tier", lambda: 24000)

    assert context.default_budget("tiny:0.5b", native=4000) == 3000


def test_env_still_wins_over_the_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_CONTEXT_BUDGET", "777")
    monkeypatch.setattr(hardware, "budget_for_tier", lambda: 24000)

    assert context.default_budget("m", native=32000) == 777
