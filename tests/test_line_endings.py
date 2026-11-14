"""No tracked text file carries CRLF: the push hook must have nothing to rewrite.

The pre-push hook normalises line endings, and when it has something to fix it
exits non-zero: the push is refused *after* the tests ran. A tree that is already
LF is the only way that hook stays quiet, so this test holds the invariant where
it can be seen - in the suite the gate runs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GITATTRIBUTES = ROOT / ".gitattributes"

#: Binary payloads: `.gitattributes` marks some, the rest are known by suffix.
BINARY_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
    ".wav",
    ".mp3",
    ".mp4",
    ".pdf",
    ".woff",
    ".woff2",
    ".whl",
    ".gz",
    ".zip",
)


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout (sdist or export)")
    return [line for line in result.stdout.splitlines() if line.strip()]


def test_no_tracked_text_file_has_crlf() -> None:
    offenders = []
    for name in tracked_files():
        if name.lower().endswith(BINARY_SUFFIXES):
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        if b"\r\n" in path.read_bytes():
            offenders.append(name)
    assert not offenders, (
        "these files have CRLF in the working tree; the pre-push hook will rewrite "
        f"them and refuse the push: {offenders}"
    )


def test_the_rule_is_declared_for_lf_and_windows_scripts() -> None:
    text = GITATTRIBUTES.read_text(encoding="utf-8")
    assert "* text=auto eol=lf" in text, "LF is the repository default"
    for suffix in ("*.bat", "*.cmd", "*.ps1"):
        assert f"{suffix} text eol=crlf" in text, (
            f"{suffix} must stay CRLF: a Windows script with LF breaks on labels"
        )
