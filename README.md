[![PyPI version](https://img.shields.io/pypi/v/uh-harness.svg)](https://pypi.org/project/uh-harness/)
[![Publish to PyPI](https://github.com/Antoine-Thomas/UH-Universal-Harness/actions/workflows/publish.yml/badge.svg)](https://github.com/Antoine-Thomas/UH-Universal-Harness/actions/workflows/publish.yml)

# UH — Universal Harness

A provider-agnostic agent harness in Python: a console, a local web UI, tool use,
persisted sessions, and **one** runtime dependency (`httpx`). Local-first, no
account, no API key on first launch.

## Quickstart

```bash
pip install uh-harness          # Python 3.10+
uh                              # console: picks a local model, starts chatting
pip install "uh-harness[web]" && uh web   # optional: the browser UI
```

That is the whole setup. UH finds an Ollama model you already have (or offers to
pull a small one sized to your RAM), falls back to a local OpenAI-compatible
router, and only asks for a key if nothing local answers — in which case
`uh doctor` and the guided setup tell you exactly what to do. Nothing is sent
anywhere until you type a prompt.

## Why UH?

Four ideas that each solved one problem, combined in one small package:

- **A real agent loop, not a chat wrapper** (from Hermes Agent). `run_turn` runs
  synchronously, executes the tools the model asks for, feeds the results back,
  and stops at a hard `max_iterations` budget instead of looping forever.
- **A contract, not a framework** (from OpenClaw). Toolsets declare their
  schema, refuse unknown arguments and return errors the model can read; the
  dangerous toolset is off unless you enable it.
- **Deterministic routing with a traced fallback** (from UHB). Ollama first,
  then a local router, then whatever you configured — and the fallback is
  *printed*, never silent. A dead provider is an error message, not a mystery.
- **Sessions and a browser UI that stay local** (from Harness Maxxing's feature
  set, reimplemented from scratch). Sessions live in SQLite with full-text
  search; the web UI is served from `127.0.0.1` behind a per-process token.

**The CLI is universal.** UH makes no assumption about a vendor: it drives any
local or free provider (Ollama on `127.0.0.1:11434`, a local router such as
OmniRouter/Nemotron) and never requires an API key on first launch. A paid
provider is an opt-in, never a prerequisite.

- Requires Python ≥ 3.10 (`tomli` is installed automatically on 3.10)
- Single runtime dependency: `httpx` (`[ui]` adds `rich`, `[web]` adds
  `fastapi` + `uvicorn`)
- Installable on its own: no dependency on `hermes-home-vision` or `uhb`
- MIT licence; the web UI is an original implementation (see
  [docs/WEB_DESIGN.md](https://github.com/Antoine-Thomas/UH-Universal-Harness/blob/main/docs/WEB_DESIGN.md))

## Install (development)

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uh --version
uh doctor
```

## Roadmap — five milestones

- [x] **Jalon 0 — Skeleton.** PyPI-installable package (`uh-harness`), `uh` CLI
      surface, `uh --version`, `uh doctor` (python / httpx / git / ollama probe),
      README, MIT license. No agent loop, no storage, no UI.
- [x] **Jalon 1 — Agent core.** Synchronous `run_turn` loop, toolsets
      (`file`, `terminal`, `memory`), in-memory message history, `uh chat`.
      No persistence yet: a session lives only as long as the process.
- [x] **Jalon 2 — Local model layer.** Streamed chat (`--no-stream` to disable),
      real `uh model pull` with progress, provider registry (`ollama`,
      `omnirouter`), traced fallback chain, `uh providers list`.
- [x] **Jalon 3 — Sessions and configuration.** Persisted sessions backed by a
      SQLite FTS5 store (`uh sessions list|show|search|delete|resume`), layered
      configuration with `uh config show|get|set|path`, `uh memory migrate|show`,
      extended `uh doctor`.
- [x] **Jalon 4 — Console experience.** `uh` with no argument opens a chat
      console: banner, status bar, slash commands (`docs/SLASH_COMMANDS.md`),
      zero-configuration onboarding (local model, offer to pull one sized to the
      RAM, local router, or a guided free key), `uh providers add|remove` with
      keys in `$UH_HOME/keys.toml` (0600), UTF-8 stdio on Windows, CI matrix.
- [x] **Jalon 5 — Web UI.** `uh web` serves a local interface (sessions column,
      streaming chat, collapsible tool cards, context gauge, per-session model,
      full-text search, command palette, stop button, light/dark themes) from
      three static files with no build step and no third-party JavaScript.

Jalons 0 to 5 are done. The interface is an **original design**: it borrows the
feature list of Harness Maxxing (public README) and none of its code, which is
under a Fair Source Licence. UH stays MIT.

## CLI surface (Jalon 4)

```
uh                      # interactive console (piped: help, unchanged)
uh --version            # print the version
uh doctor               # environment report (python, httpx, git, ollama, store, disk, config, keys)
uh model list           # list models served by Ollama, exit 1 if Ollama is down
uh model pull <name>    # download a model with progress on stderr
uh chat [prompt]        # agent loop with file/terminal/memory tools (streamed, saved)
uh sessions list        # recent sessions (id, date, provider, messages, title)
uh sessions show <id>   # one session and its messages
uh sessions search <q>  # full-text search across every message
uh sessions resume <id> # continue a session (--fork copies it into a new one)
uh config show|get|set  # layered configuration
uh memory migrate|show  # one-shot import of the flat memory file
uh providers list       # registered providers and their state
uh providers add <name> # store an OpenAI-compatible provider (key never printed)
uh providers remove <name>
```

`uh chat` reads stdin or takes a prompt argument; `--once` (or any non-TTY
stdin) answers one line and exits. Answers stream by default, `--no-stream`
waits for the whole message. Conversations are persisted unless `--no-save`;
`--session <id>` resumes (full id, unique prefix, or the short form shown by
`uh sessions list`). Tool activity goes to stderr, the answer to stdout.

## Web interface

![UH web interface - sessions on the left, streaming chat with a collapsible tool card, context gauge and model selector on the right](https://raw.githubusercontent.com/Antoine-Thomas/UH-Universal-Harness/main/docs/assets/webui.png)

*A real session, not a mockup: the assistant called `write_file`, the card is the
tool call, the gauge is the context estimate for that conversation.*

Served locally, never exposed by default:

```
pip install "uh-harness[web]"
uh web                      # http://127.0.0.1:8765/?token=...
uh web --port 9000 --no-open
uh web --allow-terminal     # enable the shell toolset in the browser (off by default)
```

`uh web` without the extra prints the exact install command and exits 1. The
core package stays on `httpx`; `fastapi`/`uvicorn` only exist in `[web]`.

Security, because a local server is still a server:

- binds `127.0.0.1`; any other `--host` prints a warning before starting;
- a 43-character token is generated at startup, accepted once as `?token=` and
  then kept in an `HttpOnly; SameSite=Strict` cookie; every `/api` route answers
  401 without it (curl: send the token as a bearer `Authorization` header);
- `Host` and `Origin` are checked (DNS-rebinding / CSRF), no CORS is enabled;
- strict CSP without `unsafe-inline`, so no inline script or style exists in the
  HTML; `X-Frame-Options: DENY`;
- the **terminal toolset is disabled** unless `--allow-terminal`; file tools stay
  inside `--workspace`;
- no provider key is ever returned by the API.

Design notes and the feature-by-feature rationale live in
[docs/WEB_DESIGN.md](https://github.com/Antoine-Thomas/UH-Universal-Harness/blob/main/docs/WEB_DESIGN.md).

## Console and slash commands

`uh` on a terminal starts the console; piped, it still prints help (scriptable
unchanged). The status bar shows `provider · model · session · messages ·
context estimate`. `/help` lists everything; the full inventory and the reason
each Hermes command was kept or dropped lives in
[docs/SLASH_COMMANDS.md](https://github.com/Antoine-Thomas/UH-Universal-Harness/blob/main/docs/SLASH_COMMANDS.md).

The release process (Trusted Publishing, TestPyPI then PyPI, yanking) is in [docs/RELEASING.md](https://github.com/Antoine-Thomas/UH-Universal-Harness/blob/main/docs/RELEASING.md).

`/help`, `/model`, `/provider`, `/tools`, `/clear`, `/new`, `/sessions`,
`/resume`, `/history`, `/retry`, `/title`, `/status`, `/config`, `/doctor`,
`/memory`, `/version`, `/exit` (aliases `/h`, `/?`, `/reset`, `/quit`, `/q`,
`/v`). An unknown command is never sent to the model: it is refused with the
closest match suggested.

First run with nothing configured, in order: an installed Ollama model, else an
offer to pull a small model sized to the detected RAM (size shown, never
downloaded without a yes), else a local OmniRouter gateway, else a provider you
already added, else a guided setup that explains where to get a free key and
stores it in `$UH_HOME/keys.toml` (0600). UH ships **no key**: a published key
is a stolen key.

The console can use [rich](https://rich.readthedocs.io/) when installed
(`pip install "uh-harness[ui]"`); without it, or off a TTY, everything falls
back to plain text with the same output contract.

```bash
echo "list the python files here" | uh chat --once --model qwen3:8b --workspace .
uh chat --session 1829-9474 "et ensuite ?"           # resume
uh sessions search clavier                            # FTS5 across all messages
uh memory migrate                                     # MEMORY.md -> one session, once
```

## Sessions, store and configuration

- Store: `$UH_HOME/uh.db` (`~/.uh/uh.db`), schema v1, WAL, foreign keys ON.
  Deleting a session cascades to its messages and to the FTS index.
- Two tables (`sessions`, `messages`) plus an external-content FTS5 index kept
  in sync by triggers; `search` returns a snippet and a `bm25` rank.
- A database written by a newer build is refused (`StoreError`), never
  downgraded silently. No FTS5 in the SQLite build means no store at all.
- Configuration, lowest to highest: defaults, `~/.uh/config.toml`, `./uh.toml`,
  `UH_*` environment, CLI flags. `uh config show` prints the source of every
  value. Unknown keys warn and are ignored; the writer handles flat keys only
  (booleans, integers, strings) and refuses anything else.
- `uh memory migrate` copies `MEMORY.md` into a `kind=memory` session after
  writing a timestamped `.bak`; it is idempotent and leaves the flat file in
  place (the memory toolset still reads and writes it).

## Providers and fallback

| Provider | Transport | Endpoint | Streaming |
| --- | --- | --- | --- |
| `ollama` | httpx, NDJSON | `POST /api/chat`, `GET /api/tags`, `POST /api/pull` | yes |
| `omnirouter` | httpx, SSE | `POST /v1/chat/completions` (OpenAI-compatible) | yes |

Without `--provider`, `uh chat` builds the chain `ollama` then `omnirouter`
(when configured). Every switch is printed on stderr, never silent:

```
fallback: ollama -> omnirouter (reason: cannot reach Ollama at ...)
```

`--provider <kind>` disables the fallback: one provider, its errors surface as
they are. Model namespaces are not shared, so the backup uses
`UH_OMNIROUTER_MODEL` (default `auto/best-free`) rather than the Ollama tag.

## OmniRouter discovery (measured, not guessed)

`uh doctor` and `uh providers list` find a local OmniRoute gateway from these
sources, first hit wins:

1. `UH_OMNIROUTER_URL` (explicit)
2. `OMNIROUTE_BASE_URL` (the OmniRoute CLI's own variable)
3. `BASE_URL=` in `~/.omniroute/.env`
4. `http://{HOST}:{PORT}` from that dotenv (`PORT` defaults to 20128)
5. otherwise: "not configured" - nothing is invented

API key: `UH_OMNIROUTER_API_KEY`, then `OMNIROUTE_API_KEY`, then the same
dotenv. The key is only ever sent as an `Authorization` header, never printed.

What the installed gateway actually looks like (OmniRoute 3.8.52):

- data dir `~/.omniroute`, configuration is a dotenv file plus a SQLite
  database - **no YAML/TOML**, so UH needs no parser dependency;
- server on `http://127.0.0.1:20128`, `GET /v1/models` and
  `POST /v1/chat/completions` answer with a `Bearer` key (1632 model ids on the
  machine this was measured on, including `auto/*` combos);
- streaming is SSE (`data: {...}` chunks, `data: [DONE]` terminator).

## License

MIT — see [LICENSE](https://github.com/Antoine-Thomas/UH-Universal-Harness/blob/main/LICENSE).
