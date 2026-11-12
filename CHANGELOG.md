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

## [1.0.2] — 2026-11-12

Keeps the console usable on a real machine: a strict provider payload, a model
without tool calling, a shell command typed at the prompt, and a test suite that
was writing into the developer's live store.

### Added
- **`uhb_status()` toolset** (UHB Niveau 1): `GET {UH_UHB_URL}/status`,
  loopback only (default `http://127.0.0.1:8101`), summarised as
  `{connected, version, mode, harness, providers, modes}` with names only. The
  toolset is built only when a 1 s probe answers, a remote URL is refused before
  any request, and a UHB that dies mid-session is a `{"error": "uhb
  unreachable"}` tool result, never an exception.
- **Usage metrics after each turn**: providers report the model they really
  served (`model`) and their counters (`usage`), normalised across OpenAI
  (`prompt_tokens`/`completion_tokens`/`total_tokens`) and Ollama
  (`prompt_eval_count`/`eval_count`). The console prints a dim line
  `omnirouter  qwen3:8b  1234 tok (in 312 / out 922)  total session 3456 tok`,
  or `tokens non rapportés`; `/status` gains `served:` and `tokens:`. Streamed
  answers request `stream_options.include_usage` and retry once without it if
  the gateway refuses the parameter.

### Fixed
- **DeepSeek `400 Invalid 'messages[N].tool_calls': empty array`**: an assistant
  message with `tool_calls: []` is replayed on the next turn, and DeepSeek
  rejects it. The empty key is now stripped from every provider payload (Ollama,
  the OpenAI-compatible providers, and the free router's transport) while the
  session history keeps its own shape.
- **Ollama models that cannot call tools** (tinyllama) now raise
  `model '<name>' does not support tool calling; try a different model or
  disable tools.` instead of a raw registry body; the `fallback:` line carries
  the same clause on its classified reason.
- **A shell command typed at `you>`** (`uh doctor`, `pip install ...`, `git
  status`, ...) is refused with a warning instead of being sent to the model:
  no provider call, no tokens, nothing appended to the history.
- **The test suite could write into the real `~/.uh`**: `tests/test_encoding.py`
  spawned `python -m uh doctor` without `UH_HOME`, so doctor opened the live
  store and its clean close checkpointed the WAL - rewriting `uh.db` and
  deleting `uh.db-wal`/`uh.db-shm` of a session that was open. Every spawned CLI
  now gets an isolated home, and a test pins that.

## [1.0.1] — 2026-11-12

Patch release: three console defects reported against 1.0.0, plus the upgrade
command the reported console was missing.

### Fixed
- **Ctrl+C while a provider streams no longer prints an httpx/httpcore
  traceback** and no longer kills the console. `KeyboardInterrupt` is a
  `BaseException`: the provider only catches `httpx.HTTPError` and the CLI's
  last-resort guard only catches `Exception`, so nothing stopped it. The turn
  is now dropped (`[interrompu]` on stderr, half-written line closed), the
  history is rolled back to before the turn, nothing partial is persisted, and
  the prompt comes back; `uh chat --once` returns 130 instead of a traceback.
- **The console shows the logo again.** `_run_repl` asked for the short banner,
  which prints the two-line ASCII art by construction, so no encoding ever
  mattered there. It now renders the full art (which still degrades to the short
  banner under 40 columns).
- **Unicode detection is a capability test**, not a substring search for "utf"
  in the encoding name: `LOGO_UNICODE.encode(encoding)`. A stream that can carry
  █ gets the logo (UTF-8 on modern PowerShell / Windows Terminal), a cp1252 or
  cp850 console and an unknown codec fall back to pure ASCII instead of raising.
- **Readable session header and spacing**: one blank line before every `you>`
  prompt and one between the question and the answer; the header is three dim
  lines (provider · model · stream / workspace · session / messages · context)
  so a long Windows path no longer garbles a single status bar.
- Status and error lines use rich when it is installed and the stream is a TTY
  (grey details, bold red errors), with markup escaped so a message such as
  `[interrompu]` prints literally; plain text otherwise, no ANSI off a TTY and
  `NO_COLOR` honoured.

### Added
- **`uh upgrade`**: reports the running and installed version, runs
  `python -m pip install --upgrade uh-harness` in the same interpreter, then
  reports `old -> new`. Refuses outside a virtualenv unless `--user` is passed,
  keeps the installed version when pip fails, forwards `--dry-run` to pip, and
  only re-execs `uh` with `--restart` (a re-exec of the old argv would loop).

### Notes
- `[interrompu]` is the one deliberately French string, documented in
  `uh/console.py`; every other user-facing message is English.
- **`1.0.0` has no section of its own**: it shipped as a version bump on top of
  0.9.0 (the README's "What 1.0.0 contains" lists the surface). Per-release
  blocks resume here.

## [0.9.0] — 2026-11-12 *(the release that carries 7a, 7b and 6b)*

### Added
- **Provider panel in `uh web` (Jalon 7a)**: list catalogue and custom
  providers, add and delete keys (write-only: only a count is ever returned),
  create a custom OpenAI-compatible provider, test a connection with a single
  `ping`, and switch `allow_remote` with an explicit consent box.
- **One service layer, three front-ends (Jalon 7b)**: `uh/providers_admin.py`
  holds the rules; the console, the CLI and the web panel call it, so a refusal
  is worded identically everywhere.
- Console and CLI commands: `/key list|add|remove`, `/provider add|test`,
  `/router [reset]`, `/local-only on|off`, `/save`, `/export`,
  `uh keys list|add|remove`, `uh providers test`, `uh docs`.
- **`uh router serve` (Jalon 6b)**: an OpenAI-compatible endpoint on loopback
  (`GET /v1/models`, `POST /v1/chat/completions`, stream and non-stream) using
  the stored keys and the existing fallback chain, with its **own token**
  (`uh router token show|rotate`, `$UH_HOME/router-token`).
- `uh web --static-dir`, to serve a replacement interface with the same token,
  `Host`/`Origin` checks and CSP.
- `docs/API.md` with a drift test, `docs/ROUTER_SERVE.md`, `docs/AUDIT_BRIEF.md`,
  `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, issue and PR templates,
  Dependabot configuration.
- A **pre-push gate** (`scripts/push_gate.py` via pre-commit) and pytest isolation
  that fails the run if a test writes into the real `~/.uh`.

### Changed
- `uh providers add` no longer accepts `--key`: a key on the command line is
  visible in the process list and kept in the shell history. It comes from a
  masked prompt, stdin, or `UH_PROVIDER_KEY`.
- The first-run suggestion prefers `lfm3` at the 4 GB tier: measured to call
  `read_file`, `terminal` and `memory_write` correctly, ~1.5 s per turn.

### Fixed
- `uh router serve`: the body cap counts bytes actually read (a chunked request
  has no `Content-Length`); a concurrency slot is held until the stream really
  ends, including on client disconnect; the access log names the provider of that
  request instead of a shared one.
- `SystemExit`/`EOFError` at a consent or provider prompt no longer crashes the
  console: the command cancels.

> **`v0.8.0` is tagged but was never published to PyPI.** It predates 7a, 7b and
> 6b — publishing it would have shipped the router alone — and a PyPI version can
> never be replaced. `0.9.0` is the first release that carries the panel, the key
> commands and `uh router serve`. Same for `v0.7.0` and `v0.7.1`, whose publish
> runs failed and which never reached the index.

### Tooling
- One line-ending convention: `.editorconfig` (LF), `core.autocrlf=false`
  documented in `CONTRIBUTING.md`.

## [0.8.0] — 2026-11-12 *(tag only, never published)*

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
