"""A path refused for being outside the server's roots is a fact, not "Erreur".

The filesystem MCP server answers in one sentence. It holds three things a user
(or the model) needs: the path asked for, the roots that exist, and therefore the
line to change. This file keeps that shape.
"""

from __future__ import annotations

from uh import mcp

REFUSAL = (
    "Access denied - path outside allowed directories: "
    "C:\\Users\\searc\\Desktop\\df_ventes.csv not in C:\\Users\\searc\\Desktop\\NOT IA"
)


def test_the_refusal_becomes_three_actionable_fields() -> None:
    payload = mcp.outside_root(REFUSAL, "atelier")

    assert payload is not None
    assert payload["kind"] == "outside_root"
    assert payload["requested"].endswith("df_ventes.csv")
    assert payload["allowed"] == ["C:\\Users\\searc\\Desktop\\NOT IA"]
    assert "config.toml" in payload["error"], "the fix must be named"
    assert "atelier" in payload["error"]


def test_several_roots_are_listed_as_several_paths() -> None:
    text = (
        "Error: Access denied - path outside allowed directories: "
        "/tmp/x.csv not in /home/u/projects, /home/u/data"
    )

    payload = mcp.outside_root(text, "atelier")

    assert payload is not None
    assert payload["allowed"] == ["/home/u/projects", "/home/u/data"]


def test_another_error_is_left_alone() -> None:
    assert mcp.outside_root("file not found: notes.csv", "atelier") is None
    assert mcp.outside_root("", "atelier") is None


def test_the_client_returns_the_shape_not_a_generic_error() -> None:
    """Every caller - chat turn, panel, test - sees the same actionable payload."""
    client = mcp.Client(mcp.ServerConfig(name="atelier", command="fake"))
    client._request = lambda *args, **kwargs: {  # type: ignore[assignment]
        "isError": True,
        "content": [{"type": "text", "text": REFUSAL}],
    }

    result = client.call("search_files", {"path": "C:\\Users\\searc\\Desktop", "pattern": "*.csv"})

    assert result["kind"] == "outside_root"
    assert result["requested"]
    assert result["allowed"]
    assert "config.toml" in result["error"]
