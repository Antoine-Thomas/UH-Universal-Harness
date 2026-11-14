"""A tool error that means "no session is open" becomes a sentence, not a dead end.

The model reads whatever the MCP server returned. When that text says no console
or no kernel is running, the useful answer is "open the CONSOLE, run one cell",
not a guessed workaround.
"""

from __future__ import annotations

from uh import mcp


def test_a_positron_session_error_names_the_console() -> None:
    hint = mcp.session_hint("Error: No session running for this code execution", "positron")
    assert hint.startswith("Positron")
    assert "CONSOLE" in hint
    assert "Rien n'a été exécuté" in hint


def test_another_ide_keeps_its_own_name() -> None:
    hint = mcp.session_hint("no active session", "vscode")
    assert "Le serveur 'vscode'" in hint


def test_a_french_error_is_understood_too() -> None:
    assert mcp.session_hint("aucune session active", "positron")


def test_a_normal_error_gets_no_hint() -> None:
    assert mcp.session_hint("file not found: notes.csv", "positron") == ""
    assert mcp.session_hint("", "positron") == ""


def test_the_client_prepends_the_hint_to_what_the_model_reads() -> None:
    """The hint is added where the tool result is built, so every caller sees it."""
    client = mcp.Client(mcp.ServerConfig(name="positron", command="fake"))
    client._request = lambda *args, **kwargs: {  # type: ignore[assignment]
        "isError": True,
        "content": [{"type": "text", "text": "Error: No session running"}],
    }

    result = client.call("execute_code", {"code": "1+1"})

    assert result["kind"] == "tool"
    assert result["text"].startswith("Positron n'a aucune session active")
    assert "No session running" in result["text"], "the server's own words are kept"
    assert result["error"] == result["text"]


def test_a_successful_call_is_untouched() -> None:
    client = mcp.Client(mcp.ServerConfig(name="positron", command="fake"))
    client._request = lambda *args, **kwargs: {  # type: ignore[assignment]
        "content": [{"type": "text", "text": "42"}],
    }

    result = client.call("execute_code", {"code": "40+2"})

    assert "error" not in result
    assert result["text"] == "42"
