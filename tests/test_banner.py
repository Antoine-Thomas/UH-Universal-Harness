"""Banner: ASCII fallback, width behaviour, TTY gating (no dependency)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from uh import banner, cli


class StubProvider:
    model = "stub:1b"

    def chat(self, messages, tools, *, on_token=None):
        if on_token is not None:
            on_token("reponse")
        return {"role": "assistant", "content": "reponse", "tool_calls": []}


def _ascii_only(text: str) -> bool:
    return all(ord(character) < 128 for character in text)


def test_ascii_render_is_pure_ascii() -> None:
    rendered = banner.render(force_ascii=True)
    assert _ascii_only(rendered), [c for c in rendered if ord(c) >= 128]
    assert "Universal Harness" in rendered


def test_unicode_render_contains_box_drawing() -> None:
    rendered = banner.render(force_ascii=False)
    assert "Universal Harness" in rendered
    assert "█" in rendered


def test_render_mentions_product_name_and_version() -> None:
    assert "Universal Harness" in banner.render()
    assert banner.__version__ in banner.render()


def test_narrow_terminal_returns_short_banner() -> None:
    assert banner.render(width=30) == banner.BANNER_SHORT
    assert banner.render(width=120) != banner.BANNER_SHORT


def test_short_banner_is_ascii_safe() -> None:
    assert _ascii_only(banner.BANNER_SHORT)


def test_uh_ascii_env_forces_ascii(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_ASCII", "1")
    assert _ascii_only(banner.render())


def test_should_show_is_false_off_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UH_BANNER", raising=False)
    monkeypatch.setattr(banner, "_stdout_is_tty", lambda: False)
    assert banner.should_show() is False


def test_should_show_is_true_on_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UH_BANNER", raising=False)
    monkeypatch.setattr(banner, "_stdout_is_tty", lambda: True)
    assert banner.should_show() is True


def test_uh_banner_zero_disables_on_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_BANNER", "0")
    monkeypatch.setattr(banner, "_stdout_is_tty", lambda: True)
    assert banner.should_show() is False


def test_no_args_on_tty_prints_banner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(banner, "_stdout_is_tty", lambda: True)
    monkeypatch.delenv("UH_BANNER", raising=False)

    code = cli.main([])

    out = capsys.readouterr()
    assert code == 0
    assert "Universal Harness" in out.out
    assert "usage: uh" in out.out  # the help still follows the banner


def test_no_args_piped_prints_no_banner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(banner, "_stdout_is_tty", lambda: False)

    code = cli.main([])

    out = capsys.readouterr()
    assert code == 0
    # the help text mentions the product name; the banner art does not appear
    assert "█" not in out.out
    assert "| |_| | |_| |" not in out.out
    assert "provider-agnostic | local-first" not in out.out
    assert "usage: uh" in out.out


def test_chat_once_never_prints_banner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(banner, "_stdout_is_tty", lambda: True)  # hostile case
    monkeypatch.setattr(cli, "_make_provider", lambda kind, model: StubProvider())
    monkeypatch.setattr("sys.stdin", io.StringIO("bonjour\n"))
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))

    code = cli.main(["chat", "--once", "--model", "stub:1b", "--workspace", str(tmp_path)])

    captured = capsys.readouterr()
    assert code == 0
    assert "█" not in captured.out
    assert "provider-agnostic | local-first" not in captured.err


def test_version_and_doctor_print_no_banner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(banner, "_stdout_is_tty", lambda: True)

    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "█" not in capsys.readouterr().out

    cli.main(["doctor"])
    assert "█" not in capsys.readouterr().out


class _FakeStdout:
    """Only the two attributes the banner reads."""

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding
        self.written: list[str] = []

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        pass


def test_a_stream_that_cannot_carry_the_logo_falls_back_to_ascii(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UH_ASCII", raising=False)
    monkeypatch.setattr(banner.sys, "stdout", _FakeStdout("cp1252"))
    assert _ascii_only(banner.render(width=80))


def test_a_utf8_stream_gets_the_box_drawing_logo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UH_ASCII", raising=False)
    monkeypatch.setattr(banner.sys, "stdout", _FakeStdout("utf-8"))
    assert "█" in banner.render(width=80)


def test_an_unknown_encoding_falls_back_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UH_ASCII", raising=False)
    monkeypatch.setattr(banner.sys, "stdout", _FakeStdout("no-such-codec"))
    assert _ascii_only(banner.render(width=80))
