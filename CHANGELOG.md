# Changelog

All notable changes to UH (Universal Harness). Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning follows
[Semantic Versioning](https://semver.org/).

Conventions worth knowing:

- every release tag on `main` is annotated, **except** `v0.7.0`, `v0.7.1` and
  `v0.7.2`, which were created as lightweight tags from the GitHub release UI;
- pushing a tag publishes nothing: PyPI publication needs a **GitHub release**
  of that tag (see [docs/RELEASING.md](docs/RELEASING.md));
- versions marked *PyPI* below were actually published; the others exist only as
  tags or releases.

## [0.8.0] — 2026-11-12

### Added
- **Free-provider router (Jalon 6a)**: `uh/router/` with a data-only catalogue of
  six OpenAI-compatible free providers, each carrying its verification status
  and the date it was checked.
- Key rotation (several keys per provider), cooldowns driven by `Retry-After`,
  a circuit breaker (3 consecutive failures → 20 s, doubling up to 5 min), and
  `priority` / `round-robin` strategies.
- Streamed failover with a trace of the provider and model that actually
  answered; no silent retry once tokens were printed.
- Tool-capability filter read from the provider's real `/models` answer
  (`supported_parameters`), never assumed.
- `uh providers free`, `uh router status`, and a global `--local-only` flag.
- Remote access is opt-in: `allow_remote` (default false), recorded consent, and
  a refusal when stdin is not a terminal.

### Changed
- `keys.toml` accepts several keys per provider (`keys = [...]`); the previous
  single `key` field is still read.
- Entries marked `partial` in the catalogue are labelled `UNVERIFIED` and stay out
  of the automatic chain until explicitly enabled with
  `router_extra_providers`.

### Fixed
- `--local-only` no longer prints a spurious "unknown config key 'allow_remote'".
- `uh router status` no longer reports a provider as ready when all its keys are
  cooling down.

## [0.7.3] — 2026-11-12

### Added
- `ruff`, `mypy` and `pre-commit` configuration; a `lint` workflow runs them on
  every pull request and on `main`.
- README: quickstart, "Why UH?", and a screenshot taken from the real web UI.
- CONTRIBUTING.md.

### Fixed
- Publishing workflow now gates on tests, checks the release tag against
  `pyproject.toml` and `uh.__version__`, and runs `twine check`.
- A truncated line in the README (`authorization` header sentence).

## [0.7.2] — 2026-11-12 *(PyPI)*

### Added
- Python 3.10 support through `uh/_compat.py` and the `tomli` backport.
- The PyPI publishing job, alongside TestPyPI.

### Changed
- `doctor` accepts Python 3.10 (it previously required 3.11).

## [0.7.0], [0.7.1] — 2026-11-12 *(tags only, never published)*

These two tags were created from the GitHub release UI and are lightweight.
Both publishing runs failed (`Fail-fast in unsupported environments`, the
environment was passed as an action input instead of at job level), so neither
version reached PyPI or TestPyPI.

- `v0.7.0` — publish workflow: environment moved to job level, PyPI job prepared
  but commented.
- `v0.7.1` — Python 3.10 support, PyPI job enabled.

## [0.6.0] — 2026-11-12 *(PyPI)*

### Added
- **Local web UI (Jalon 5)**: `uh web` with streamed SSE chat, session list,
  full-text search, command palette, context gauge, per-session model, light/dark
  themes, served from three static files with no build step.
- Token authentication (cookie `HttpOnly; SameSite=Strict`), `Host`/`Origin`
  checks, strict CSP, terminal toolset off unless `--allow-terminal`.
- Sessions CRUD, `/api/search`, `/api/models`, `/api/status`.
- CI: a `web` job across the three operating systems.

### Changed
- `uh` without arguments opens the console on a TTY; piped output still prints
  help.

## [0.5.1] — 2026-11-11

### Fixed
- Windows CI: tests now read child-process output as UTF-8 instead of the
  runner's cp1252 locale, which killed the reader thread.

## [0.5.0] — 2026-11-11

### Added
- **Console experience (Jalon 4)**: banner, status bar, and a slash-command
  registry (`uh/commands.py`) with aliases, closest-match suggestions and help
  groups.
- Zero-configuration first run: an installed Ollama model, else a model sized to
  the detected RAM (never downloaded without a confirmation), else a local
  router, else a guided free-key setup.
- `uh providers add|remove`, keys stored in `$UH_HOME/keys.toml` (0600 where the
  OS allows).
- UTF-8 stdio on Windows (`_configure_stdio`), CI matrix 3 OS × Python 3.11–3.13.

## [0.4.0] — 2026-11-11

### Added
- **Sessions and configuration (Jalon 3)**: SQLite store with FTS5, WAL and
  cascading deletes; `uh sessions list|show|search|delete|resume`.
- Layered configuration (environment > project `uh.toml` > user `config.toml` >
  defaults), `uh config show|get|set|path`.
- One-shot migration of `~/.uh/MEMORY.md` into the store (idempotent, keeps a
  `.bak`).
- `uh doctor` extended with store, schema version and free disk.

### Changed
- Session ids are shown as `HHMM-xxxx` (the first characters alone were only the
  date and two sessions a day looked identical).

## [0.3.1] — 2026-11-11

### Added
- ASCII/Unicode banner on interactive sessions only (TTY), `UH_ASCII=1` to force
  ASCII, `UH_BANNER=0` to disable.

## [0.3.0] — 2026-11-11

### Added
- **Local model layer (Jalon 2)**: streamed answers, `uh model pull` with
  progress, OmniRouter as an OpenAI-compatible backup, and a traced fallback
  chain.
- `uh providers list` with reachability per provider.

## [0.2.0] — 2026-11-11

### Added
- **Agent core (Jalon 1)**: synchronous `run_turn` loop with a hard
  `max_iterations` budget, and three toolsets — `file` (workspace-confined),
  `terminal` (single command, timeout, documented blacklist), `memory`
  (markdown sections).
- `uh chat` with streamed output on stdout and tool activity on stderr.

## [0.1.1] — 2026-11-11 *(TestPyPI rehearsal)*

Lightweight tag used to rehearse the publishing pipeline; no feature change.

## [0.1.0] — 2026-11-11

### Added
- **Skeleton (Jalon 0)**: installable `uh-harness` package, `uh --version`,
  `uh doctor`, the documented command surface with explicit "not implemented
  yet" placeholders, MIT licence, README.
