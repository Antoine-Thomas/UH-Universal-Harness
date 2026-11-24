"""Les routes de G8 : interrupteur, provenance vérifiée côté serveur, refus.

Le réseau et la récupération sont simulés (`link_preview.preview` et
`link_preview.fetch_image` sont remplacés) : ce qui est vérifié ici est le
**contrat des routes**. Le fait que rien ne parte vers l'extérieur est prouvé
par un compteur sur ces deux fonctions : quand la route refuse en amont, le
compteur reste à zéro.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import link_preview  # noqa: E402
from uh.store import Store  # noqa: E402
from uh.web.app import (  # noqa: E402
    LINK_IMAGE_PATH,
    LINK_PREVIEW_PATH,
    LINK_SETTINGS_PATH,
    create_app,
)

TOKEN = "test-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 48
URL = "https://example.com/article?utm=secret"


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
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


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


def enable(home: Path) -> None:
    link_preview.save_enabled(True)


def session_with(app: Any, messages: List[Dict[str, Any]]) -> str:
    """Une session qui contient vraiment ces messages."""
    store = Store(app.state.settings.store_path)
    try:
        session_id = store.create_session(title="essai")
        for message in messages:
            store.append_message(session_id, message)
    finally:
        store.close()
    return session_id


class Recorder:
    """Compte les appels réellement faits à la récupération."""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.fail: str = ""
        self.page: Any = None
        self.image: Any = None

    def preview(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(url)
        if self.fail:
            raise link_preview.PreviewRefused(self.fail)
        return self.page

    def fetch_image(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(url)
        if self.fail:
            raise link_preview.PreviewRefused(self.fail)
        return self.image


@pytest.fixture()
def page() -> "link_preview.Page":
    return link_preview.Page(
        url=URL,
        final_url="https://example.com/article",
        host="example.com",
        title="Un titre",
        description="Une description",
        image="https://example.com/img.png",
        icon="https://example.com/favicon.ico",
        redirects=1,
        bytes_read=1200,
        robots="allowed",
        notes=("note",),
    )


def install(monkeypatch: pytest.MonkeyPatch, recorder: Recorder) -> Recorder:
    monkeypatch.setattr(link_preview, "preview", recorder.preview)
    monkeypatch.setattr(link_preview, "fetch_image", recorder.fetch_image)
    return recorder


# --------------------------------------------------------------------------- #
# l'interrupteur
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_the_switch_is_off_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    recorder = install(monkeypatch, Recorder())

    async with client(app) as http:
        answer = await http.get(
            LINK_PREVIEW_PATH, params={"url": URL, "origin": "click"}, headers=AUTH
        )
        settings = await http.get(LINK_SETTINGS_PATH, headers=AUTH)

    assert answer.status_code == 403
    assert "désactivés" in answer.json()["detail"]
    assert recorder.calls == [], "aucune requête ne part quand l'interrupteur est fermé"
    assert settings.json()["enabled"] is False
    assert settings.json()["sentence"] == link_preview.SENTENCE


@pytest.mark.anyio
async def test_the_switch_turns_on_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    install(monkeypatch, Recorder())

    async with client(app) as http:
        flipped = await http.post(
            LINK_SETTINGS_PATH,
            json={"enabled": True},
            headers={**AUTH, "Origin": BASE},
        )
        after = await http.get(LINK_SETTINGS_PATH, headers=AUTH)

    assert flipped.status_code == 200
    assert flipped.json()["enabled"] is True
    assert after.json()["enabled"] is True
    assert (tmp_path / "home" / link_preview.SETTINGS_FILE).is_file()


@pytest.mark.anyio
async def test_the_switch_refuses_a_body_that_is_not_a_boolean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    install(monkeypatch, Recorder())

    async with client(app) as http:
        answer = await http.post(
            LINK_SETTINGS_PATH, json={"enabled": "oui"}, headers={**AUTH, "Origin": BASE}
        )

    assert answer.status_code == 400
    assert "booléen" in answer.json()["detail"]


# --------------------------------------------------------------------------- #
# provenance : la règle vérifiée par le serveur
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_a_model_url_without_a_click_is_refused_and_never_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: Any
) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    recorder = install(monkeypatch, Recorder())
    recorder.page = page
    # La session ne contient que du texte de modèle : l'URL n'y est pas.
    session_id = session_with(
        app, [{"role": "assistant", "content": "vois https://ailleurs.example/"}]
    )

    async with client(app) as http:
        answer = await http.get(
            LINK_PREVIEW_PATH,
            params={"url": URL, "session": session_id, "origin": "tool"},
            headers=AUTH,
        )

    assert answer.status_code == 403
    assert "aucun résultat d'outil" in answer.json()["detail"]
    assert recorder.calls == [], "aucune requête sortante : le refus est côté serveur"


@pytest.mark.anyio
async def test_a_tool_url_is_accepted_when_it_really_is_in_the_tool_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: Any
) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    recorder = install(monkeypatch, Recorder())
    recorder.page = page
    session_id = session_with(
        app,
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "function": {"name": "web"}}],
            },
            {"role": "tool", "name": "web", "content": f"2 résultats\n- {URL}"},
        ],
    )

    async with client(app) as http:
        answer = await http.get(
            LINK_PREVIEW_PATH,
            params={"url": URL, "session": session_id, "origin": "tool"},
            headers=AUTH,
        )

    assert answer.status_code == 200
    assert answer.json()["title"] == "Un titre"
    assert recorder.calls == [URL]


@pytest.mark.anyio
async def test_a_falsified_origin_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: Any
) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    recorder = install(monkeypatch, Recorder())
    recorder.page = page
    session_id = session_with(app, [{"role": "tool", "name": "web", "content": "autre chose"}])

    async with client(app) as http:
        answer = await http.get(
            LINK_PREVIEW_PATH,
            params={"url": URL, "session": session_id, "origin": "tool"},
            headers=AUTH,
        )

    assert answer.status_code == 403
    assert recorder.calls == []


@pytest.mark.anyio
async def test_an_operator_url_is_proven_by_the_operator_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: Any
) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    recorder = install(monkeypatch, Recorder())
    recorder.page = page
    session_id = session_with(app, [{"role": "user", "content": f"lis {URL} s'il te plaît"}])

    async with client(app) as http:
        proven = await http.get(
            LINK_PREVIEW_PATH,
            params={"url": URL, "session": session_id, "origin": "operator"},
            headers=AUTH,
        )
        unproven = await http.get(
            LINK_PREVIEW_PATH,
            params={"url": "https://autre.example/x", "session": session_id, "origin": "operator"},
            headers=AUTH,
        )

    assert proven.status_code == 200
    assert unproven.status_code == 403
    assert "aucun de vos messages" in unproven.json()["detail"]


@pytest.mark.anyio
async def test_a_click_needs_no_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: Any
) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    recorder = install(monkeypatch, Recorder())
    recorder.page = page

    async with client(app) as http:
        answer = await http.get(
            LINK_PREVIEW_PATH, params={"url": URL, "origin": "click"}, headers=AUTH
        )

    assert answer.status_code == 200
    assert recorder.calls == [URL]


@pytest.mark.anyio
async def test_an_unknown_origin_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    recorder = install(monkeypatch, Recorder())

    async with client(app) as http:
        answer = await http.get(
            LINK_PREVIEW_PATH, params={"url": URL, "origin": "model"}, headers=AUTH
        )

    assert answer.status_code == 403
    assert "origine inconnue" in answer.json()["detail"]
    assert recorder.calls == []


# --------------------------------------------------------------------------- #
# jeton, budget, bornes, erreurs
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_the_token_is_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    install(monkeypatch, Recorder())

    async with client(app) as http:
        answer = await http.get(LINK_PREVIEW_PATH, params={"url": URL, "origin": "click"})

    assert answer.status_code in (401, 403)


@pytest.mark.anyio
async def test_the_session_budget_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: Any
) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    recorder = install(monkeypatch, Recorder())
    recorder.page = page
    app.state.media_limiter = _Spent()

    async with client(app) as http:
        answer = await http.get(
            LINK_PREVIEW_PATH,
            params={"url": URL, "session": "s1", "origin": "click"},
            headers=AUTH,
        )

    assert answer.status_code == 429
    assert recorder.calls == []


class _Spent:
    def allow(self, key: str) -> Any:
        return False, "budget de 30 aperçus par minute dépassé : réessayez dans un instant"


@pytest.mark.anyio
async def test_the_answer_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    recorder = install(monkeypatch, Recorder())
    recorder.page = link_preview.Page(
        url="https://example.com/" + "a" * 5000,
        final_url="https://example.com/" + "b" * 5000,
        host="example.com",
        title="t" * 5000,
        description="d" * 5000,
        image="https://example.com/" + "i" * 5000,
        icon="https://example.com/" + "f" * 5000,
        notes=tuple("n" * 1000 for _ in range(10)),
    )

    async with client(app) as http:
        answer = await http.get(
            LINK_PREVIEW_PATH, params={"url": URL, "origin": "click"}, headers=AUTH
        )

    body = answer.json()
    assert len(body["title"]) == link_preview.TITLE_MAX
    assert len(body["description"]) == link_preview.DESCRIPTION_MAX
    assert len(body["url"]) == link_preview.URL_MAX
    assert len(body["image"]) == link_preview.URL_MAX
    assert len(body["notes"]) <= 4
    assert all(len(note) <= 200 for note in body["notes"])


@pytest.mark.anyio
async def test_a_refusal_is_a_readable_french_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    recorder = install(monkeypatch, Recorder())
    recorder.fail = "adresse non publique refusée : example.com -> 127.0.0.1"

    async with client(app) as http:
        answer = await http.get(
            LINK_PREVIEW_PATH, params={"url": URL, "origin": "click"}, headers=AUTH
        )

    assert answer.status_code == 400
    detail = answer.json()["detail"]
    assert detail == recorder.fail
    assert "Traceback" not in detail and "Error" not in detail


@pytest.mark.anyio
async def test_the_journal_names_the_domain_and_never_the_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: Any, caplog: Any
) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    recorder = install(monkeypatch, Recorder())
    recorder.page = page

    with caplog.at_level(logging.INFO, logger="uh.web.links"):
        async with client(app) as http:
            await http.get(LINK_PREVIEW_PATH, params={"url": URL, "origin": "click"}, headers=AUTH)

    lines = [record.getMessage() for record in caplog.records]
    assert any("example.com" in line for line in lines)
    assert all("utm=secret" not in line and "article" not in line for line in lines)


# --------------------------------------------------------------------------- #
# l'image d'aperçu
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_an_image_is_served_without_being_interpreted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    recorder = install(monkeypatch, Recorder())
    recorder.image = link_preview.ImageData(
        mime="image/png", data=PNG, final_url="https://example.com/i.png"
    )

    async with client(app) as http:
        answer = await http.get(
            LINK_IMAGE_PATH,
            params={"url": "https://example.com/i.png", "session": "s"},
            headers=AUTH,
        )

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "image/png"
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in answer.headers["content-security-policy"]
    assert answer.content == PNG


@pytest.mark.anyio
@pytest.mark.parametrize(
    "reason",
    [
        "format refusé : JPEG, PNG, WebP ou GIF attendu (un SVG n'est pas une image affichable)",
        "page trop lourde : plus de 2097152 octets",
        "adresse non publique refusée : example.com -> 127.0.0.1",
    ],
)
async def test_a_refused_image_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    app = make_app(tmp_path, monkeypatch)
    enable(tmp_path / "home")
    recorder = install(monkeypatch, Recorder())
    recorder.fail = reason

    async with client(app) as http:
        answer = await http.get(
            LINK_IMAGE_PATH,
            params={"url": "https://example.com/i.svg", "session": "s"},
            headers=AUTH,
        )

    assert answer.status_code == 400
    assert answer.json()["detail"] == reason


@pytest.mark.anyio
async def test_the_image_route_is_closed_when_the_switch_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    recorder = install(monkeypatch, Recorder())

    async with client(app) as http:
        answer = await http.get(
            LINK_IMAGE_PATH,
            params={"url": "https://example.com/i.png", "session": "s"},
            headers=AUTH,
        )

    assert answer.status_code == 403
    assert recorder.calls == []
