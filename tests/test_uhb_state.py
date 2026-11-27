"""P1-8 — l'état du second cerveau est à trois valeurs, et chacune se distingue.

« Hors ligne » mélangeait deux pannes que l'utilisateur ne répare pas de la même
façon : UHB absent (rien n'écoute, il faut le démarrer) et UHB en erreur (il
répond, mais mal : il faut lire). Ces tests passent par la route réelle de UH et
par un faux UHB, pour que ce soit le transport qui soit mesuré, pas une fonction.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from uh.web.app import UHB_STATE_ABSENT, UHB_STATE_ERROR, UHB_STATE_OK, create_app

TOKEN = "test-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

STATUS_OK = {
    "connected": True,
    "version": "1.9.2",
    "mode": "chat",
    "harness": {"name": "claude"},
    "providers": [],
    "modes": ["chat", "dev"],
    "execute": {},
}


class FakeUHB:
    def __init__(
        self, body: bytes, status_code: int = 200, content_type: str = "application/json"
    ) -> None:
        self.body = body
        self.status_code = status_code
        self.content_type = content_type
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                self.send_response(outer.status_code)
                self.send_header("Content-Type", outer.content_type)
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

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


def status_of(app: Any) -> dict:
    async def go() -> dict:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            response = await client.get("/api/uhb/status", headers=AUTH)
        assert response.status_code == 200, response.text
        return response.json()

    return asyncio.run(go())


def test_a_good_uhb_is_ok(monkeypatch, tmp_path) -> None:
    uhb = FakeUHB(json.dumps(STATUS_OK).encode())
    monkeypatch.setenv("UH_UHB_URL", uhb.url)
    try:
        payload = status_of(make_app(tmp_path, monkeypatch))
    finally:
        uhb.close()

    assert payload["state"] == UHB_STATE_OK
    assert payload["connected"] is True
    assert payload["mode"] == "chat"
    assert payload["sentence"] == "Second cerveau joignable."


def test_no_url_is_absent_and_says_which_switch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("UH_UHB_URL", "")

    payload = status_of(make_app(tmp_path, monkeypatch))

    assert payload["state"] == UHB_STATE_ABSENT
    assert "UH_UHB_URL" in payload["sentence"]


def test_nothing_listening_is_absent_not_error(monkeypatch, tmp_path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    monkeypatch.setenv("UH_UHB_URL", f"http://127.0.0.1:{closed_port}")

    payload = status_of(make_app(tmp_path, monkeypatch))

    assert payload["state"] == UHB_STATE_ABSENT
    assert "rien ne répond" in payload["sentence"]


def test_an_http_error_is_error(monkeypatch, tmp_path) -> None:
    uhb = FakeUHB(b'{"error": {"message": "boom"}}', status_code=500)
    monkeypatch.setenv("UH_UHB_URL", uhb.url)
    try:
        payload = status_of(make_app(tmp_path, monkeypatch))
    finally:
        uhb.close()

    assert payload["state"] == UHB_STATE_ERROR
    assert "en erreur" in payload["sentence"]


def test_invalid_json_is_error_not_absent(monkeypatch, tmp_path) -> None:
    uhb = FakeUHB(b"<html>pas du json</html>", content_type="text/html")
    monkeypatch.setenv("UH_UHB_URL", uhb.url)
    try:
        payload = status_of(make_app(tmp_path, monkeypatch))
    finally:
        uhb.close()

    assert payload["state"] == UHB_STATE_ERROR


def test_the_three_states_are_the_only_ones(monkeypatch, tmp_path) -> None:
    """Aucun quatrième état : le contrat est exactement ok / absent / error."""
    monkeypatch.setenv("UH_UHB_URL", "")

    payload = status_of(make_app(tmp_path, monkeypatch))

    assert payload["state"] in {UHB_STATE_OK, UHB_STATE_ABSENT, UHB_STATE_ERROR}
    assert set(payload) >= {"state", "sentence", "connected"}
