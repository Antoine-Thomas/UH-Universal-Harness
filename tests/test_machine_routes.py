"""Le panneau Machine, prouvé par les routes réelles de UH (G19).

Un faux UHB compte ses requêtes : c'est la seule façon de distinguer « le
serveur a refusé » de « le serveur a refusé après avoir demandé un
téléchargement ». Sans accord, le compteur doit rester à zéro.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from uh.web.app import create_app

TOKEN = "test-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

SCAN = {
    "profile": {
        "os": "Windows",
        "gpu": None,
        "ram": {"total_gb": 8.0, "free_gb": 4.0},
        "unknown": ["gpu"],
    },
    "entry": {"id": "qwen2.5:7b", "mode": "cpu"},
    "alternatives": [],
    "reasons": ["VRAM non mesurée : seuls les petits modèles sont proposés"],
}


class FakeUHB:
    """Un UHB minimal qui retient ce qu'on lui demande."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                pass

            def _answer(self, payload: dict[str, Any], status: int) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                outer.requests.append(("GET", self.path, {}))
                self._answer({"id": "run-1", "state": "downloading"}, outer.status)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw or b"{}")
                except ValueError:
                    payload = {}
                outer.requests.append(("POST", self.path, payload))
                if outer.status >= 400:
                    self._answer({"error": {"message": "refus simulé de UHB"}}, outer.status)
                    return
                if self.path == "/machine/scan":
                    self._answer(SCAN, 200)
                else:
                    self._answer(
                        {
                            "started": True,
                            "id": "run-1",
                            "mode": "cpu",
                            "status_url": "/models/pull/run-1",
                        },
                        202,
                    )

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(
            target=lambda: self.httpd.serve_forever(poll_interval=0.01), daemon=True
        ).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def uhb(monkeypatch):
    fake = FakeUHB()
    monkeypatch.setenv("UH_UHB_URL", fake.url)
    try:
        yield fake
    finally:
        fake.close()


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


def post(app: Any, path: str, payload: dict[str, Any] | None = None) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            return await client.post(
                path, json=payload if payload is not None else {}, headers=AUTH
            )

    return asyncio.run(go())


def get(app: Any, path: str) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            return await client.get(path, headers=AUTH)

    return asyncio.run(go())


def test_the_scan_route_returns_what_uhb_measured(uhb, tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    response = post(app, "/api/machine/scan", {"category": "chat"})

    assert response.status_code == 200
    assert response.json() == SCAN
    assert uhb.requests == [("POST", "/machine/scan", {"category": "chat"})]


def test_without_accept_the_route_refuses_and_nothing_reaches_uhb(
    uhb, tmp_path, monkeypatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    response = post(app, "/api/machine/pull", {"entry_id": "qwen2.5:7b"})

    assert response.status_code == 403
    assert "accord explicite" in response.json()["detail"]
    assert uhb.requests == [], "aucune requête ne doit partir sans accord"


def test_accept_false_is_not_an_accord(uhb, tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    response = post(app, "/api/machine/pull", {"entry_id": "qwen2.5:7b", "accept": False})

    assert response.status_code == 403
    assert uhb.requests == []


def test_with_accept_one_request_carries_it(uhb, tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    response = post(app, "/api/machine/pull", {"entry_id": "qwen2.5:7b", "accept": True})

    assert response.status_code == 200
    assert response.json()["started"] is True
    assert uhb.requests == [("POST", "/models/pull", {"entry_id": "qwen2.5:7b", "accept": True})]


def test_an_entry_id_is_required(uhb, tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    response = post(app, "/api/machine/pull", {"accept": True})

    assert response.status_code == 400
    assert uhb.requests == []


def test_uhbs_refusal_keeps_its_numbers(uhb, tmp_path, monkeypatch) -> None:
    uhb.status = 409
    app = make_app(tmp_path, monkeypatch)

    response = post(
        app, "/api/machine/pull", {"entry_id": "inkling-2-mini:26b-a5b-iq4_NL", "accept": True}
    )

    assert response.status_code == 409
    assert "refus simulé de UHB" in response.json()["detail"]


def test_the_status_route_follows_uhb(tmp_path, monkeypatch) -> None:
    uhb_url = "http://127.0.0.1:1"  # rien n'écoute
    monkeypatch.setenv("UH_UHB_URL", uhb_url)
    app = make_app(tmp_path, monkeypatch)

    response = get(app, "/api/machine/pull/run-1")

    assert response.status_code == 503
    assert "injoignable" in response.json()["detail"]
