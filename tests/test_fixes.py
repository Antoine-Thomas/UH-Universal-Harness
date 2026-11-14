"""`uh fix`: a patch has to prove it applies before anyone believes it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from uh import fixes


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    (root / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


GOOD = """diff --git a/app.py b/app.py
index 1111111..2222222 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""

BAD_CONTEXT = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -9,2 +9,2 @@
 def nope(a, b):
-    return a - b
+    return a + b
"""


def test_a_patch_that_does_not_apply_is_refused(repo: Path) -> None:
    proposal = fixes.propose("fix the sum", repo=repo, patch=BAD_CONTEXT)

    assert proposal.applies is False
    assert proposal.check_error
    assert (repo / "app.py").read_text(encoding="utf-8").endswith("return a - b\n"), (
        "a refused patch must not touch the tree"
    )


def test_prose_is_not_a_patch(repo: Path) -> None:
    proposal = fixes.propose("fix", repo=repo, patch="I would change line 2 to a + b.")

    assert proposal.applies is False
    assert "unified diff" in proposal.check_error


def test_a_good_patch_applies_on_a_branch_and_is_not_committed(repo: Path) -> None:
    proposal = fixes.propose("fix the sum", repo=repo, patch=GOOD)
    assert proposal.applies is True

    outcome = fixes.apply(proposal, repo=repo)

    assert outcome["applied"] is True
    assert outcome["branch"].startswith("fix/auto-")
    assert (repo / "app.py").read_text(encoding="utf-8").endswith("return a + b\n")
    branch = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"], capture_output=True, text=True
    ).stdout.strip()
    assert branch == outcome["branch"]
    head = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline", "-1"], capture_output=True, text=True
    ).stdout
    assert "init" in head, "nothing was committed: HEAD is still the original commit"


def test_a_failing_check_reverts_the_patch(repo: Path) -> None:
    """A patch that breaks the tests is not a repair."""
    proposal = fixes.propose("fix the sum", repo=repo, patch=GOOD)

    outcome = fixes.apply(
        proposal,
        repo=repo,
        checks=[["python", "-c", "import sys; sys.exit(1)"]],
    )

    assert outcome["applied"] is False
    assert outcome["reverted"] is True
    assert (repo / "app.py").read_text(encoding="utf-8").endswith("return a - b\n"), (
        "the working tree goes back to HEAD when a check fails"
    )


def test_the_diff_is_recorded_even_when_it_is_refused(repo: Path) -> None:
    proposal = fixes.propose("fix", repo=repo, patch=BAD_CONTEXT)

    assert proposal.path.is_file()
    assert "diff --git" in proposal.path.read_text(encoding="utf-8")


def test_a_directory_that_is_not_a_repository_is_refused(tmp_path: Path) -> None:
    with pytest.raises(fixes.FixError) as caught:
        fixes.repo_root(tmp_path)

    assert "not inside a git repository" in str(caught.value)


def test_apply_refuses_a_patch_that_was_never_validated(repo: Path) -> None:
    proposal = fixes.propose("fix", repo=repo, patch=BAD_CONTEXT)

    with pytest.raises(fixes.FixError):
        fixes.apply(proposal, repo=repo)
