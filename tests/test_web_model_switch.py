"""The header switches a *pair*: a model id belongs to a provider.

The bug this pins: the menu only listed Ollama models, so picking DeepSeek (or a
local model while DeepSeek was active) changed the engine and not the model - the
chat went faster and dumber, or came back as a bare ``HTTP 500``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh.web.app import create_app  # noqa: E402

TOKEN = "switch-token"
PORT = 8765
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LOCAL = [{"name": "lfm3:latest", "size": 1}]


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """A provider factory that records the pair instead of calling anything."""
    import uh.providers.ollama as ollama
    import uh.providers_admin as admin

    monkeypatch.setattr(ollama, "list_models", lambda timeout=1.5: LOCAL)
    monkeypatch.setattr(
        admin,
        "read_providers",
        lambda: {"deepseek": {"keys": ["sk-test"], "model": "deepseek-flash", "base_url": ""}},
    )
    seen: list[tuple[str, str]] = []

    def factory(kind: str, model: str) -> Any:
        seen.append((kind, model))
        return None

    factory.calls = seen  # type: ignore[attr-defined]
    return seen


def call(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, str]],
    tmp_path: Path,
    method: str,
    path: str,
    body: Optional[dict] = None,
) -> httpx.Response:
    """One request through the real app, without a socket."""
    monkeypatch.setenv("UH_HOME", str(tmp_path))

    def factory(kind: str, model: str) -> Any:
        calls.append((kind, model))
        return None

    app = create_app(
        token=TOKEN,
        port=PORT,
        host="127.0.0.1",
        workspace=tmp_path,
        provider_factory=factory,
        kind="ollama",
        model="lfm3:latest",
    )
    transport = httpx.ASGITransport(app=app)

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=transport,
            base_url=f"http://127.0.0.1:{PORT}",
            headers=AUTH,
            timeout=10.0,
        ) as http:
            return await http.request(method, path, json=body)

    return asyncio.run(go())


def test_the_menu_lists_api_models_with_their_provider(monkeypatch, calls, tmp_path) -> None:
    payload = call(monkeypatch, calls, tmp_path, "GET", "/api/models").json()

    choices = payload["choices"]
    local = [row for row in choices if row["local"]]
    api = [row for row in choices if not row["local"]]
    assert {row["id"] for row in local} == {"lfm3:latest"}
    assert ("deepseek-flash", "deepseek") in {(row["id"], row["provider"]) for row in api}
    assert all(row["provider"] == "ollama" for row in local)
    # The current pair is reported as such, not inferred by the client.
    assert payload["current"]["id"] == "lfm3:latest"
    assert payload["current"]["provider"] == "ollama"


def test_a_local_model_that_cannot_call_tools_says_so(monkeypatch, calls, tmp_path) -> None:
    """The menu tells the truth about tools instead of letting the turn fail.

    Ollama answers /api/show with its capabilities; lfm3 does not list "tools",
    which is exactly the model that answers a task with a code block.
    """
    import uh.providers.ollama as ollama_mod

    monkeypatch.setattr(ollama_mod, "model_capabilities", lambda name: frozenset({"completion"}))

    payload = call(monkeypatch, calls, tmp_path, "GET", "/api/models").json()

    local = [row for row in payload["choices"] if row["local"]]
    assert local and all(row["tools_ok"] is False for row in local)


def test_an_unknown_capability_is_not_a_warning(monkeypatch, calls, tmp_path) -> None:
    """No answer is not a "no": an old Ollama must not get its models flagged."""
    import uh.providers.ollama as ollama_mod

    monkeypatch.setattr(ollama_mod, "model_capabilities", lambda name: None)

    payload = call(monkeypatch, calls, tmp_path, "GET", "/api/models").json()

    local = [row for row in payload["choices"] if row["local"]]
    assert local and all(row["tools_ok"] is None for row in local)


def test_a_provider_without_a_key_is_not_offered(monkeypatch, calls, tmp_path) -> None:
    import uh.providers_admin as admin

    monkeypatch.setattr(admin, "read_providers", lambda: {})
    payload = call(monkeypatch, calls, tmp_path, "GET", "/api/models").json()

    assert all(row["local"] for row in payload["choices"]), "no key, no API entry"


def test_switching_to_deepseek_sends_both_halves(monkeypatch, calls, tmp_path) -> None:
    answer = call(
        monkeypatch, calls, tmp_path, "POST", "/api/model",
        {"provider": "deepseek", "model": "deepseek-v4-pro"},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["provider"] == "deepseek"
    assert calls[-1] == ("deepseek", "deepseek-v4-pro")


def test_the_provider_is_inferred_from_the_catalogue(monkeypatch, calls, tmp_path) -> None:
    """An older client sends only the id; the catalogue names the provider."""
    answer = call(monkeypatch, calls, tmp_path, "POST", "/api/model", {"model": "deepseek-v4-pro"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["provider"] == "deepseek"
    assert calls[-1] == ("deepseek", "deepseek-v4-pro")


def test_a_local_name_never_reaches_an_api_provider(monkeypatch, calls, tmp_path) -> None:
    """The exact mismatch behind the 'HTTP 500': ollama's name sent to DeepSeek."""
    answer = call(
        monkeypatch, calls, tmp_path, "POST", "/api/model",
        {"provider": "deepseek", "model": "lfm3:latest"},
    )

    assert answer.status_code == 400
    detail = answer.json()["detail"]
    assert "modèle local" in detail and "ollama" in detail
    assert calls == [], "nothing was constructed from a contradictory pair"


def test_an_uninstalled_local_model_is_refused_with_a_sentence(monkeypatch, calls, tmp_path) -> None:
    answer = call(
        monkeypatch, calls, tmp_path, "POST", "/api/model",
        {"provider": "ollama", "model": "qwen9:70b"},
    )

    assert answer.status_code == 400
    assert "n'est pas installé" in answer.json()["detail"]
    assert calls == []


def test_setup_refuses_a_model_that_is_not_installed(monkeypatch, calls, tmp_path) -> None:
    answer = call(
        monkeypatch, calls, tmp_path, "POST", "/api/setup",
        {"choice": "ollama", "model": "qwen9:70b"},
    )

    assert answer.status_code == 400
    assert "n'est pas installé" in answer.json()["detail"]


def test_setup_picks_a_published_model_for_a_cloud_choice(monkeypatch, calls, tmp_path) -> None:
    import uh.providers_admin as admin

    stored: list[tuple[str, str]] = []
    monkeypatch.setattr(admin, "add_key_to", lambda name, key: stored.append((name, key)) or 1)
    answer = call(
        monkeypatch, calls, tmp_path, "POST", "/api/setup",
        {"choice": "cloud", "provider": "deepseek", "key": "sk-x"},
    )

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["provider"] == "deepseek"
    assert body["model"] == "deepseek-flash", "a cloud choice never keeps an Ollama model"
    assert stored == [("deepseek", "sk-x")]


def test_a_catalogue_provider_is_listed_once(monkeypatch, calls, tmp_path) -> None:
    """The key of a catalogue provider lives in keys.toml: both stores see it."""
    import uh.keys as keys_mod

    monkeypatch.setattr(
        keys_mod,
        "read_providers",
        lambda: {"deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash", "keys": ["sk"]}},
    )
    payload = call(monkeypatch, calls, tmp_path, "GET", "/api/models").json()

    pairs = [(row["provider"], row["id"]) for row in payload["choices"]]
    assert len(pairs) == len(set(pairs)), f"duplicate entries: {pairs}"
    assert ("deepseek", "deepseek-flash") in pairs
