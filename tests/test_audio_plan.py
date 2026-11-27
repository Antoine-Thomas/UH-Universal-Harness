"""G12 — découpage dictable et porte VRAM : deux décisions, testables sans GPU.

Mesures du 27/11 qui fixent les bornes : ~35 ms par caractère, pic de XTTS ≈ 4,2 Go
de VRAM, 2 000 caractères ≈ 100 s. Un texte plus long se lit par morceaux, et le
moteur lourd ne se propose que si la carte peut le tenir — sinon la voix du
navigateur, et une phrase en français qui dit pourquoi.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh.audio import AudioUnavailable  # noqa: E402
from uh.audio.local import (  # noqa: E402
    heavy_engine_fits,
    heavy_engine_refusal,
    speech_timeout,
)
from uh.audio.speech_plan import MAX_PART_CHARS, plan_speech  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# --------------------------------------------------------------------------- #
# le découpage
# --------------------------------------------------------------------------- #
def test_a_short_text_is_one_part_and_loses_nothing() -> None:
    text = "Bonjour. Ceci est une phrase courte, et elle tient."

    plan = plan_speech(text)

    assert plan.parts == (text,)
    assert plan.total_chars == len(text)
    assert plan.truncated is False


def test_no_part_is_longer_than_the_limit_and_no_word_is_cut() -> None:
    text = "Une phrase de taille moyenne pour ce test. " * 20

    plan = plan_speech(text, limit=80, max_parts=40)

    assert all(len(part) <= 80 for part in plan.parts)
    assert plan.truncated is False
    # Rien n'est perdu : la concaténation des morceaux redonne le texte, espaces près.
    assert " ".join(plan.parts).split() == text.split()


def test_a_sentence_longer_than_the_limit_is_split_on_spaces() -> None:
    text = "mot " * 50  # 200 caractères, aucune ponctuation

    plan = plan_speech(text.strip(), limit=40)

    assert len(plan.parts) > 1
    assert all(len(part) <= 40 for part in plan.parts)
    assert " ".join(plan.parts).split() == text.split()


def test_a_word_longer_than_the_limit_is_cut_and_the_text_survives() -> None:
    text = "a" * 90

    plan = plan_speech(text, limit=40)

    assert [len(part) for part in plan.parts] == [40, 40, 10]
    assert "".join(plan.parts) == text


def test_too_many_parts_is_refused_with_its_counts_never_truncated() -> None:
    text = "Une phrase moyenne pour compter les morceaux. " * 40

    with pytest.raises(AudioUnavailable, match=r"texte trop long : \d+ morceaux"):
        plan_speech(text, limit=60, max_parts=3)


def test_an_empty_text_says_so_instead_of_silence() -> None:
    with pytest.raises(AudioUnavailable, match="le texte est vide"):
        plan_speech("   ")


def test_the_cap_is_the_measured_one() -> None:
    assert MAX_PART_CHARS == 2000
    assert speech_timeout("x" * MAX_PART_CHARS) == pytest.approx(180.0)


# --------------------------------------------------------------------------- #
# la porte VRAM
# --------------------------------------------------------------------------- #
def test_the_heavy_engine_needs_the_peak_plus_a_margin() -> None:
    assert heavy_engine_fits(4.2 + 0.8) is True
    assert heavy_engine_fits(4.2) is False, "le pic seul ne laisse aucune marge"
    assert heavy_engine_fits(None) is False, "une VRAM non mesurée ne tient pas"


def test_the_refusal_names_the_numbers_in_french() -> None:
    message = heavy_engine_refusal(4.5)

    assert "4.5 Go de VRAM libre" in message
    assert "4.2 Go" in message and "0.8 Go de marge" in message
    assert "voix du navigateur" in message
    assert "non mesurable" in heavy_engine_refusal(None)


# --------------------------------------------------------------------------- #
# l'endpoint
# --------------------------------------------------------------------------- #
def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(
        token=TOKEN,
        port=8765,
        workspace=tmp_path,
        provider_factory=lambda kind, model: object(),
        kind="stub",
        model="stub:1b",
    )


@pytest.mark.anyio
async def test_the_plan_route_announces_parts_and_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = "Première phrase. " * 30

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        response = await client.post(
            "/api/audio/plan", json={"text": text, "limit": 100}, headers=AUTH
        )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == len(body["parts"]) > 1
    assert body["truncated"] is False
    assert body["part_limit"] == 100
    assert body["delay_seconds"] == pytest.approx(speech_timeout(body["parts"][-1]), abs=0.1)


@pytest.mark.anyio
async def test_the_plan_route_refuses_a_too_long_text_with_its_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        response = await client.post(
            "/api/audio/plan",
            json={"text": "Une phrase. " * 40, "limit": 40, "max_parts": 2},
            headers=AUTH,
        )

    assert response.status_code == 400
    assert "texte trop long" in response.json()["detail"]
