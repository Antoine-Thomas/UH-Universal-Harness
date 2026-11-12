"""Jalon 7b.1: one service layer, three front-ends.

The console, the CLI and the web panel must accept and refuse exactly the same
things. These tests exercise the library first, then the two command surfaces,
and finish with the "known key" check that runs a whole battery of commands and
looks for the secret **by bytes** in every output.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from uh import cli, commands, console
from uh import config as uh_config
from uh import keys as keys_mod
from uh import providers_admin as admin

SECRET = "known-key-7b1-must-never-appear-0001"
HOME_ENV = "UH_HOME"


class StubProvider:
    model = "stub:1b"
    streaming = False

    def chat(self, messages, tools, *, on_token=None):
        return {"role": "assistant", "content": "ok", "tool_calls": []}


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(HOME_ENV, str(tmp_path / "home"))
    monkeypatch.delenv("UH_PROVIDER_KEY", raising=False)
    return tmp_path


@pytest.fixture()
def session(home: Path) -> console.ChatSession:
    return console.ChatSession(
        provider_factory=lambda kind, model: StubProvider(),
        provider_kind="stub",
        chain=[("stub", StubProvider())],
        model="stub:1b",
        toolsets={},
        workspace=home,
        cfg=uh_config.load(),
        stream=False,
        store=None,
        max_iterations=2,
    )


# --------------------------------------------------------------------------- #
# the service layer
# --------------------------------------------------------------------------- #
def test_service_rejects_a_catalogue_name_for_a_custom_provider(home: Path) -> None:
    with pytest.raises(admin.AdminError) as failure:
        admin.create_custom("openrouter", "https://openrouter.ai/api/v1")
    assert "catalogue provider" in str(failure.value)


def test_service_counts_keys_and_never_returns_them(home: Path) -> None:
    admin.save_provider("groq", "https://api.groq.com/openai/v1", "m", SECRET)
    assert admin.add_key_to("groq", "second-known-key-7b1") == 2
    rows = {row["name"]: row for row in admin.provider_rows()}
    assert rows["groq"]["keys"] == 2
    assert SECRET not in repr(rows)
    assert admin.remove_key_index("groq", 1) == 1
    assert SECRET not in repr(admin.provider_rows())


def test_service_update_settings_requires_consent(home: Path) -> None:
    with pytest.raises(admin.AdminError) as failure:
        admin.update_settings(allow_remote=True, consent=False)
    assert "consent" in str(failure.value)
    assert admin.settings()["allow_remote"] is False

    updated = admin.update_settings(allow_remote=True, consent=True)
    assert updated["allow_remote"] is True and updated["consent"] is True
    assert updated["consent_date"]


def test_service_probe_is_a_ping(home: Path) -> None:
    admin.save_provider("groq", "https://api.groq.com/openai/v1", "m", SECRET)
    import json

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    result = admin.test_connection("groq", transport=httpx.MockTransport(handler))
    assert result["ok"] is True
    assert seen[0]["messages"] == [{"role": "user", "content": "ping"}]
    assert seen[0]["max_tokens"] == 1


# --------------------------------------------------------------------------- #
# the CLI
# --------------------------------------------------------------------------- #
def test_cli_keys_flow(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    admin.save_provider("groq", "https://api.groq.com/openai/v1", "m", "")
    assert cli.main(["keys", "list"]) == 0
    assert "groq" in capsys.readouterr().out

    assert cli.main(["keys", "add", "groq"]) == 1  # nothing to read, no prompt
    assert "no key given" in capsys.readouterr().err

    import os

    os.environ["UH_PROVIDER_KEY"] = SECRET
    try:
        assert cli.main(["keys", "add", "groq"]) == 0
    finally:
        del os.environ["UH_PROVIDER_KEY"]
    captured = capsys.readouterr()
    assert SECRET not in captured.out and SECRET not in captured.err
    assert keys_mod.get_provider("groq")["keys"] == [SECRET]

    assert cli.main(["keys", "remove", "groq", "1"]) == 0
    assert keys_mod.get_provider("groq")["keys"] == []
    assert cli.main(["keys", "remove", "groq", "1"]) == 1


def test_cli_providers_test_reports_without_a_key(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        admin, "test_connection", lambda name, **_kwargs: {"ok": False, "status": 401, "error_kind": "auth"}
    )
    assert cli.main(["providers", "test", "groq"]) == 1
    captured = capsys.readouterr()
    assert "failed: auth" in captured.err
    assert "HTTP 401" in captured.err


def test_cli_unknown_key_action_shows_help(home: Path) -> None:
    # like any bare sub-group: usage on stdout, non-zero exit
    assert cli.main(["keys"]) == 2


# --------------------------------------------------------------------------- #
# the console
# --------------------------------------------------------------------------- #
def test_console_key_list_shows_counts_only(session: console.ChatSession) -> None:
    admin.save_provider("groq", "https://api.groq.com/openai/v1", "m", SECRET)
    result = commands.dispatch(session, "/key list")
    assert not result.err
    assert "groq" in result.out
    assert "1 key(s)" in result.out
    assert SECRET not in result.out


def test_console_key_add_uses_the_masked_reader(
    session: console.ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_reader(prompt: str = "", **_kwargs) -> str:
        calls.append(prompt)
        return SECRET

    monkeypatch.setattr(admin, "read_secret", fake_reader)
    result = commands.dispatch(session, "/key add openrouter")
    assert calls and "not echoed" in calls[0]
    assert not result.err
    assert SECRET not in (result.out or "")
    assert keys_mod.get_provider("openrouter")["keys"] == [SECRET]


def test_console_key_add_refuses_an_empty_secret(
    session: console.ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(admin, "read_secret", lambda *a, **k: "")
    result = commands.dispatch(session, "/key add groq")
    assert not result.out and "nothing entered" in (result.err or "")


def test_console_router_shows_the_chain(session: console.ChatSession) -> None:
    admin.save_provider("openrouter", "https://openrouter.ai/api/v1", "m", SECRET)
    result = commands.dispatch(session, "/router")
    assert "allow_remote: false" in result.out
    assert "openrouter" in result.out
    assert SECRET not in result.out


def test_console_router_reset_without_a_router(session: console.ChatSession) -> None:
    result = commands.dispatch(session, "/router reset")
    assert "nothing to reset" in result.out


def test_console_local_only_on_switches_off_remote(session: console.ChatSession) -> None:
    admin.update_settings(allow_remote=True, consent=True)
    assert admin.settings()["allow_remote"] is True
    result = commands.dispatch(session, "/local-only on")
    assert "local only" in result.out
    assert admin.settings()["allow_remote"] is False


def test_console_local_only_off_refuses_without_a_terminal(
    session: console.ChatSession,
) -> None:
    # pytest's stdin is not a TTY: the command must refuse rather than assume consent
    result = commands.dispatch(session, "/local-only off")
    assert not result.out
    assert "interactive confirmation" in (result.err or "")
    assert admin.settings()["allow_remote"] is False


def test_console_local_only_off_uses_recorded_consent(session: console.ChatSession) -> None:
    admin.update_settings(allow_remote=False, consent=True)
    result = commands.dispatch(session, "/local-only off")
    assert "remote enabled" in result.out
    assert admin.settings()["allow_remote"] is True


def test_console_save_and_export(session: console.ChatSession, home: Path) -> None:
    session.history.append({"role": "user", "content": "bonjour"})
    session.history.append(
        {"role": "assistant", "content": "salut", "tool_calls": [{"function": {"name": "read_file"}}]}
    )

    saved = commands.dispatch(session, "/save notes.md")
    assert not saved.err
    target = home / "notes.md"
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    assert "bonjour" in text and "tool call: `read_file`" in text
    assert "sensitive data" in saved.out

    exported = commands.dispatch(session, "/export json")
    assert not exported.err
    data = list(home.glob("session-*.json"))
    assert data, "the JSON export was not written"
    payload = data[0].read_text(encoding="utf-8")
    assert '"role": "assistant"' in payload


def test_console_save_warns_but_writes_no_key(
    session: console.ChatSession, home: Path
) -> None:
    admin.save_provider("groq", "https://api.groq.com/openai/v1", "m", SECRET)
    session.history.append({"role": "user", "content": "question"})
    result = commands.dispatch(session, "/export md")
    assert not result.err
    files = list(home.glob("session-*.md"))
    assert files
    assert SECRET.encode() not in files[0].read_bytes()


# --------------------------------------------------------------------------- #
# the "known key" battery
# --------------------------------------------------------------------------- #
def test_known_key_never_appears_in_any_command_output(
    session: console.ChatSession, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    admin.save_provider("groq", "https://api.groq.com/openai/v1", "m", SECRET)
    admin.add_key_to("groq", SECRET + "-second")

    outputs: list[str] = []
    for line in (
        "/key list",
        "/key add",
        "/key remove groq 99",
        "/router",
        "/router reset",
        "/local-only",
        "/local-only off",
        "/save",
        "/export md",
        "/export json",
        "/provider",
    ):
        result = commands.dispatch(session, line)
        outputs.append(f"{line}: {result.out} | {result.err}")

    for path in home.glob("session-*"):
        outputs.append(path.read_bytes().decode("utf-8", "replace"))

    blob = "\n".join(outputs) + capsys.readouterr().out
    assert SECRET not in blob
    assert SECRET[:12] not in blob
    assert "known-key-7b1" not in blob

def test_console_local_only_off_handles_eof(
    session: console.ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Ctrl-D at the consent prompt must cancel, not raise EOFError."""

    class Tty:
        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            return ""

    def raise_eof(*_args, **_kwargs):
        raise EOFError

    monkeypatch.setattr("sys.stdin", Tty())
    monkeypatch.setattr("builtins.input", raise_eof)
    result = commands.dispatch(session, "/local-only off")
    assert "cancelled" in (result.out or "")
    assert admin.settings()["allow_remote"] is False
    assert admin.settings()["consent"] is False
