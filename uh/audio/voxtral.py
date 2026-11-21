"""Voxtral 3 over REST.

The SDK is not used on purpose: measured elsewhere on this machine, `mistralai` 3.x
imports but `from mistralai.client import Mistral` dies on a pydantic binary
mismatch between two venvs, and the three REST endpoints are enough. Nothing here
logs a key, and a 401/403/429/timeout is raised as a plain exception so the chain
above can fall through to a local engine.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

#: Where the key is looked up. Never printed.
KEY_ENV = "MISTRAL_API_KEY"
BASE_URL = "https://api.mistral.ai"
TTS_MODEL = "voxtral-mini-tts-3-0"
STT_MODEL = "voxtral-mini-transcribe-3-0"
TIMEOUT_S = 90.0


class VoxtralError(RuntimeError):
    pass


def _headers(key: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def health(key: str) -> bool:
    """`GET /v1/models` answers 200 only for a valid key."""
    with httpx.Client(timeout=15.0) as client:
        response = client.get(f"{BASE_URL}/v1/models", headers=_headers(key))
    return response.status_code == 200


def transcribe(clip: Path, key: str, *, language: str = "fr") -> str:
    """Speech to text. The file is sent as multipart; the answer is `{"text": ...}`."""
    with open(clip, "rb") as handle:
        payload = handle.read()
    files = {"file": (clip.name, payload, "audio/wav")}
    data = {"model": STT_MODEL}
    if language:
        data["language"] = language
    with httpx.Client(timeout=TIMEOUT_S) as client:
        response = client.post(
            f"{BASE_URL}/v1/audio/transcriptions",
            headers=_headers(key),
            data=data,
            files=files,
        )
    if response.status_code != 200:
        raise VoxtralError(f"transcription HTTP {response.status_code}")
    try:
        return str(response.json().get("text") or "")
    except ValueError as exc:
        raise VoxtralError("réponse de transcription illisible") from exc


def preset_voice(key: str, *, language: str = "fr") -> Optional[str]:
    """A voice the account already has, preferring one that speaks the language.

    This is the floor, not the goal: a preset is *not* the operator's voice, and the
    interface says so. It exists so a fresh account can still hear something instead
    of a refusal that only mentions `ref_audio`.
    """
    with httpx.Client(timeout=30.0) as client:
        response = client.get(
            f"{BASE_URL}/v1/audio/voices", headers=_headers(key), params={"limit": 100}
        )
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    items = payload.get("items") or payload.get("data") or []
    fallback = ""
    for item in items:
        voice_id = str(item.get("id") or "")
        if not voice_id:
            continue
        languages = [str(lang).lower() for lang in (item.get("languages") or [])]
        if any(lang.startswith(language) for lang in languages):
            return voice_id
        fallback = fallback or voice_id
    return fallback or None


def speech(
    text: str,
    key: str,
    *,
    voice: Optional[str] = None,
    reference_wav: Optional[Path] = None,
) -> bytes:
    """Text to a WAV (24 kHz mono PCM16).

    `ref_audio` comes first, exactly as measured: it clones for that one request and
    needs no paid plan. `voice_id` is used when the caller owns one; a preset is the
    last resort so the feature still speaks - saving a custom voice is refused on a
    free account with HTTP 403, so presenting it as the normal path is a dead end.
    """
    body: Dict[str, Any] = {
        "model": TTS_MODEL,
        "input": text,
        "response_format": "wav",
    }
    if reference_wav is not None and Path(reference_wav).is_file():
        raw = Path(reference_wav).read_bytes()
        body["ref_audio"] = base64.b64encode(raw).decode("ascii")
    elif voice:
        body["voice_id"] = voice
    else:
        chosen = preset_voice(key)
        if not chosen:
            raise VoxtralError("aucune voix disponible sur ce compte (ni ref_audio, ni preset)")
        body["voice_id"] = chosen
    with httpx.Client(timeout=TIMEOUT_S) as client:
        response = client.post(
            f"{BASE_URL}/v1/audio/speech",
            headers={**_headers(key), "Content-Type": "application/json"},
            content=json.dumps(body),
        )
    if response.status_code != 200:
        raise VoxtralError(f"synthèse HTTP {response.status_code}")
    try:
        encoded = str(response.json().get("audio_data") or "")
    except ValueError as exc:
        raise VoxtralError("réponse de synthèse illisible") from exc
    if not encoded:
        raise VoxtralError("réponse de synthèse sans audio")
    audio = base64.b64decode(encoded)
    if not audio:
        raise VoxtralError("audio vide")
    return audio


__all__ = [
    "BASE_URL",
    "KEY_ENV",
    "STT_MODEL",
    "TTS_MODEL",
    "VoxtralError",
    "health",
    "speech",
    "transcribe",
]
