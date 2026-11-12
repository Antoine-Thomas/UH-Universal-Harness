# Releasing UH on PyPI

Publishing is automated by `.github/workflows/publish.yml`. It uses **Trusted
Publishing (OIDC)**: no API token, no password, no secret anywhere in the
repository. PyPI trusts the repository, the workflow file and the environment
name, and GitHub mints a short-lived identity token at run time.

## How the workflow works today

- **Trigger**: a GitHub **release is published** (`on: release: types: [published]`).
  Nothing else starts it — no push, no manual dispatch, no schedule.
- **Two jobs, run in parallel**, each self-contained:
  - `build-and-publish-testpypi` → environment `testpypi` → uploads to
    `https://test.pypi.org/legacy/`;
  - `build-and-publish-pypi` → environment `pypi` → uploads to PyPI.
  They run the same steps: checkout, Python 3.11, `python -m build`, then
  `pypa/gh-action-pypi-publish@release/v1` with `permissions: id-token: write`
  as the only granted permission.
- A job can carry **one** environment only, which is why PyPI is a separate job
  and not a second step in the TestPyPI one.

What the workflow does **not** do (by design, for now):

- it does not run the test suite;
- it does not run `twine check`;
- it does not compare the version in `pyproject.toml` with the tag.

Consequences to keep in mind:

- a broken build is uploaded rather than blocked, so run `pytest -q` and
  `python -m build && python -m twine check dist/*` locally before tagging;
- the version that gets published is the one in `pyproject.toml`, **not** the
  tag name — always tag the commit that carries the right version;
- the two jobs do not depend on each other: if TestPyPI rejects a duplicate,
  the PyPI job still runs and still publishes. A red TestPyPI job is not a gate.

## One-time setup (already done for this project)

On <https://pypi.org> and <https://test.pypi.org>:

1. account with two-factor authentication enabled;
2. *Publishing → Add a pending publisher*: project `uh-harness`, owner
   `Antoine-Thomas`, repository `UH-Universal-Harness`, workflow `publish.yml`,
   environment `pypi` (respectively `testpypi`).

On GitHub: *Settings → Environments* → `testpypi` (no rule) and `pypi`
(**Required reviewers** set, so no PyPI upload happens without an explicit
approval; during a run the job shows "Waiting for review").

## Release checklist

```bash
# 1. version in BOTH files (they must agree: uh --version reads uh/__init__.py)
#    pyproject.toml  -> version = "X.Y.Z"
#    uh/__init__.py  -> __version__ = "X.Y.Z"

# 2. local gate
python -m pip install -e ".[dev]"
pytest -q
python -m build
python -m twine check dist/*

# 3. commit, tag, push (annotated tag, never move a published one)
git add pyproject.toml uh/__init__.py
git commit -m "chore: release X.Y.Z"
git tag -a vX.Y.Z -m "X.Y.Z"
git push origin main --tags
```

4. On GitHub: *Releases → Draft a new release → choose tag `vX.Y.Z` → Publish
   release*. That single click fires `release: published`.
5. Wait: the `testpypi` job publishes immediately; the `pypi` job waits for your
   approval on the `pypi` environment. Approve it once the TestPyPI job is green.
6. Verify both indexes (see below).

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

`pip download uh-harness==X.Y.Z --no-deps -d /tmp/probe` is a quick way to check
what is actually served. The JSON API answers the same question:
`https://pypi.org/pypi/uh-harness/json` lists every released version.

## Version rules

- PyPI **and** TestPyPI refuse to replace an existing version: an upload is
  final for that number, on that index.
- The two indexes are independent: a version present on TestPyPI can still be
  published to PyPI, and the same number can carry slightly different artefacts.
  Prefer publishing the same version to both in the same run.
- To rehearse twice, bump: `X.Y.Z` then `X.Y.(Z+1)`. Never reuse a number.
- Number gaps are allowed (`0.6.0` → `0.7.2` is fine); no need to publish the
  intermediate versions.
- Tags are never moved and history is never force-pushed.

## Undoing a bad release

- **Yank** (preferred): <https://pypi.org/manage/project/uh-harness/releases/> →
  pick the version → *Yank*. Yanked versions stay installable when pinned
  exactly but are never chosen for new installs. The number stays consumed.
- **Delete** is only possible in a short window for the newest upload and PyPI
  usually refuses it; never rely on it.
- Fix forward: bump the patch, tag, publish.

## Hardening ideas (not implemented yet)

- add a `test` job and `needs: test` on both publish jobs;
- compare the tag with `pyproject.toml` before building;
- run `twine check` in the build step;
- pin `actions/checkout` and `actions/setup-python` to their current major
  (v7) to silence the Node 24 deprecation warnings.

Any of these makes the run slower but turns "it published" into "it published
and it was checked".

## What never goes in the repository

No `~/.pypirc`, no API token, no `keys.toml`, no provider key. The workflow
grants `id-token: write` and nothing else. If a secret is ever found in a
committed file, rotate it before publishing anything.
