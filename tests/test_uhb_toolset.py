"""UHB toolset (Niveau 1): loopback-only, probe-gated, and never raises.

The HTTP layer is exercised through ``httpx.MockTransport``: a real client and a
real request/response round-trip, with the socket replaced. No test needs a
running UHB, and no test can accidentally contact one (``conftest`` sets
``UH_UHB_URL=""`` for the whole suite).
"""

from __future__ import annotations

import json
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
        "type": "tool_error",
    }


def test_a_403_is_a_forbidden_refusal_carrying_uhbs_reason() -> None:
    """UHB is the authority: its 403 reaches the model in the same vocabulary the
    loop uses when it refuses a tool it was never offered."""
    payload = {"error": {"message": "mode 'chat' does not allow execute_code",
                         "type": "forbidden"}}
    toolset = uhb_mod.Uhbtoolset(URL, client=answer(payload, status=403))

    result = toolset.call("uhb_status", {})

    assert result["type"] == "forbidden"
    assert result["status"] == 403
    assert "execute_code" in result["error"], "the reason comes from UHB, not from a guess"


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
    result = uhb_mod.Uhbtoolset(URL).call("nope", {})
    assert result["error"] == "unknown tool: nope"
    assert result["type"] == "forbidden", "one vocabulary with the agent loop"


def test_the_catalogue_adds_the_tools_this_file_does_not_name() -> None:
    """UH transports what UHB catalogs: no second list of tool names here."""
    catalogue = {
        "tools": [
            {"name": "desktop_open_url", "description": "Open a URL.",
             "parameters": {"type": "object", "properties": {"url": {"type": "string"}},
                            "required": ["url"]},
             "execution_type": "in_process"},
            {"name": "execute_code", "description": "Run code.",
             "parameters": {"type": "object", "properties": {"code": {"type": "string"}}},
             "execution_type": "subprocess"},
        ]
    }
    posted: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=catalogue)
        posted.append((str(request.url), json.loads(request.content or b"{}")))
        return httpx.Response(200, json={"ok": True, "url": "https://x", "request_id": "r1"})

    asked: list[str] = []

    def allow(tool: str, _arguments: dict) -> bool:
        asked.append(tool)
        return True

    toolset = uhb_mod.Uhbtoolset(URL, client=client_for(handler), discover=True, approver=allow)

    names = [spec["function"]["name"] for spec in toolset.tools]
    assert "desktop_open_url" in names
    assert "execute_code" not in names, "a subprocess tool is not offered to the chat"

    result = toolset.call("desktop_open_url", {"url": "https://x"})
    assert result["ok"] is True
    assert asked == ["desktop_open_url"], "a desktop_* tool asks before it acts (P3-15)"
    assert posted and posted[0][0].endswith("/tools/desktop_open_url")
    assert posted[0][1] == {"url": "https://x"}


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
    """The four built-ins, plus whatever the catalogue published.

    Discovery is what depends on the machine: a live UHB publishes its own
    catalogue, and its size grows with UHB's roadmap (the desktop tools arrived
    that way). Scripting it here keeps the test about the mechanism — the
    catalogue is relayed, not re-declared — instead of about tonight's UHB.
    """
    monkeypatch.setattr(uhb_mod, "probe", lambda url, timeout=1.0: True)

    def fake_discover(self) -> None:
        self.remote.add("desktop_windows")
        self.tools.append(
            {
                "type": "function",
                "function": {
                    "name": "desktop_windows",
                    "description": "catalogued by UHB",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        )

    monkeypatch.setattr(uhb_mod.Uhbtoolset, "_discover", fake_discover)

    toolsets = build_toolsets(tmp_path)

    assert "uhb" in toolsets
    toolset = toolsets["uhb"]
    names = [spec["function"]["name"] for spec in toolset.tools]
    assert {"uhb_status", "uhb_modes", "uhb_route", "describe_tools"} <= set(names)
    assert "desktop_windows" in names, "what discovery published reaches the model"
    assert "desktop_windows" in toolset.remote, "and it is called through the generic route"
    described = describe_toolsets(toolsets)
    assert "uhb: uhb_status, uhb_modes, uhb_route, describe_tools" in "\n".join(described)


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


# --------------------------------------------------------------------------- #
# describe_tools: consultation, jamais une permission
# --------------------------------------------------------------------------- #
CATALOG = {
    "tools": [
        {
            "name": "execute_code",
            "description": "Execute Python code in the authorized workspace.",
            "parameters": {"type": "object", "properties": {"code": {"type": "string"}},
                           "required": ["code"]},
            "category": "code",
            "execution_type": "subprocess",
            "version": "1",
            "enabled": True,
            "required_permissions": [],
            "compatible_modes": ["dev"],
            "available": False,
            "reason": {"code": "not_in_mode", "mode": "chat", "available_in": ["dev"],
                       "hint": "the current mode does not declare this tool"},
        }
    ],
    "count": 1,
    "mode": "chat",
    "request_id": "abc123",
}


def test_describe_tools_passes_uhbs_catalogue_through_untouched() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=CATALOG)

    toolset = uhb_mod.Uhbtoolset(URL, client=client_for(handler))

    assert toolset.call("describe_tools", {}) == CATALOG
    assert seen[0].method == "GET", "consultation: this tool must never POST"
    assert str(seen[0].url).endswith("/tools")


def test_describe_tools_details_one_name_and_forwards_the_mode() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"tool": CATALOG["tools"][0], "count": 1, "mode": "dev"})

    toolset = uhb_mod.Uhbtoolset(URL, client=client_for(handler))
    toolset.call("describe_tools", {"name": "execute_code", "mode": "dev"})

    url = str(seen[0].url)
    assert url.endswith("/tools/execute_code?mode=dev")


def test_an_unknown_tool_from_uhb_stays_a_structured_tool_error() -> None:
    payload = {"error": {"message": "unknown tool 'nope'; known: execute_code",
                         "type": "not_found", "hint": "GET /tools lists every name"}}
    toolset = uhb_mod.Uhbtoolset(URL, client=answer(payload, status=404))

    result = toolset.call("describe_tools", {"name": "nope"})

    assert result["status"] == 404
    assert result["type"] == "tool_error"
    assert "known: execute_code" in result["error"]


# --------------------------------------------------------------------------- #
# P0-4: execute_code, deux conditions et aucun second chemin
# --------------------------------------------------------------------------- #
def _catalogue_with_execute() -> dict:
    return {
        "tools": [
            {"name": "execute_code", "description": "Run Python.",
             "parameters": {"type": "object", "properties": {"code": {"type": "string"}},
                            "required": ["code"]},
             "execution_type": "subprocess"},
        ]
    }


def test_execute_code_is_not_offered_without_the_switch(monkeypatch) -> None:
    monkeypatch.delenv(uhb_mod.EXECUTE_ENV, raising=False)
    toolset = uhb_mod.Uhbtoolset(
        URL, client=client_for(lambda request: httpx.Response(200, json=_catalogue_with_execute())),
        discover=True,
    )

    assert "execute_code" not in [spec["function"]["name"] for spec in toolset.tools]


def test_execute_code_is_offered_with_the_switch_and_goes_to_its_own_route(monkeypatch) -> None:
    monkeypatch.setenv(uhb_mod.EXECUTE_ENV, "1")
    posted: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_catalogue_with_execute())
        if request.url.path == "/tools/approvals":
            return httpx.Response(200, json={"approval_id": "nonce-route",
                                             "tool": "execute_code", "expires_in": 120})
        posted.append((str(request.url), json.loads(request.content or b"{}")))
        return httpx.Response(200, json={"ok": True, "stdout": "4\n", "exit_code": 0})

    toolset = uhb_mod.Uhbtoolset(
        URL, client=client_for(handler), discover=True, approver=lambda _t, _a: True
    )

    assert "execute_code" in [spec["function"]["name"] for spec in toolset.tools]
    result = toolset.call("execute_code", {"code": "print(2+2)"})

    assert result["ok"] is True
    assert posted[0][0].endswith("/tools/execute"), "its own route, not the generic one"
    assert posted[0][1]["code"] == "print(2+2)"
    assert posted[0][1]["approval_id"] == "nonce-route", "the nonce UHB minted travels with it"


def test_a_refusal_from_uhb_keeps_its_type_and_message(monkeypatch) -> None:
    """UH keeps no whitelist: the 403 comes from the mode, and it is shown as such."""
    monkeypatch.setenv(uhb_mod.EXECUTE_ENV, "1")
    refusal = {"error": {"message": "mode 'chat' does not allow execute_code",
                         "type": "forbidden"}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_catalogue_with_execute())
        if request.url.path == "/tools/approvals":
            return httpx.Response(200, json={"approval_id": "nonce-refused",
                                             "tool": "execute_code", "expires_in": 120})
        return httpx.Response(403, json=refusal)

    toolset = uhb_mod.Uhbtoolset(
        URL, client=client_for(handler), discover=True, approver=lambda _t, _a: True
    )
    result = toolset.call("execute_code", {"code": "print(1)"})

    assert result["type"] == "forbidden"
    assert result["status"] == 403
    assert "does not allow execute_code" in result["error"]


def test_a_refusal_caused_by_the_mode_is_marked_as_such() -> None:
    """`mode 'chat' has no tool 'execute_code'` is not a permission bug.

    The tool exists; the profile does not carry it. Marking it lets the page offer
    the only useful action - one click that grants execution - instead of sending
    the user to look for a setting (measured dead end, 19/11).
    """
    import httpx

    from uh.toolsets import uhb as toolset

    body = {"error": "mode 'chat' has no tool 'execute_code'"}
    response = httpx.Response(403, json=body, request=httpx.Request("POST", "http://127.0.0.1:8101/tools/execute"))

    payload = toolset._failure(response)

    assert payload["type"] == "forbidden"
    assert payload["reason"] == "mode_forbidden"
    assert payload["mode"] == "chat", "the page needs to know which profile refused"


def test_a_plain_403_is_not_mistaken_for_a_mode_refusal() -> None:
    import httpx

    from uh.toolsets import uhb as toolset

    response = httpx.Response(
        403,
        json={"error": "approval required", "reason": "approval_required"},
        request=httpx.Request("POST", "http://127.0.0.1:8101/tools/execute"),
    )

    payload = toolset._failure(response)

    assert "reason" not in payload or payload["reason"] != "mode_forbidden"
