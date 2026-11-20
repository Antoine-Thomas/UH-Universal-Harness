"""Supervision Infra (P3-16c lot 3) : ce qu'elle lit, ce qu'elle masque, ce qu'elle refuse.

Deux familles de preuves :
- le module (`uh/infra.py`) sur des arbres temporaires : tailles exactes, journaux bornés,
  secrets masqués, sonde d'un port fermé ;
- la route sur le vrai chemin HTTP, avec un UHB pointé sur un port fermé : la vue doit
  passer au rouge avec une raison explicite, jamais un « hors ligne » muet.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import infra as uh_infra  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-infra"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def run(coro):
    return asyncio.run(coro)


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _Stub:
    model = "stub:1b"
    streaming = True

    def chat(self, messages, tools, *, on_token=None):
        return {"role": "assistant", "content": "ok", "tool_calls": []}


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


# --------------------------------------------------------------------------- #
# le module
# --------------------------------------------------------------------------- #
def test_a_token_is_masked_before_it_leaves() -> None:
    line = 'GET /?token=abc123SECRET HTTP/1.1" 200 OK'
    assert "abc123SECRET" not in uh_infra.redact(line)
    assert "token=***" in uh_infra.redact(line)


def test_authorization_headers_and_keys_are_masked() -> None:
    for raw, secret in [
        ("Authorization: Bearer sk-live-abcdefghijklmnop", "abcdefghijklmnop"),
        ("x-api-key: sk-ant-api03-ZZZZZZZZZZZZ", "ZZZZZZZZZZZZ"),
        ("key = hf_ABCDEFGHIJKLMNOPQRSTUV", "ABCDEFGHIJKLMNOPQRSTUV"),
        ("GOOGLE=AIzaSyA1234567890abcdefghijklmnop", "AIzaSyA1234567890abcdefghijklmnop"),
    ]:
        out = uh_infra.redact(raw)
        assert secret not in out, f"{secret} a survécu au masquage de {raw!r}"


def test_an_absent_log_is_said_not_invented(tmp_path: Path) -> None:
    report = uh_infra.tail_lines(tmp_path / "absent.log")
    assert report["exists"] is False
    assert report["lines"] == []
    assert report["reason"] == "absent"


def test_the_log_returns_only_its_tail(tmp_path: Path) -> None:
    log = tmp_path / "uh-web.log"
    log.write_text("\n".join(f"ligne {index}" for index in range(5000)) + "\n", encoding="utf-8")
    report = uh_infra.tail_lines(log, 20)
    assert report["returned"] == 20
    assert report["lines"][-1] == "ligne 4999"
    assert "ligne 0\n" not in "\n".join(report["lines"]) + "\n", "les vieilles lignes ne remontent pas"
    # Ce fichier tient dans la fenêtre de lecture : rien n'a été coupé en tête.
    assert report["head_cut"] is False


def test_a_huge_log_is_read_by_bytes_not_whole(tmp_path: Path) -> None:
    log = tmp_path / "gros.log"
    log.write_bytes(b"x" * (uh_infra.MAX_LOG_BYTES * 3) + b"\nfin\n")
    report = uh_infra.tail_lines(log)
    assert report["lines"][-1] == "fin"
    assert report["bytes"] == uh_infra.MAX_LOG_BYTES * 3 + 5


def test_a_secret_in_the_log_does_not_reach_the_report(tmp_path: Path) -> None:
    log = tmp_path / "uh-web.log"
    log.write_text('GET /?token=VERYSECRETTOKEN HTTP/1.1" 200\n', encoding="utf-8")
    report = uh_infra.tail_lines(log)
    assert "VERYSECRETTOKEN" not in "\n".join(report["lines"])


def test_disk_sizes_are_the_real_ones(tmp_path: Path) -> None:
    tree = tmp_path / "arbre"
    (tree / "sous").mkdir(parents=True)
    (tree / "a.txt").write_bytes(b"12345")
    (tree / "sous" / "b.bin").write_bytes(b"1234567890")
    report = uh_infra.directory_size(tree)
    assert report["bytes"] == 15
    assert report["files"] == 2
    assert report["complete"] is True


def test_an_absent_directory_is_reported_absent(tmp_path: Path) -> None:
    report = uh_infra.directory_size(tmp_path / "jamais")
    assert report["exists"] is False
    assert report["bytes"] == 0
    assert report["reason"] == "absent"


def test_a_closed_port_is_not_listening_and_says_why() -> None:
    report = uh_infra.probe(f"http://127.0.0.1:{closed_port()}")
    assert report["listening"] is False
    assert report["reason"], "un port fermé doit dire pourquoi, pas seulement « non »"


def test_an_open_port_is_listening() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        report = uh_infra.probe(f"http://127.0.0.1:{port}")
    assert report["listening"] is True
    assert report["port"] == port


# --------------------------------------------------------------------------- #
# la route
# --------------------------------------------------------------------------- #
@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    uh_home = tmp_path / "uh-home"
    uh_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UH_HOME", str(uh_home))
    runs = tmp_path / "runs"
    (runs / "run-1").mkdir(parents=True)
    (runs / "run-1" / "a.bin").write_bytes(b"z" * 1000)
    (runs / "run-1" / "b.bin").write_bytes(b"z" * 24)
    (uh_home / "brouillon.txt").write_bytes(b"c" * 500)
    (uh_home / "uh-web.log").write_text(
        "\n".join(f"ligne {i}" for i in range(300)) + '\nGET /?token=SECRET123 200\n',
        encoding="utf-8",
    )
    (uh_home / "uhb.log").write_text("second cerveau\n", encoding="utf-8")
    monkeypatch.setenv("UH_UHB_RUNS", str(runs))
    monkeypatch.setenv("UH_UHB_URL", f"http://127.0.0.1:{closed_port()}")
    return uh_home


@pytest.fixture()
def app(home: Path, tmp_path: Path):
    return create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=tmp_path,
        provider_factory=lambda kind, model: _Stub(),
        kind="stub",
        model="stub:1b",
    )


def snapshot(app, **params) -> dict:
    async def go():
        async with client(app) as http:
            response = await http.get("/api/infra", headers=AUTH, params=params)
            assert response.status_code == 200, response.text
            return response.json()

    return run(go())


def test_the_route_requires_the_token(app) -> None:
    async def go():
        async with client(app) as http:
            return await http.get("/api/infra")

    assert run(go()).status_code == 401


def test_a_dead_second_brain_turns_the_view_red_with_a_reason(app) -> None:
    body = snapshot(app)
    uhb = next(service for service in body["services"] if service["name"] == "UHB")
    assert uhb["state"] == "down"
    reason = uhb["reason"].lower()
    assert reason and reason != "uhb unreachable", "le refus doit être explicite, pas un « hors ligne » muet"
    assert "port" in reason, f"la raison nomme le port : {uhb['reason']!r}"
    assert any(
        marker in reason for marker in ("refus", "connect", "timed out", "timeout", "unreachable")
    ), uhb["reason"]
    levels = {alert["level"] for alert in body["alerts"]}
    assert "error" in levels
    labels = " ".join(alert["label"].lower() for alert in body["alerts"])
    assert "cerveau" in labels


def test_disk_sizes_match_the_disk(app, home: Path) -> None:
    body = snapshot(app)
    assert body["disk"]["home"]["bytes"] == 500 + len(
        (home / "uh-web.log").read_bytes() + (home / "uhb.log").read_bytes()
    ), "la taille de ~/.uh est celle des fichiers sur disque"
    assert body["disk"]["home"]["files"] == 3
    assert body["disk"]["runs"]["bytes"] == 1024
    assert body["disk"]["runs"]["files"] == 2
    assert body["disk"]["free"]["free_bytes"] > 0


def test_the_logs_are_bounded_and_masked(app) -> None:
    body = snapshot(app, log_lines=10)
    web = next(log for log in body["logs"] if log["name"] == "uh-web.log")
    assert web["exists"] is True
    assert web["returned"] == 10
    assert "SECRET123" not in "\n".join(web["lines"])
    assert any("token=***" in line for line in web["lines"])
    uhb_log = next(log for log in body["logs"] if log["name"] == "uhb.log")
    assert uhb_log["lines"] == ["second cerveau"]


def test_the_view_is_read_only_by_contract(app) -> None:
    body = snapshot(app)
    assert body["read_only"] is True
    assert body["actions_available"] == []
    # Aucune route d'action n'existe sous /api/infra : rien à arrêter, rien à relancer.
    routes = {(getattr(route, "path", ""), tuple(getattr(route, "methods", []) or [])) for route in app.routes}
    for path, methods in routes:
        if path.startswith("/api/infra"):
            assert "GET" in methods
            assert "POST" not in methods and "DELETE" not in methods and "PUT" not in methods


def test_the_install_block_says_where_the_code_comes_from(app) -> None:
    body = snapshot(app)
    install = body["install"]
    assert install["origin"] in {"dépôt", "paquet installé"}
    assert "python" in install and install["python"]
    assert body["services"][0]["name"] == "UH"
    assert body["services"][0]["uptime_s"] >= 0


def test_an_alert_is_ok_when_nothing_is_wrong(app, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans panne, la vue ne crie pas : une alerte « ok » dit que tout est dans les seuils."""
    monkeypatch.setenv("UH_UHB_URL", "http://127.0.0.1:1")  # port fermé, mais on teste le cas

    class _Reachable:
        def status(self):
            return {"connected": True, "mode": "dev", "version": "0.3.0"}

    from uh.toolsets import uhb as uhb_module

    monkeypatch.setattr(uhb_module, "Uhbtoolset", lambda *a, **k: _Reachable())
    monkeypatch.setattr(uh_infra, "probe", lambda url, timeout=0.4: {"listening": True, "port": 8101, "reason": ""})
    body = snapshot(app)
    levels = [alert["level"] for alert in body["alerts"]]
    assert "error" not in levels
