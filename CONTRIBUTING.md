# Contributing to UH

Thanks for having a look. UH is small on purpose: a provider-agnostic agent
loop, three toolsets, a console, an optional local web UI, and one runtime
dependency (`httpx`). Keeping it that way is the main review criterion.

## Set up

```bash
git clone https://github.com/Antoine-Thomas/UH-Universal-Harness.git
cd UH-Universal-Harness
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

Python 3.10 to 3.13 are supported. On 3.10 the `tomli` backport is installed
automatically (`uh/_compat.py` is the only place that knows about it).

## Checks before pushing

```bash
pytest -q          # 185 tests, no network, no model required
ruff check uh tests
mypy uh
```

All three must be green. CI runs them on Linux, macOS and Windows for every
Python version in the matrix, plus a `web` job with the `[web]` extra.

## Pre-commit hooks

```bash
pre-commit install          # once per clone
pre-commit run --all-files  # run everything now
```

Hooks: trailing whitespace, end-of-file, YAML/TOML syntax, merge conflicts,
large files, LF endings, `ruff --fix`, and `mypy` on `uh/`. If a hook rewrites
a file, re-add it and commit again.

## Line endings

`.editorconfig` sets `end_of_line = lf` for everything, and `.gitattributes`
normalises the repository to LF. On Windows, set `git config core.autocrlf false`
once: otherwise checkouts come back as CRLF and the `mixed-line-ending` hook
rewrites them on every commit.

## The push gate

`pre-commit install --hook-type pre-push` installs a hook that runs `ruff`,
`mypy` and `pytest -q` before any `git push`, and refuses the push if one fails
(`python scripts/push_gate.py` runs the same thing by hand). The same rule applies
to scripts and to a human: every check is evaluated **before** a commit is
created, never after.

## Style

- `ruff` config lives in `pyproject.toml` (`target-version = py310`,
  `line-length = 100`, rules `E4 E7 E9 F I B`). `E501` is deliberately not
  enforced: long prompt strings are legitimate.
- `mypy` runs with `ignore_missing_imports` but with `warn_return_any`,
  `strict_equality` and `check_untyped_defs` on. Prefer a real annotation or a
  narrowing `if x is None: ...` over `# type: ignore`; if an ignore is
  unavoidable, give it a code (`# type: ignore[attr-defined]`), `warn_unused_ignores`
  will flag it once it stops being needed.
- Public functions get type hints. Tests do not have to.
- No new runtime dependency without a discussion: `httpx` is the only one, and
  optional features live in extras (`[ui]`, `[web]`).

## Tests

- `tests/` mirrors the module layout (`test_web.py`, `test_store.py`, …).
- Tests never touch the network, a real model or the user's `~/.uh`: set
  `UH_HOME` to `tmp_path` and inject a fake provider / provider factory.
- Anything that reads a child process output uses
  `subprocess.run(..., encoding="utf-8", errors="replace")` — Windows runners
  decode with cp1252 otherwise and the reader thread dies.
- A new CLI surface needs a test that asserts both the output and the exit code.

## Commits and pull requests

- Small commits, imperative subject (`feat: …`, `fix: …`, `docs: …`,
  `test: …`, `ci: …`, `chore: …`).
- No secrets, ever: keys live in `$UH_HOME/keys.toml`, never in the repository,
  tests or examples.
- Do not force-push a branch that has an open pull request, do not move tags.
- New behaviour comes with a test; a bug fix comes with the test that would have
  caught it.

## Releasing

Maintainers only — see [docs/RELEASING.md](RELEASING.md). Short version: bump
the version in **both** `pyproject.toml` and `uh/__init__.py`, tag `vX.Y.Z`,
publish a GitHub release, approve the `pypi` environment.
