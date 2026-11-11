# UH — Universal Harness

A provider-agnostic agent harness in Python. UH merges the ideas that worked in
four prior projects:

| Source | What UH takes from it |
| --- | --- |
| Harness Maxxing | web UX, organised sessions |
| OpenClaw | the harness contract (toolsets, fail-closed modes) |
| Hermes Agent | the synchronous agent loop (`run_turn`) |
| UHB | deterministic routing, fail-closed modes |

**The CLI is universal.** UH makes no assumption about a specific vendor: it
must drive any local or free provider (Ollama on `127.0.0.1:11434`, a local
free router such as OmniRouter/Nemotron) and must never require an API key on
first launch. A paid provider is an opt-in, never a prerequisite.

The web UI will be **copied and adapted from Harness Maxxing**, not written from
scratch and not vendored as a dependency.

- Requires Python ≥ 3.11
- Single runtime dependency: `httpx`
- Installable on its own: no dependency on `hermes-home-vision` or `uhb`

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
- [ ] **Jalon 4 — Web UI.** Sessions and streaming chat in the browser, copied
      and adapted from Harness Maxxing.

Jalons 0 to 3 are done; only the web UI (Jalon 4) remains. No CLI command is a
placeholder any more.

## CLI surface (Jalon 3)

```
uh --version            # print the version
uh doctor               # environment report (python, httpx, git, ollama, store, disk, config)
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
```

`uh chat` reads stdin or takes a prompt argument; `--once` (or any non-TTY
stdin) answers one line and exits. Answers stream by default, `--no-stream`
waits for the whole message. Conversations are persisted unless `--no-save`;
`--session <id>` resumes (full id, unique prefix, or the short form shown by
`uh sessions list`). In-session commands: `/exit`, `/model <name>`, `/tools`,
`/clear`. Tool activity goes to stderr, the answer to stdout.

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

MIT — see [LICENSE](LICENSE).
