# Releasing UH on PyPI

Publishing is automated by `.github/workflows/publish.yml` with **Trusted
Publishing (OIDC)**: no API token, no password, no secret anywhere. PyPI trusts
the repository, the workflow file and the environment name; GitHub mints a
short-lived identity token at run time.

## The one thing to remember

**Pushing a tag triggers nothing.** The workflow starts on
`release: published` only. Push `vX.Y.Z`, then open *Releases → Draft a new
release → pick the tag → Publish release*. That click is what publishes.

## What the workflow does

Three jobs, strictly ordered:

1. **`test`** — installs `.[dev,web]` on Python 3.11 and runs `pytest -q`.
   Nothing is published if a test fails.
2. **`build-and-publish-testpypi`** (`needs: test`, environment `testpypi`) —
   checks the version, builds, `twine check`, uploads to TestPyPI.
3. **`build-and-publish-pypi`** (`needs: [test, build-and-publish-testpypi]`,
   environment `pypi`) — same checks, uploads to PyPI. It only runs if the
   TestPyPI job succeeded.

Guards, in both publishing jobs:

- **version check before the build**: the release tag (minus a leading `v`)
  must equal `pyproject.toml`'s `version` *and* `uh.__version__`; any mismatch
  fails the job with an explicit `::error::` message. This catches the classic
  "published 0.6.0 twice" mistake;
- `python -m twine check dist/*` after the build, so a broken `long_description`
  never reaches the index;
- `skip-existing: true` on the TestPyPI upload, so re-running a release that
  already landed there does not block;
- a job can only carry one environment, which is why TestPyPI and PyPI are two
  jobs rather than two steps.

Because of `needs`, a red TestPyPI job **stops** the PyPI job: nothing reaches
the real index if the rehearsal failed.

## One-time setup (already done for this project)

On <https://pypi.org> and <https://test.pypi.org>: an account with 2FA, plus a
pending publisher for project `uh-harness`, owner `Antoine-Thomas`, repository
`UH-Universal-Harness`, workflow `publish.yml`, environments `pypi` and
`testpypi`.

On GitHub: *Settings → Environments* → `testpypi` (no rule) and `pypi` with
**Required reviewers** listing the maintainer.

> **Measured on 2026-11-12, 12:5x UTC**: `pypi` carries a `required_reviewers`
> rule with the maintainer's account (`prevent_self_review: false`), so the PyPI
> job stops at "Waiting for review" until it is approved. `testpypi` has no rule
> on purpose: a rehearsal should not need a second approval.
>
> `prevent_self_review` must stay **false**: the maintainer is the only reviewer,
> and with it enabled nobody could ever approve the release. This is a manual
> GitHub setting — nothing in the repository can enforce it — so re-check it after
> any change to the environment:
>
> ```bash
> gh api repos/:owner/:repo/environments --jq '.environments[] | {name, rules: .protection_rules}'
> ```

## Release checklist

```bash
# 0. the platform gate: this suite is written on Windows and must be green on
#    Linux too, or CI will stop the release (it did, on 1.3.0: two tests patched
#    os.name, which breaks pathlib on a runner, and three assumed the maintainer's
#    ~/.omniroute/.env existed). One venv, once:
#      wsl -e bash -lc 'python3 -m venv ~/uhlin && ~/uhlin/bin/pip install -e ".[dev,web]"'
#      wsl -e bash -lc 'cd /mnt/c/.../UH-Universal-Harness && ~/uhlin/bin/python -m pytest -q'

# 1. version in BOTH files (they must agree: uh --version reads uh/__init__.py)
#    pyproject.toml  -> version = "X.Y.Z"
#    uh/__init__.py  -> __version__ = "X.Y.Z"

# 2. local gate
python -m pip install -e ".[dev]"
pytest -q
python -m build
python -m twine check dist/*

# 3. commit, tag, push (annotated tag; never move a published one)
git add pyproject.toml uh/__init__.py
git commit -m "chore: release X.Y.Z"
git tag -a vX.Y.Z -m "X.Y.Z"
git push origin main --tags
```

4. On GitHub: *Releases → Draft a new release → tag `vX.Y.Z` → Publish release*.
   This is the trigger. The `test` job runs first, then TestPyPI.
5. Approve the `pypi` environment once TestPyPI is green — **only if you set
   a required reviewer**; without it, the upload starts on its own.
6. Verify both indexes (below).

## Verifying a published version

```bash
python -m venv /tmp/uh-check

# TestPyPI (dependencies come from PyPI: TestPyPI does not mirror them)
/tmp/uh-check/bin/pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ uh-harness==X.Y.Z

# PyPI
/tmp/uh-check/bin/pip install uh-harness==X.Y.Z

/tmp/uh-check/bin/uh --version     # must print X.Y.Z
/tmp/uh-check/bin/uh doctor        # must exit 0
```

The PyPI project page renders the README **as it was at build time**: a README
change only appears on PyPI with the next release, which is why documentation
fixes often ship with a patch version.

## Before you publish: the two gates that are not in this repository

- **Environment protection** on `pypi` (above): manual, measured absent.
- **Branch protection** on `main`: manual too. Without it, nothing forces the CI
  to be green before a push reaches `main`; the release process above assumes it
  is, but the repository cannot enforce it.

Both are listed with their exact menu paths in `docs/JALON7.md`.

### Technical debt: the bypass, accepted for now

A direct `git push origin main` from the owner succeeds **before** the required
checks exist, and GitHub says so: `Bypassed rule violations for refs/heads/main:
2 of 2 required status checks are expected`. The runs then verify the commit the
push already delivered, which is the wrong order: a red CI can no longer stop a
bad `main`, it can only tell you afterwards.

Accepted as debt while the repository has one maintainer who reads the runs
before publishing anything: one branch per release (push the branch, open a PR,
merge after green) would remove the window entirely, at the cost of two more
clicks per release. Turn on **Do not allow bypassing the above settings** in the
branch protection rule at the same time, or the owner still skips it.

Revisit when a second person can push, or when releases become frequent enough
that "I read the runs" stops being true.

## Cross-platform CI gates

Before any release, run `mypy --platform linux uh` — a Windows-only mypy run
hides ctypes attributes (`ctypes.windll`, `ctypes.WinDLL`) that do not exist on
Linux or macOS, and the lint job will fail after the tag is already pushed.

Tests must not depend on the hardware tier. When testing budget rules, mock
`hardware.budget_for_tier`: a machine in the `xl` tier passes a 75 %-of-window
assertion that a 7 GB runner fails, which makes the suite green locally and red
in CI.

**The one exception to "tags are never moved"**: a tag that has never been
published (no GitHub release, no upload on either index) may be deleted and
recreated on the fixed commit. Once a release exists it is immutable — delete
the release is not a thing this repository does, so the rule below still holds
from that moment on.

## Version rules

- PyPI and TestPyPI refuse to replace an existing version: an upload is final,
  on that index. `skip-existing` only silences the TestPyPI rehearsal.
- The two indexes are independent, so the same number can carry different
  artefacts; publishing both in one run keeps them identical.
- To rehearse twice, bump. Never reuse a number.
- Version gaps are allowed (`0.6.0` → `0.7.2` is fine).
- Tags are never moved and history is never force-pushed.

## Undoing a bad release

- **Yank** (preferred): <https://pypi.org/manage/project/uh-harness/releases/> →
  the version → *Yank*. Yanked versions stay installable when pinned exactly but
  are never chosen for new installs. The number stays consumed.
- **Delete** is only possible in a short window for the newest upload and PyPI
  usually refuses it. Never rely on it.
- Fix forward: bump the patch, tag, release.

## What never goes in the repository

No `~/.pypirc`, no API token, no `keys.toml`, no provider key. The publishing
jobs grant `id-token: write` and nothing else. If a secret is ever found in a
committed file, rotate it before publishing anything.
