"""The MCP client: a real stdio server, and every way one can misbehave.

The fake server below is a real child process speaking real newline-delimited
JSON-RPC, because the transport *is* the feature: a mock would prove that UH can
call a mock. It answers the three methods, and it also hangs, dies mid-call,
returns a JSON-RPC error, and writes eleven megabytes on one line - the five
failures that turn "we support MCP" into a crashed session if nobody tests them.

Nothing here needs an installed MCP server or a network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from uh import config as uh_config
from uh import mcp

FAKE_SERVER = r'''
import json
import os
import sys
import time

TOOLS = [
    {"name": "echo", "description": "Echo the text back",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "long", "description": "A very long answer",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "image", "description": "An image",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "boom", "description": "Answers with a JSON-RPC error",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "die", "description": "Exits mid-call",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hang", "description": "Never answers",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "huge", "description": "One oversized line",
     "inputSchema": {"type": "object", "properties": {}}},
]


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "9.9"}}})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "echo":
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": arguments.get("text", "")}]}})
        elif name == "long":
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": "x" * 300000}]}})
        elif name == "image":
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "image", "mimeType": "image/png", "data": "A" * 1000}]}})
        elif name == "boom":
            send({"jsonrpc": "2.0", "id": request_id, "error": {
                "code": -32000, "message": "the tool exploded"}})
        elif name == "die":
            os._exit(3)
        elif name == "hang":
            time.sleep(30)
        elif name == "huge":
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": "y" * (11 * 1024 * 1024)}]}})
        else:
            send({"jsonrpc": "2.0", "id": request_id, "error": {
                "code": -32601, "message": "no such tool"}})
'''


@pytest.fixture(autouse=True)
def no_leaked_servers():
    """Every test starts real processes: none may outlive its test."""
    yield
    mcp.shutdown()


@pytest.fixture
def make_server(tmp_path: Path):
    def build(name: str = "fake", timeout: float = 5.0, **overrides) -> mcp.ServerConfig:
        script = tmp_path / f"{name}-server.py"
        script.write_text(FAKE_SERVER, encoding="utf-8")
        return mcp.ServerConfig(
            name=name,
            command=sys.executable,
            args=(str(script),),
            timeout=timeout,
            **overrides,
        )

    return build


def table(servers: list[dict]) -> uh_config.Config:
    """A Config with an in-memory ``[mcp]`` table: no file, no side effect."""
    return uh_config.Config(values=dict(uh_config.DEFAULTS), tables={"mcp": {"servers": servers}})


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def test_a_server_entry_is_read_from_the_mcp_table(tmp_path: Path) -> None:
    script = tmp_path / "s.py"
    script.write_text("", encoding="utf-8")
    cfg = table(
        [
            {
                "name": "filesystem",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", str(tmp_path)],
                "timeout": 12,
                "allow": ["read_file"],
                "env": {"LOG_LEVEL": "error"},
                "cwd": str(tmp_path),
            }
        ]
    )

    servers, problems = mcp.servers_from_config(cfg)

    assert problems == []
    assert len(servers) == 1
    server = servers[0]
    assert server.name == "filesystem"
    assert server.command == "npx"
    assert server.args[-1] == str(tmp_path)
    assert server.timeout == 12.0
    assert server.allow == ("read_file",)
    assert server.env == {"LOG_LEVEL": "error"}
    assert server.cwd == str(tmp_path)


def test_a_broken_entry_does_not_hide_the_others() -> None:
    cfg = table(
        [
            {"name": "good", "command": "python"},
            {"command": "python"},  # no name
            {"name": "no command"},
            {"name": "bad name", "command": "python"},
            {"name": "good", "command": "python"},  # duplicate
            {"name": "env", "command": "python", "env": {"A": 1}},
        ]
    )

    servers, problems = mcp.servers_from_config(cfg)

    assert [server.name for server in servers] == ["good"]
    assert len(problems) == 5
    assert any("duplicate name 'good'" in problem for problem in problems)
    assert any("may only contain" in problem for problem in problems)


def test_no_mcp_table_is_not_an_error() -> None:
    servers, problems = mcp.servers_from_config(uh_config.Config(values={}))

    assert servers == []
    assert problems == []


def test_config_toml_carries_the_server_list(isolated_home: Path) -> None:
    """``[[mcp.servers]]`` must survive ``config.load`` and reach the client."""
    path = isolated_home / "config.toml"
    path.write_text(
        '[[mcp.servers]]\nname = "notes"\ncommand = "python"\nargs = ["-m", "server"]\n',
        encoding="utf-8",
    )

    servers, problems = mcp.servers_from_config(uh_config.load())

    assert problems == []
    assert [server.name for server in servers] == ["notes"]
    assert servers[0].args == ("-m", "server")


def test_an_allowlist_hides_the_tools_it_does_not_name(make_server) -> None:
    server = make_server(allow=("echo",))
    client = mcp.client_for(server)

    assert [tool["name"] for tool in client.tools] == ["echo"]


def test_the_status_says_supported_and_counts_nothing_when_unconfigured() -> None:
    payload = mcp.status(uh_config.Config(values={}))

    assert payload["supported"] is True
    assert payload["configured"] == 0
    assert "no [[mcp.servers]]" in payload["reason"]


# --------------------------------------------------------------------------- #
# the handshake and the toolset the agent loop sees
# --------------------------------------------------------------------------- #
def test_handshake_and_namespaced_tools(make_server) -> None:
    client = mcp.client_for(make_server())

    assert client.protocol_version == "2024-11-05"
    assert client.server_info["name"] == "fake"
    toolset = mcp.Toolset(client)
    names = [spec["function"]["name"] for spec in toolset.tools]

    assert "mcp__fake__echo" in names
    assert all(name.startswith("mcp__fake__") for name in names)
    assert toolset.name == "mcp:fake"


def test_a_call_reaches_the_server_under_the_raw_name(make_server) -> None:
    toolset = mcp.Toolset(mcp.client_for(make_server()))

    result = toolset.call("mcp__fake__echo", {"text": "bonjour"})

    assert result["text"] == "bonjour"
    assert result["server"] == "fake"
    assert "error" not in result


def test_an_unknown_tool_is_a_tool_result_not_an_exception(make_server) -> None:
    toolset = mcp.Toolset(mcp.client_for(make_server()))

    result = toolset.call("mcp__fake__nope", {})

    assert result["kind"] == "invalid_request"
    assert "unknown MCP tool" in result["error"]


def test_an_image_is_described_never_inlined(make_server) -> None:
    """Base64 in the context window is tens of thousands of tokens of noise."""
    toolset = mcp.Toolset(mcp.client_for(make_server()))

    result = toolset.call("mcp__fake__image", {})

    assert result["text"] == "[image: image/png, 1000 base64 characters]"


def test_a_long_result_is_truncated_with_a_marker(make_server) -> None:
    toolset = mcp.Toolset(mcp.client_for(make_server()))

    result = toolset.call("mcp__fake__long", {})

    assert len(result["text"]) < mcp.MAX_RESULT_CHARS + 200
    assert "truncated by UH" in result["text"]


# --------------------------------------------------------------------------- #
# the five ways a server breaks
# --------------------------------------------------------------------------- #
def test_a_server_that_never_answers_times_out(make_server) -> None:
    client = mcp.client_for(make_server(timeout=0.5))

    with pytest.raises(mcp.MCPError) as caught:
        client.call("hang", {})

    assert caught.value.kind == "timeout"
    assert "did not answer tools/call" in str(caught.value)


def test_a_server_that_dies_mid_call_is_a_transport_error(make_server) -> None:
    toolset = mcp.Toolset(mcp.client_for(make_server()))

    result = toolset.call("mcp__fake__die", {})

    assert result["kind"] == "transport"
    assert "exited" in result["error"]


def test_a_json_rpc_error_is_surfaced_with_its_message(make_server) -> None:
    client = mcp.client_for(make_server())

    with pytest.raises(mcp.MCPError) as caught:
        client.call("boom", {})

    assert caught.value.kind == "server"
    assert "the tool exploded" in str(caught.value)


def test_an_oversized_message_is_refused_not_buffered(make_server) -> None:
    """11 MB on one line: the reader drains it and reports, the harness survives.

    The budget is generous on purpose: the point is *refused instead of buffered*,
    not speed, and reading 11 MB through a pipe on a busy machine took most of a
    15 s timeout - a race, not a behaviour.
    """
    client = mcp.client_for(make_server(timeout=60.0))

    with pytest.raises(mcp.MCPError) as caught:
        client.call("huge", {})

    assert caught.value.kind == "protocol"
    assert str(mcp.MAX_MESSAGE_BYTES) in str(caught.value)


def test_a_missing_command_is_a_configuration_error_and_is_not_retried(
    tmp_path: Path,
) -> None:
    server = mcp.ServerConfig(
        name="ghost", command=str(tmp_path / "does-not-exist"), args=(), timeout=1.0
    )

    with pytest.raises(mcp.MCPError) as caught:
        mcp.client_for(server)
    assert caught.value.kind == "configuration"

    # The cockpit polls: the second ask must not wait for another timeout.
    with pytest.raises(mcp.MCPError) as again:
        mcp.client_for(server)
    assert again.value.kind == "unavailable"

    payload = mcp.status(table([{"name": "ghost", "command": server.command}]))
    assert payload["servers"][0]["state"] == "failed"
    assert payload["servers"][0]["connected"] is False


# --------------------------------------------------------------------------- #
# integration points
# --------------------------------------------------------------------------- #
def test_build_toolsets_starts_only_what_answers(make_server, tmp_path: Path) -> None:
    good = make_server(name="good")
    cfg = table(
        [
            {"name": "good", "command": good.command, "args": list(good.args)},
            {"name": "ghost", "command": str(tmp_path / "nope")},
        ]
    )

    toolsets = mcp.build_toolsets(cfg)

    assert list(toolsets) == ["mcp:good"]


def test_the_agent_loop_toolsets_include_mcp(monkeypatch) -> None:
    """``uh.toolsets.build_toolsets`` is what ``run_turn`` and the cockpit call."""
    from uh import toolsets as uh_toolsets

    class FakeToolset:
        tools = [{"type": "function", "function": {"name": "mcp__x__y"}}]

        def call(self, name, arguments=None):
            return {"text": "ok"}

    monkeypatch.setattr(mcp, "build_toolsets", lambda config=None: {"mcp:x": FakeToolset()})

    built = uh_toolsets.build_toolsets()

    assert "mcp:x" in built
    assert {"file", "terminal", "memory", "web_search"} <= set(built)


def test_a_broken_mcp_config_never_costs_the_built_in_tools(monkeypatch) -> None:
    """An external server is never load-bearing for file/memory/terminal."""
    from uh import toolsets as uh_toolsets

    def explode(config=None):
        raise RuntimeError("config.toml is on fire")

    monkeypatch.setattr(mcp, "build_toolsets", explode)

    built = uh_toolsets.build_toolsets()

    assert {"file", "terminal", "memory", "web_search"} <= set(built)


def test_test_server_connects_lists_and_disconnects(make_server) -> None:
    server = make_server()
    cfg = table([{"name": server.name, "command": server.command, "args": list(server.args)}])

    payload = mcp.test_server("fake", cfg)

    assert payload["state"] == "connected"
    assert "echo" in payload["tools"]
    assert not any(client.alive for client in mcp._CLIENTS.values())


def test_test_server_for_an_unknown_name_is_a_configuration_error() -> None:
    with pytest.raises(mcp.MCPError) as caught:
        mcp.test_server("nobody", uh_config.Config(values={}))

    assert caught.value.kind == "configuration"


def test_a_server_name_becomes_a_safe_tool_name() -> None:
    assert mcp.exposed_name("my server", "read/file") == "mcp__my_server__read_file"
    long = mcp.exposed_name("s" * 40, "t" * 40)
    assert len(long) <= mcp.MAX_TOOL_NAME
