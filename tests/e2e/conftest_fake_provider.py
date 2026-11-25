"""Fixture : un faux fournisseur OpenAI branché sur le vrai `uh web`.

Le point qui a coûté deux tours, et qu'il vaut la peine d'écrire ici : `uh web`
n'a **aucun** argument `--provider`. La sélection vient de la configuration lue
au démarrage (`app.state.kind` / `app.state.model`, voir `_provider_for`), donc
elle doit être écrite dans le `UH_HOME` temporaire **avant** de lancer le
processus. C'est ce que fait `seed_provider`.

Le faux serveur vit dans `tests/e2e/fake_openai.py` : loopback uniquement, port
choisi par le système, clé factice, et aucun accès depuis l'application.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import pytest

from .conftest import Server
from .fake_openai import FAKE_KEY, FakeOpenAI

MODEL_ID = "fake-1b"


def seed_provider_into(home: Path, base_url: str) -> None:
    from uh import config as uh_config
    from uh import keys as uh_keys

    Path(home).mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("UH_HOME")
    os.environ["UH_HOME"] = str(home)
    try:
        uh_keys.add_provider("fake", base_url, MODEL_ID, FAKE_KEY)
        uh_config.write_flat_key_preserving_tables("default_provider", "fake")
        uh_config.write_flat_key_preserving_tables("default_model", MODEL_ID)
    finally:
        if previous is None:
            os.environ.pop("UH_HOME", None)
        else:
            os.environ["UH_HOME"] = previous


@pytest.fixture()
def fake_provider(make_server: Any) -> Any:
    """Un serveur UH dont le fournisseur est le faux, déjà démarré."""
    fake = FakeOpenAI().start()
    try:
        server = make_server(
            "home-fake",
            seed=lambda home: seed_provider_into(home, fake.base_url),
            # « Ollama d'abord » gagne au démarrage si un Ollama répond. Un port
            # fermé suffit à écarter ce parasite, sans toucher à uh/ : UH lit
            # OLLAMA_URL (uh/capabilities.py:278).
            extra_env={"OLLAMA_URL": "http://127.0.0.1:9"},
        )
        yield server, fake
    finally:
        fake.stop()


def provider_state(server: Server) -> Dict[str, Any]:
    """Ce que le serveur croit être son fournisseur, par sa propre route."""
    import httpx

    with httpx.Client(base_url=server.base, timeout=10) as http:
        answer = http.get("/api/status", headers={"Authorization": f"Bearer {server.token}"})
        return answer.json() if answer.status_code == 200 else {"error": answer.text}


@pytest.fixture()
def fake_server_home(tmp_path: Path) -> Path:
    home = tmp_path / "home-seeded"
    home.mkdir(parents=True, exist_ok=True)
    return home
