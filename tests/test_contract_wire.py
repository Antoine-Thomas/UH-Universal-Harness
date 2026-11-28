"""P2-9 — le contrat UH <-> UHB est versionné, et une majeure différente se refuse.

Trois preuves, chacune par une entrée réelle :

* ``uh.contract.check`` sur les cinq formes qu'un UHB peut publier ;
* la route ``/api/uhb/status`` : contrat v2 -> état ``error`` et phrase nommée ;
* le toolset : contrat v2 -> l'appel est refusé **sans** qu'une requête POST parte.
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

from uh import contract as wire
from uh.toolsets.uhb import Uhbtoolset
from uh.web.app import create_app

TOKEN = "test-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeUHB:
    """Publie le contrat demandé et compte les POST reçus."""

    def __init__(self, contract: dict[str, Any] | None) -> None:
        self.contract = contract
        self.posts: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                payload: dict[str, Any] = {
                    "connected": True,
                    "version": "9.9.9",
                    "mode": "chat",
                    "harness": {"name": "fake"},
                    "providers": [],
                    "modes": ["chat", "dev"],
                    "execute": {},
                }
                if outer.contract is not None:
                    payload["contract"] = outer.contract
                self._send(payload)

            def do_POST(self) -> None:
                outer.posts.append(self.path)
                self._send({"ok": True})

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


# --- la fonction pure ------------------------------------------------------ #


def test_no_contract_is_unknown_not_incompatible() -> None:
    state, sentence = wire.check({"connected": True})

    assert state == wire.STATE_UNKNOWN
    assert "antérieur au contrat" in sentence


def test_the_same_major_is_compatible() -> None:
    state, sentence = wire.check({"contract": {"name": "uh-uhb", "version": 1}})

    assert state == wire.STATE_OK
    assert "v1" in sentence


def test_a_newer_minor_is_accepted() -> None:
    state, _ = wire.check({"contract": {"name": "uh-uhb", "version": 1, "minor": 3}})

    assert state == wire.STATE_OK


def test_a_newer_major_is_incompatible() -> None:
    state, sentence = wire.check({"contract": {"name": "uh-uhb", "version": 2}})

    assert state == wire.STATE_INCOMPATIBLE
    assert "majeure" in sentence
    assert "v2" in sentence and "v1" in sentence


def test_another_contract_is_incompatible() -> None:
    state, sentence = wire.check({"contract": {"name": "autre-produit", "version": 1}})

    assert state == wire.STATE_INCOMPATIBLE
    assert "autre-produit" in sentence


def test_a_string_version_is_read() -> None:
    assert wire.parse({"contract": {"version": "1.4"}}) == (1, 4)


# --- la route réelle ------------------------------------------------------- #


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


def test_a_version_two_uhb_puts_the_badge_in_error(monkeypatch, tmp_path) -> None:
    uhb = FakeUHB({"name": "uh-uhb", "version": 2})
    monkeypatch.setenv("UH_UHB_URL", uhb.url)
    try:
        payload = status_of(make_app(tmp_path, monkeypatch))
    finally:
        uhb.close()

    assert payload["state"] == "error"
    assert payload["contract"] == wire.STATE_INCOMPATIBLE
    assert "majeure" in payload["sentence"]
    assert payload["reason"] == "contract_mismatch"


def test_a_version_one_uhb_is_ok_and_publishes_its_contract(monkeypatch, tmp_path) -> None:
    uhb = FakeUHB({"name": "uh-uhb", "version": 1})
    monkeypatch.setenv("UH_UHB_URL", uhb.url)
    try:
        payload = status_of(make_app(tmp_path, monkeypatch))
    finally:
        uhb.close()

    assert payload["state"] == "ok"
    assert payload["contract"] == wire.STATE_OK


def test_an_old_uhb_without_contract_stays_usable(monkeypatch, tmp_path) -> None:
    uhb = FakeUHB(None)
    monkeypatch.setenv("UH_UHB_URL", uhb.url)
    try:
        payload = status_of(make_app(tmp_path, monkeypatch))
    finally:
        uhb.close()

    assert payload["state"] == "ok", "un UHB qui marche ne doit pas être refusé"
    assert payload["contract"] == wire.STATE_UNKNOWN


# --- le toolset ------------------------------------------------------------ #


def test_the_toolset_refuses_to_call_a_version_two_uhb() -> None:
    uhb = FakeUHB({"name": "uh-uhb", "version": 2})
    try:
        tools = Uhbtoolset(uhb.url, timeout=2.0)
        tools.remote = {"read_file"}

        refusal = tools.call("read_file", {"path": "x"})
    finally:
        uhb.close()

    assert refusal["reason"] == "contract_mismatch"
    assert refusal["status"] == 409
    assert uhb.posts == [], "aucun appel ne doit partir vers un contrat incompatible"


def test_the_toolset_calls_a_version_one_uhb() -> None:
    uhb = FakeUHB({"name": "uh-uhb", "version": 1})
    try:
        tools = Uhbtoolset(uhb.url, timeout=2.0)
        tools.remote = {"read_file"}

        answer = tools.call("read_file", {"path": "x"})
    finally:
        uhb.close()

    assert uhb.posts == ["/tools/read_file"], "le contrat compatible laisse passer"
    assert answer.get("ok") is True
