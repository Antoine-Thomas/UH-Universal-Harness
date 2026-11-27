"""G12 — les quatre portes, éprouvées par l'ENTRÉE RÉELLE du produit.

Règle de Thomas : « fait » = l'endpoint ou ``speak()`` appelle le code, et un test
qui passe par cette entrée échoue sans le correctif. Un test d'une fonction isolée
ne prouve rien.

Couvert ici : porte VRAM + accord moteur lourd, consentement cloud transmis par
``POST /api/audio/speak``, routes de politique, et ce que ``capabilities()`` dit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh.audio import AudioUnavailable, capabilities, local, policy, speak, voxtral  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CANARY = "canary-key-do-not-log-7c1d"


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("uh.audio._hermes_env_key", lambda name: "")
    monkeypatch.setattr("uh.audio.save_clip", lambda *a, **k: Path("clip.wav"))
    monkeypatch.setattr(
        "uh.audio.reference", lambda path, engine: {"path": str(path), "engine": engine}
    )
    monkeypatch.setattr(local, "piper", lambda: None)
    policy.reset_cloud_calls()
    state = {"xtts": 0, "cloud": 0, "vram": None}

    def fake_xtts(text, reference=None):
        state["xtts"] += 1
        return b"RIFF0000WAVE"

    def fake_cloud(text, key, **kwargs):
        state["cloud"] += 1
        return b"RIFF0000WAVE"

    monkeypatch.setattr(local, "xtts_speak", fake_xtts)
    monkeypatch.setattr(local, "xtts_python", lambda: "python")
    monkeypatch.setattr(local, "free_vram_gb", lambda: state["vram"])
    monkeypatch.setattr(voxtral, "speech", fake_cloud)
    yield state
    policy.reset_cloud_calls()


def _app(tmp_path: Path) -> Any:
    return create_app(
        token=TOKEN,
        port=8765,
        workspace=tmp_path,
        provider_factory=lambda kind, model: object(),
        kind="stub",
        model="stub:1b",
    )


# --------------------------------------------------------------------------- #
# porte VRAM + accord moteur lourd, par speak()
# --------------------------------------------------------------------------- #
def test_unknown_vram_never_starts_the_heavy_engine(env) -> None:
    env["vram"] = None

    with pytest.raises(AudioUnavailable) as caught:
        speak("bonjour", heavy_consent=True)

    assert env["xtts"] == 0
    assert "VRAM libre non mesurable" in str(caught.value)


def test_three_gigabytes_free_never_starts_the_heavy_engine(env) -> None:
    env["vram"] = 3.0

    with pytest.raises(AudioUnavailable) as caught:
        speak("bonjour", heavy_consent=True)

    assert env["xtts"] == 0
    assert "3.0 Go de VRAM libre" in str(caught.value)


def test_enough_vram_but_no_consent_never_starts_the_heavy_engine(env) -> None:
    env["vram"] = 6.0

    with pytest.raises(AudioUnavailable) as caught:
        speak("bonjour")

    assert env["xtts"] == 0
    assert "accord non donné (moteur lourd" in str(caught.value)


def test_enough_vram_and_consent_starts_it_once(env) -> None:
    env["vram"] = 6.0

    clip = speak("bonjour", heavy_consent=True)

    assert clip["engine"] == "xtts"
    assert env["xtts"] == 1


# --------------------------------------------------------------------------- #
# consentement cloud, par speak() puis par l'endpoint
# --------------------------------------------------------------------------- #
def test_the_api_forwards_no_consent_by_default(env, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(voxtral.KEY_ENV, CANARY)
    app = _app(tmp_path)

    import anyio

    async def call() -> dict:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE
        ) as client:
            response = await client.post("/api/audio/speak", json={"text": "bonjour"}, headers=AUTH)
            return {"status": response.status_code, "body": response.json()}

    result = anyio.run(call)

    assert result["status"] == 503, result
    assert env["cloud"] == 0, "aucun texte ne part chez Mistral sans accord"
    assert "accord non donné" in result["body"]["detail"]


def test_the_api_forwards_an_explicit_consent(env, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(voxtral.KEY_ENV, CANARY)
    app = _app(tmp_path)

    import anyio

    async def call() -> dict:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE
        ) as client:
            response = await client.post(
                "/api/audio/speak", json={"text": "bonjour", "consent": True}, headers=AUTH
            )
            return {"status": response.status_code, "body": response.json()}

    result = anyio.run(call)

    assert result["status"] == 200, result
    assert env["cloud"] == 1
    assert result["body"]["engine"] == "voxtral"


# --------------------------------------------------------------------------- #
# politique : lisible et réglable par l'API, jamais à la main
# --------------------------------------------------------------------------- #
def test_the_policy_routes_read_and_write(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    app = _app(tmp_path)

    import anyio

    async def call() -> dict:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE
        ) as client:
            read = await client.get("/api/audio/policy", headers=AUTH)
            written = await client.post("/api/audio/policy", json={"policy": "never"}, headers=AUTH)
            bad = await client.post("/api/audio/policy", json={"policy": "parfois"}, headers=AUTH)
            return {
                "read": read.json(),
                "written": written.json(),
                "bad_status": bad.status_code,
                "bad_detail": bad.json()["detail"],
            }

    result = anyio.run(call)

    assert result["read"]["policy"] == "ask"
    assert result["read"]["choices"] == ["ask", "never", "always"]
    assert result["read"]["consent_line"] == "Le texte sera envoyé à Mistral"
    assert result["written"]["policy"] == "never"
    assert result["bad_status"] == 400
    assert "politique inconnue" in result["bad_detail"]


def test_capabilities_expose_both_gates(env) -> None:
    env["vram"] = 4.5

    data = capabilities()

    assert data["heavy"]["fits"] is False
    assert "4.5 Go de VRAM libre" in data["heavy"]["reason"]
    assert data["cloud"]["policy"] in {"ask", "never", "always"}
    assert data["cloud"]["consent_line"] == "Le texte sera envoyé à Mistral"
