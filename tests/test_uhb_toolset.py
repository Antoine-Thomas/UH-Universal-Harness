"""UHB toolset (Niveau 1): loopback-only, probe-gated, and never raises.

The HTTP layer is exercised through ``httpx.MockTransport``: a real client and a
real request/response round-trip, with the socket replaced. No test needs a
running UHB, and no test can accidentally contact one (``conftest`` sets
``UH_UHB_URL=""`` for the whole suite).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from uh.toolsets import build_toolsets, describe_toolsets
from uh.toolsets import uhb as uhb_mod

URL = "http://127.0.0.1:8101"

STATUS = {
    "status": "ok",
    "version": "0.4.2",
    "mode": "dev",
    "harness": {"name": "uh", "version": "1.0.1"},
    "providers": {
        "deepseek": {"tier": "eco", "has_key": True},
        "ollama": {"tier": "local", "url": "http://127.0.0.1:11434"},
    },
    "modes": [{"name": "chat", "allow_paid": False}, {"name": "dev"}, {"id": "review"}],
    # Anything below must never reach the model's context.
    "config": {"api_key": "must-not-leak"},
    "uptime_s": 12,
}

EXPECTED = {
    "connected": True,
    "version": "0.4.2",
    "mode": "dev",
    "harness": "uh 1.0.1",
    "providers": ["deepseek", "ollama"],
    "modes": ["chat", "dev", "review"],
}


@pytest.fixture(autouse=True)
def uhb_url(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("UH_UHB_URL", URL)
    return URL


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def answer(payload, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return client_for(handler)


def raise_connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #
def test_status_returns_only_the_compact_fields() -> None:
    out = uhb_mod.Uhbtoolset(URL, client=answer(STATUS)).call("uhb_status", {})

    assert out == EXPECTED
    assert "must-not-leak" not in repr(out)
    assert "uptime_s" not in out


def test_status_asks_the_configured_endpoint() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=STATUS)

    uhb_mod.Uhbtoolset(URL, client=client_for(handler)).status()
    assert seen == [f"{URL}/status"]


def test_a_trailing_slash_in_the_url_does_not_double_the_path() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=STATUS)

    uhb_mod.Uhbtoolset(f"{URL}/", client=client_for(handler)).status()
    assert seen == [f"{URL}/status"]


def test_a_missing_field_becomes_none_or_an_empty_list() -> None:
    out = uhb_mod.Uhbtoolset(URL, client=answer({"version": "0.4.2"})).status()

    assert out == {
        "connected": True,
        "version": "0.4.2",
        "mode": None,
        "harness": None,
        "providers": [],
        "modes": [],
    }


def test_provider_names_are_deduplicated() -> None:
    payload = {"providers": [{"name": "deepseek"}, {"name": "deepseek"}, "ollama"]}
    out = uhb_mod.Uhbtoolset(URL, client=answer(payload)).status()
    assert out["providers"] == ["deepseek", "ollama"]


# --------------------------------------------------------------------------- #
# failure modes: a tool error, never an exception
# --------------------------------------------------------------------------- #
def test_a_dead_uhb_is_a_tool_error_not_an_exception() -> None:
    toolset = uhb_mod.Uhbtoolset(URL, client=client_for(raise_connect_error))
    assert toolset.call("uhb_status", {}) == {"error": "uhb unreachable", "kind": "transport"}


def test_an_http_error_is_reported_with_its_code() -> None:
    toolset = uhb_mod.Uhbtoolset(URL, client=answer({}, status=503))
    assert toolset.call("uhb_status", {}) == {
        "error": "uhb HTTP 503",
        "kind": "http",
        "status": 503,
    }


def test_invalid_json_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>nope</html>")

    toolset = uhb_mod.Uhbtoolset(URL, client=client_for(handler))
    assert toolset.call("uhb_status", {}) == {
        "error": "uhb returned invalid JSON",
        "kind": "invalid_response",
    }


def test_a_json_list_is_not_a_status_payload() -> None:
    toolset = uhb_mod.Uhbtoolset(URL, client=answer([1, 2, 3]))
    assert toolset.call("uhb_status", {}) == {
        "error": "uhb returned invalid JSON",
        "kind": "invalid_response",
    }


def test_an_unknown_tool_is_reported() -> None:
    assert uhb_mod.Uhbtoolset(URL).call("nope", {}) == {"error": "unknown tool: nope"}


# --------------------------------------------------------------------------- #
# URL policy: loopback only
# --------------------------------------------------------------------------- #
def test_default_url_is_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UH_UHB_URL", raising=False)
    assert uhb_mod.resolve_url() == uhb_mod.DEFAULT_URL == "http://127.0.0.1:8101"


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1:8101",
        "http://localhost:8101",
        "http://[::1]:8101",
        "https://localhost:8101",
        "http://127.0.0.1",
        "http://127.0.0.1:8101/",
    ],
)
def test_loopback_urls_are_accepted(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("UH_UHB_URL", value)
    assert uhb_mod.resolve_url() == value.rstrip("/")


@pytest.mark.parametrize(
    "value",
    [
        "http://api.example.com:8101",
        "http://10.0.0.5:8101",
        "http://0.0.0.0:8101",
        "ftp://127.0.0.1:8101",
        "http://127.0.0.1@evil.example:8101",  # userinfo hides the real host
        "127.0.0.1:8101",  # no scheme
        "",
    ],
)
def test_non_loopback_urls_are_refused(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("UH_UHB_URL", value)
    assert uhb_mod.resolve_url() is None


def test_an_empty_url_disables_the_toolset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_UHB_URL", "")
    assert "uhb" not in build_toolsets()


def test_a_remote_url_is_never_contacted(monkeypatch: pytest.MonkeyPatch) -> None:
    probes: list[str] = []
    monkeypatch.setenv("UH_UHB_URL", "http://evil.example:8101")
    monkeypatch.setattr(uhb_mod, "probe", lambda url, timeout=1.0: probes.append(url) or True)

    assert "uhb" not in build_toolsets()
    assert probes == [], "a refused URL must not even be probed"


# --------------------------------------------------------------------------- #
# probe and the build_toolsets gate
# --------------------------------------------------------------------------- #
def test_probe_accepts_a_json_status() -> None:
    assert uhb_mod.probe(URL, client=answer(STATUS)) is True


@pytest.mark.parametrize("status", [404, 500, 503])
def test_probe_rejects_an_http_error(status: int) -> None:
    assert uhb_mod.probe(URL, client=answer({}, status=status)) is False


def test_probe_rejects_a_dead_uhb() -> None:
    assert uhb_mod.probe(URL, client=client_for(raise_connect_error)) is False


def test_probe_rejects_a_non_json_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    assert uhb_mod.probe(URL, client=client_for(handler)) is False


def test_probe_without_a_url_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_UHB_URL", "")
    assert uhb_mod.probe() is False


def test_toolset_is_absent_when_uhb_does_not_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(uhb_mod, "probe", lambda url, timeout=1.0: False)

    toolsets = build_toolsets()

    assert "uhb" not in toolsets
    assert "uhb:" not in "\n".join(describe_toolsets(toolsets))


def test_toolset_is_built_when_uhb_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(uhb_mod, "probe", lambda url, timeout=1.0: True)

    toolsets = build_toolsets(tmp_path)

    assert "uhb" in toolsets
    names = [spec["function"]["name"] for spec in toolsets["uhb"].tools]
    assert names == ["uhb_status", "uhb_modes", "uhb_route"]
    assert "uhb: uhb_status, uhb_modes, uhb_route" in describe_toolsets(toolsets)


def test_the_tool_result_reaches_the_agent_loop_as_a_tool_message() -> None:
    """Smoke: the toolset honours the dict contract ``run_turn`` relies on."""
    from uh.agent import run_turn

    class Scripted:
        model = "stub:1b"

        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "uhb_status", "arguments": "{}"},
                        }
                    ],
                }
            return {"role": "assistant", "content": "UHB is up.", "tool_calls": []}

    toolset = uhb_mod.Uhbtoolset(URL, client=answer(STATUS))
    history = run_turn(
        [{"role": "user", "content": "is UHB up?"}],
        Scripted(),
        {"uhb": toolset},
        max_iterations=3,
    )

    tool_messages = [m for m in history if m.get("role") == "tool"]
    assert tool_messages, "the tool result never reached the history"
    assert "ollama" in tool_messages[0]["content"]
    assert "must-not-leak" not in tool_messages[0]["content"]
