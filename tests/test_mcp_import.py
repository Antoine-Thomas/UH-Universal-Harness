"""Reading an MCP configuration file: JSON, and the YAML subset the popup accepts."""

from __future__ import annotations

import json

import pytest

from uh import mcp_import


def test_a_claude_desktop_json_file() -> None:
    text = json.dumps(
        {
            "mcpServers": {
                "atelier": {
                    "command": "npx",
                    "args": ["-y", "secure-filesystem-server", "C:/notes"],
                    "env": {"TOKEN": "secret"},
                }
            }
        }
    )
    (server,) = mcp_import.parse(text, "claude.json")
    assert server == {
        "name": "atelier",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "secure-filesystem-server", "C:/notes"],
        "env": {"TOKEN": "secret"},
    }


def test_one_http_server_without_the_wrapper() -> None:
    (server,) = mcp_import.parse(
        json.dumps({"name": "positron", "url": "http://127.0.0.1:8787/sse"})
    )
    assert server["transport"] == "http"
    assert server["url"] == "http://127.0.0.1:8787/sse"


def test_a_list_of_servers_is_accepted() -> None:
    found = mcp_import.parse(
        json.dumps(
            [
                {"name": "one", "command": "npx", "args": ["-y", "a"]},
                {"name": "two", "url": "https://example.test/mcp"},
            ]
        )
    )
    assert [server["name"] for server in found] == ["one", "two"]
    assert [server["transport"] for server in found] == ["stdio", "http"]


def test_the_documented_yaml_subset() -> None:
    text = """
# a comment
mcpServers:
  atelier:
    command: npx
    args:
      - -y
      - secure-filesystem-server
    env:
      TOKEN: "abc def"
  positron:
    transport: sse
    url: http://127.0.0.1:8787/sse
"""
    found = {server["name"]: server for server in mcp_import.parse(text, "servers.yml")}
    assert found["atelier"]["args"] == ["-y", "secure-filesystem-server"]
    assert found["atelier"]["env"] == {"TOKEN": "abc def"}
    assert found["positron"]["transport"] == "sse"


def test_args_as_a_string_are_split_on_lines() -> None:
    (server,) = mcp_import.parse(
        json.dumps({"name": "x", "command": "uvx", "args": "-y\nserver\nC:/docs"})
    )
    assert server["args"] == ["-y", "server", "C:/docs"]


def test_a_bearer_header_becomes_the_token_field() -> None:
    (server,) = mcp_import.parse(
        json.dumps(
            {
                "name": "remote",
                "url": "https://example.test/mcp",
                "headers": {"Authorization": "Bearer abc123"},
            }
        )
    )
    assert server["token"] == "abc123"


def test_an_awkward_name_is_slugged_not_refused() -> None:
    (server,) = mcp_import.parse(json.dumps({"name": "My Server (prod)", "command": "npx"}))
    assert server["name"] == "my-server-prod"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "just a sentence",
        '{"mcpServers": {}}',
        json.dumps({"mcpServers": {"a": {"args": ["-y"]}}}),
        json.dumps([{"name": "x"}, {"name": "x", "command": "npx"}]),
    ],
)
def test_what_is_not_a_configuration_is_refused(text: str) -> None:
    with pytest.raises(mcp_import.MCPImportError):
        mcp_import.parse(text)


def test_a_stdio_server_needs_a_command() -> None:
    with pytest.raises(mcp_import.MCPImportError) as raised:
        mcp_import.parse(json.dumps({"mcpServers": {"a": {"args": ["-y"]}}}))
    assert "command" in str(raised.value)


def test_a_remote_server_needs_a_url() -> None:
    with pytest.raises(mcp_import.MCPImportError) as raised:
        mcp_import.parse(json.dumps({"mcpServers": {"a": {"transport": "http"}}}))
    assert "url" in str(raised.value)


def test_tabs_are_refused_with_their_line_number() -> None:
    with pytest.raises(mcp_import.MCPImportError) as raised:
        mcp_import.parse("mcpServers:\n\tatelier:\n\t\tcommand: npx\n")
    assert "tabulation" in str(raised.value)


def test_an_over_long_file_is_refused() -> None:
    with pytest.raises(mcp_import.MCPImportError) as raised:
        mcp_import.parse("x" * (mcp_import.MAX_BYTES + 10))
    assert "trop gros" in str(raised.value)
