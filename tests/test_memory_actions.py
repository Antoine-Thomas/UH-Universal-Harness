"""The two memory actions: UHB's answer, mapped and passed through.

These tests describe the mapping, not the transport: 200 keeps UHB's body, 409
keeps the command that fixes it, 400 keeps the variable name, and a dead UHB is
"uhb_offline" - a different failure from "SiYuan is down".
"""

from __future__ import annotations

import json

import httpx
import pytest

from uh import memory_actions


@pytest.fixture(autouse=True)
def _restore_factory():
    yield
    memory_actions.set_client_factory(lambda timeout: httpx.Client(timeout=timeout))


def use(handler) -> None:
    memory_actions.set_client_factory(
        lambda timeout: httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)
    )


URL = "http://127.0.0.1:8101"


# --------------------------------------------------------------------------- #
# RAG reload
# --------------------------------------------------------------------------- #
def test_a_reload_keeps_the_service_count_and_the_time() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url).endswith("/rag/reindex")
        return httpx.Response(200, json={"reloaded": True, "service": {"fragments": 4893}})

    use(handler)
    result = memory_actions.rag_reload(URL)

    assert result["status"] == 200
    assert result["payload"]["reloaded"] is True
    assert result["payload"]["service"]["fragments"] == 4893
    assert result["payload"]["at"].endswith("+00:00") or "T" in result["payload"]["at"]


def test_a_dead_rag_service_keeps_the_command_that_starts_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"error": {"message": "the RAG service is not reachable", "type": "memory"}}
        )

    use(handler)
    result = memory_actions.rag_reload(URL)

    assert result["status"] == 409
    assert result["payload"]["kind"] == "service_offline"
    assert "serveur_rag.py" in result["payload"]["hint"]
    assert "not reachable" in result["payload"]["error"]


def test_a_dead_uhb_is_uhb_offline_not_service_offline() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    use(handler)
    result = memory_actions.rag_reload(URL)

    assert result["status"] == 502
    assert result["payload"]["kind"] == "uhb_offline"
    assert result["payload"]["error"] == "UHB unreachable"


def test_an_uhb_error_that_is_not_409_is_a_502_with_its_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    use(handler)
    result = memory_actions.rag_reload(URL)

    assert result["status"] == 502
    assert result["payload"]["kind"] == "uhb_error"
    assert "boom" in result["payload"]["error"]


def test_a_reload_never_says_reindexed() -> None:
    use(lambda request: httpx.Response(200, json={"reloaded": True, "service": {}}))

    body = json.dumps(memory_actions.rag_reload(URL))

    assert "reindexed" not in body
    assert "rebuilt" not in body


# --------------------------------------------------------------------------- #
# SiYuan blocks
# --------------------------------------------------------------------------- #
def test_blocks_are_forwarded_with_a_capped_limit() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={"count": 1, "path": "veille", "blocks": [
                {"id": "a", "type": "h", "hpath": "/veille", "content": "Titre"}
            ]},
        )

    use(handler)
    result = memory_actions.siyuan_blocks(URL, path="veille", limit=999)

    assert "limit=200" in seen["url"], "the cap lives on both sides"
    assert "path=veille" in seen["url"]
    assert result["status"] == 200
    assert result["payload"]["count"] == 1
    assert result["payload"]["blocks"][0]["content"] == "Titre"


def test_a_missing_token_keeps_the_variable_name() -> None:
    use(lambda request: httpx.Response(400, json={"error": {"message": "no token"}}))

    result = memory_actions.siyuan_blocks(URL)

    assert result["status"] == 400
    assert result["payload"]["kind"] == "config_missing"
    assert "SIYUAN_TOKEN" in result["payload"]["error"]
    assert "SIYUAN_TOKEN" in result["payload"]["hint"]


def test_a_refused_token_is_an_auth_failure() -> None:
    use(lambda request: httpx.Response(401, json={"error": {"message": "401"}}))

    result = memory_actions.siyuan_blocks(URL)

    assert result["status"] == 401
    assert result["payload"]["kind"] == "auth"
    assert "SIYUAN_TOKEN" in result["payload"]["hint"]


def test_an_unreachable_kernel_is_a_transport_failure() -> None:
    use(lambda request: httpx.Response(502, json={"error": {"message": "SiYuan is not reachable at http://x"}}))

    result = memory_actions.siyuan_blocks(URL)

    assert result["status"] == 502
    assert result["payload"]["kind"] == "transport"
    assert "unreachable" in result["payload"]["error"].lower()


def test_a_dead_uhb_during_a_block_listing_is_uhb_offline() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    use(handler)
    result = memory_actions.siyuan_blocks(URL)

    assert result["status"] == 502
    assert result["payload"]["kind"] == "uhb_offline"


def test_an_empty_path_is_not_sent_as_a_filter() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"count": 0, "blocks": []})

    use(handler)
    memory_actions.siyuan_blocks(URL, path="   ")

    assert "path=" not in seen["url"]
