"""G12 — aucun texte ne part chez Mistral sans accord, et ça se compte.

Le défaut d'origine (27/11) : `speak()` essayait Voxtral **en premier** dès qu'une
clé existait, sans que l'interface le dise, et `--local-only` ne l'arrêtait pas.
Ici : politique `ask` par défaut, `never`, `always`, compteur d'appels cloud, et un
secret canari qui ne doit apparaître dans aucun message.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh.audio import AudioUnavailable, policy, speak, voxtral

CANARY = "canary-key-do-not-log-9f2b06f"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(voxtral.KEY_ENV, CANARY)
    monkeypatch.setattr("uh.audio._hermes_env_key", lambda name: "")
    monkeypatch.setattr("uh.audio.local.xtts_python", lambda: None)
    monkeypatch.setattr("uh.audio.local.piper", lambda: None)
    policy.reset_cloud_calls()
    yield
    policy.reset_cloud_calls()


def _stub_speech(calls: list[str]):
    def fake(text: str, key: str, **kwargs: object) -> bytes:
        calls.append(f"{text}|{key}")
        return b"RIFF0000WAVE"

    return fake


def test_the_default_policy_sends_nothing_without_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(voxtral, "speech", _stub_speech(calls))

    assert policy.load_policy() == "ask"
    with pytest.raises(AudioUnavailable, match="accord non donné"):
        speak("bonjour")

    assert calls == []
    assert policy.cloud_calls() == 0


def test_consent_true_sends_once_and_counts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(voxtral, "speech", _stub_speech(calls))
    monkeypatch.setattr("uh.audio.save_clip", lambda *a, **k: Path("clip.wav"))
    monkeypatch.setattr(
        "uh.audio.reference", lambda path, engine: {"path": str(path), "engine": engine}
    )

    result = speak("bonjour", consent=True)

    assert result["engine"] == "voxtral"
    assert len(calls) == 1
    assert policy.cloud_calls() == 1


def test_never_wins_even_over_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(voxtral, "speech", _stub_speech(calls))
    policy.save_policy("never")

    with pytest.raises(AudioUnavailable, match="politique « jamais »"):
        speak("bonjour", consent=True)

    assert calls == []


def test_always_counts_as_consent_given_in_advance(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(voxtral, "speech", _stub_speech(calls))
    monkeypatch.setattr("uh.audio.save_clip", lambda *a, **k: Path("clip.wav"))
    monkeypatch.setattr(
        "uh.audio.reference", lambda path, engine: {"path": str(path), "engine": engine}
    )
    policy.save_policy("always")

    assert speak("bonjour")["engine"] == "voxtral"
    assert len(calls) == 1


def test_the_canary_key_never_reaches_a_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(text: str, key: str, **kwargs: object) -> bytes:
        raise voxtral.VoxtralError(f"synthèse HTTP 500 (clé {key[:0]})")

    monkeypatch.setattr(voxtral, "speech", boom)

    with pytest.raises(AudioUnavailable) as caught:
        speak("bonjour", consent=True)

    assert CANARY not in str(caught.value)
    assert "voxtral" in str(caught.value)


def test_an_unknown_policy_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="politique inconnue"):
        policy.save_policy("sometimes")


def test_the_policy_file_is_written_where_the_readme_says(tmp_path: Path) -> None:
    path = policy.save_policy("never")

    assert path.name == policy.POLICY_FILE
    assert path.read_text(encoding="utf-8").strip() == 'cloud = "never"'
    assert policy.load_policy() == "never"


# --------------------------------------------------------------------------- #
# (6) la bascule : qui lit quand aucun moteur ne répond
# --------------------------------------------------------------------------- #
def test_without_any_engine_the_answer_names_the_browser_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("uh.audio.mistral_key", lambda: "")

    with pytest.raises(AudioUnavailable) as caught:
        speak("bonjour")

    message = str(caught.value)
    assert policy.FALLBACK_ENGINE == "browser"
    assert "voix du navigateur" in message, message
    assert "aucun envoi à Mistral" in message


def test_capabilities_expose_the_fallback_decision() -> None:
    from uh.audio import capabilities

    fallback = capabilities()["fallback"]

    assert fallback["engine"] == "browser"
    assert "voix du navigateur" in fallback["info"]
