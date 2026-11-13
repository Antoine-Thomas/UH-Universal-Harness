"""UHB toolset (Niveau 2): /route and /modes, compacted, with typed errors.

Same transport discipline as Niveau 1: a real ``httpx`` client over a
``MockTransport``, so the request that leaves UH is the one under test.
"""

from __future__ import annotations

import json

import httpx
import pytest

from uh.toolsets import uhb as uhb_mod

URL = "http://127.0.0.1:8101"

DECISION = {
    "mode": "dev",
    "complexity": "standard",
    "score": 4,
    "signals": {"length": 2, "code": 1, "paths": 1},
    "allow_paid": False,
    "reason": "dev/standard -> deepseek/deepseek-flash (2 candidate(s): a > b)",
    "chain": [
        {
            "ref": "deepseek/deepseek-flash",
            "tier": "eco",
            "complexity": ["trivial", "standard"],
            "protocol": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "model_id": "deepseek-flash",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
        {"ref": "ollama/qwen3:8b", "tier": "free", "model_id": "qwen3:8b"},
    ],
}

MODES = {
    "modes": [
        {
            "name": "dev",
            "description": "Write code in the workspace",
            "kind": "coding",
            "read": ["**"],
            "write": ["uhb/**", "tests/**"],
            "deny": [".git/**"],
            "tools": ["read_file", "write_file", "patch"],
            "system_prompt": "You are UHB dev. " * 40,
            "model": {"tier": "eco", "temperature": 0.0, "max_tokens": 4096},
            "allow_paid": False,
            "max_turns": 8,
        }
    ],
    "count": 1,
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


def error(status: int, message: str) -> httpx.Client:
    return answer({"error": {"message": message, "type": "x"}}, status=status)


# --------------------------------------------------------------------------- #
# uhb_route
# --------------------------------------------------------------------------- #
def test_route_compacts_the_decision() -> None:
    out = uhb_mod.Uhbtoolset(URL, client=answer(DECISION)).call(
        "uhb_route", {"prompt": "corrige le bug", "mode": "dev"}
    )

    assert out["mode"] == "dev"
    assert out["complexity"] == "standard"
    assert out["score"] == 4
    assert out["signals"] == {"length": 2, "code": 1, "paths": 1}
    assert out["chosen"] == "deepseek/deepseek-flash"
    assert out["chain"] == [
        {"ref": "deepseek/deepseek-flash", "tier": "eco", "model_id": "deepseek-flash"},
        {"ref": "ollama/qwen3:8b", "tier": "free", "model_id": "qwen3:8b"},
    ]
    # Installation details never reach the model.
    assert "base_url" not in repr(out)
    assert "DEEPSEEK_API_KEY" not in repr(out)


def test_route_sends_only_the_fields_that_were_given() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=DECISION)

    uhb_mod.Uhbtoolset(URL, client=client_for(handler)).route("corrige le bug")
    uhb_mod.Uhbtoolset(URL, client=client_for(handler)).route("corrige", "dev", "complex")

    assert seen[0] == {"prompt": "corrige le bug"}
    assert seen[1] == {"prompt": "corrige", "mode": "dev", "complexity": "complex"}


def test_route_posts_to_the_route_endpoint() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(200, json=DECISION)

    uhb_mod.Uhbtoolset(URL, client=client_for(handler)).route("x")
    assert seen == [("POST", f"{URL}/route")]


def test_an_empty_prompt_never_leaves_the_machine() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a request was sent for an empty prompt")

    out = uhb_mod.Uhbtoolset(URL, client=client_for(handler)).route("   ")

    assert out == {"error": "prompt is required", "kind": "invalid_request"}


@pytest.mark.parametrize(
    ("status", "kind"),
    [(400, "invalid_request"), (409, "unroutable"), (500, "configuration"), (503, "http")],
)
def test_an_http_error_keeps_uhb_message_and_kind(status: int, kind: str) -> None:
    toolset = uhb_mod.Uhbtoolset(URL, client=error(status, "no model for this mode"))

    out = toolset.route("x")

    assert out["error"] == "no model for this mode"
    assert out["kind"] == kind
    assert out["status"] == status


def test_a_timeout_is_a_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    out = uhb_mod.Uhbtoolset(URL, client=client_for(handler)).route("x")

    assert out == {"error": "uhb unreachable", "kind": "transport"}


def test_a_non_json_route_answer_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>nope</html>")

    assert uhb_mod.Uhbtoolset(URL, client=client_for(handler)).route("x") == {
        "error": "uhb returned invalid JSON",
        "kind": "invalid_response",
    }


# --------------------------------------------------------------------------- #
# uhb_modes
# --------------------------------------------------------------------------- #
def test_modes_keeps_permissions_and_drops_the_prose() -> None:
    out = uhb_mod.Uhbtoolset(URL, client=answer(MODES)).call("uhb_modes", {})

    dev = out["modes"][0]
    assert dev["name"] == "dev"
    assert dev["write"] == ["uhb/**", "tests/**"]
    assert dev["tools"] == ["read_file", "write_file", "patch"]
    assert dev["model"] == {"tier": "eco", "max_tokens": 4096}
    assert "system_prompt" not in dev
    assert "You are UHB dev" not in repr(out)


def test_modes_reports_a_broken_installation_as_configuration() -> None:
    toolset = uhb_mod.Uhbtoolset(URL, client=error(500, "mode catalog is not usable"))
    out = toolset.modes()
    assert out["kind"] == "configuration"


def test_modes_without_a_modes_list_is_an_invalid_response() -> None:
    out = uhb_mod.Uhbtoolset(URL, client=answer({"count": 0})).modes()
    assert out == {"error": "uhb returned invalid JSON", "kind": "invalid_response"}


# --------------------------------------------------------------------------- #
# the contract gate
# --------------------------------------------------------------------------- #
def test_probe_accepts_a_uhb_that_exposes_the_contract() -> None:
    assert uhb_mod.probe(URL, client=answer(MODES)) is True


def test_probe_rejects_a_uhb_that_only_knows_status() -> None:
    """An older UHB answers /modes with 404: no toolset, rather than half a one."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/modes":
            return httpx.Response(404, json={"error": {"message": "not found"}})
        return httpx.Response(200, json={"version": "0.4.2"})

    assert uhb_mod.probe(URL, client=client_for(handler)) is False


def test_an_unknown_tool_says_so() -> None:
    assert uhb_mod.Uhbtoolset(URL).call("uhb_nope", {}) == {"error": "unknown tool: uhb_nope"}


# --------------------------------------------------------------------------- #
# the loop through the agent
# --------------------------------------------------------------------------- #
def test_a_route_decision_reaches_the_history_as_a_tool_message() -> None:
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
                            "function": {
                                "name": "uhb_route",
                                "arguments": json.dumps({"prompt": "corrige le bug", "mode": "dev"}),
                            },
                        }
                    ],
                }
            return {"role": "assistant", "content": "UHB choisirait deepseek-flash.", "tool_calls": []}

    history = run_turn(
        [{"role": "user", "content": "que ferait UHB ?"}],
        Scripted(),
        {"uhb": uhb_mod.Uhbtoolset(URL, client=answer(DECISION))},
        max_iterations=3,
    )

    tool_messages = [m for m in history if m.get("role") == "tool"]
    assert tool_messages
    assert "deepseek/deepseek-flash" in tool_messages[0]["content"]
    assert "DEEPSEEK_API_KEY" not in tool_messages[0]["content"]
