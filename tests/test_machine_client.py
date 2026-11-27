"""Le panneau Machine ne décide de rien : il transporte, et il refuse sans accord.

Trois choses sont prouvées ici, et chacune par l'entrée réelle du module :

* sans ``accept`` explicite, **aucune** requête ne part vers UHB (compteur à zéro) ;
* avec l'accord, une seule requête part, sur ``/models/pull``, avec ``accept: true`` ;
* UHB absent ou en erreur devient un refus nommé, jamais un succès silencieux.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from uh import machine_client


def transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def uhb_url(monkeypatch) -> None:
    monkeypatch.setenv("UH_UHB_URL", "http://127.0.0.1:8101")


SCAN = {
    "profile": {
        "os": "Windows",
        "gpu": {
            "vendor": "nvidia",
            "name": "RTX 3070 Ti",
            "vram_total_gb": 8.0,
            "vram_free_gb": 6.05,
            "source": "nvidia-smi",
        },
        "ram": {"total_gb": 63.9, "free_gb": 38.4, "source": "GlobalMemoryStatusEx"},
        "unknown": ["disk_home_free_gb"],
    },
    "catalog": {"version": "1", "updated": "2026-11-27", "count": 5},
    "entry": {
        "id": "qwen2.5:7b",
        "mode": "vram",
        "warning": None,
        "default_checked": True,
        "needs": {"vram_gb": 5.7},
        "verified": True,
        "license": "Apache-2.0",
        "file_gb": 4.7,
    },
    "alternatives": [
        {
            "id": "inkling-2-mini:26b-a5b-iq4_NL",
            "mode": "offload",
            "warning": "ne tient pas en 8 Go de VRAM : experts en RAM, lenteur non mesurée",
            "default_checked": False,
            "verified": False,
            "file_gb": 18.0,
            "needs": {"vram_gb": 19.3},
            "license": "Apache-2.0",
        },
    ],
    "reasons": ["6.05 Go de VRAM libres : qwen2.5:7b tient avec la marge de 0.8 Go"],
}


def test_a_scan_is_a_single_post_and_comes_back_untouched() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SCAN)

    with transport(handler) as client:
        payload = machine_client.scan(client=client)

    assert payload == SCAN
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/machine/scan"
    assert json.loads(seen[0].content) == {"category": "chat"}


def test_without_accept_nothing_leaves_and_the_sentence_says_so() -> None:
    sent: list[httpx.Request] = []

    with transport(lambda request: sent.append(request) or httpx.Response(202, json={})) as http:
        with pytest.raises(machine_client.MachineRefused) as refusal:
            machine_client.start_pull("qwen2.5:7b", accept=False, client=http)

    assert refusal.value.status == 403
    assert "accord explicite" in str(refusal.value)
    assert sent == [], "aucune requête ne doit partir sans accord"


def test_with_accept_one_request_carries_the_consent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"started": True, "id": "42", "mode": "vram"})

    with transport(handler) as http:
        started = machine_client.start_pull("qwen2.5:7b", accept=True, client=http)

    assert started["started"] is True
    assert len(seen) == 1
    assert seen[0].url.path == "/models/pull"
    assert json.loads(seen[0].content) == {"entry_id": "qwen2.5:7b", "accept": True}


def test_uhbs_own_refusal_keeps_its_word() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"error": {"message": "inkling ne tient pas ici : 19.3 Go de VRAM requis"}},
        )

    with transport(handler) as http:
        with pytest.raises(machine_client.MachineRefused) as refusal:
            machine_client.start_pull("inkling-2-mini:26b-a5b-iq4_NL", accept=True, client=http)

    assert refusal.value.status == 409
    assert "19.3 Go" in str(refusal.value)


def test_an_absent_uhb_is_named_not_guessed(monkeypatch) -> None:
    monkeypatch.setenv("UH_UHB_URL", "")

    with pytest.raises(machine_client.MachineUnavailable) as refusal:
        machine_client.scan()

    assert "UHB n'est pas configuré" in str(refusal.value)


def test_a_broken_uhb_becomes_502() -> None:
    with transport(lambda request: httpx.Response(500, text="boom")) as http:
        with pytest.raises(machine_client.MachineUnavailable) as refusal:
            machine_client.scan(client=http)

    assert refusal.value.status == 502


def test_a_network_failure_is_a_named_refusal() -> None:
    def handler(request: httpx.Request) -> Any:
        raise httpx.ConnectError("refusé", request=request)

    with transport(handler) as http:
        with pytest.raises(machine_client.MachineUnavailable) as refusal:
            machine_client.scan(client=http)

    assert "injoignable" in str(refusal.value)


def test_pull_status_reads_the_route_uhb_publishes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"id": "abc", "state": "downloading", "completed": 2, "total": 18}
        )

    with transport(handler) as http:
        payload = machine_client.pull_status("abc", client=http)

    assert payload["state"] == "downloading"
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/models/pull/abc"
