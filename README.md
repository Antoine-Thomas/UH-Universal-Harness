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
- [ ] **Jalon 3 — Sessions and configuration.** Persisted sessions backed by a
      SQLite FTS5 store (`uh sessions`), layered config file and `uh config`,
      extended `uh doctor`.
- [ ] **Jalon 4 — Web UI.** Sessions and streaming chat in the browser, copied
      and adapted from Harness Maxxing.

Only Jalons 0, 1 and 2 are done. Everything else is a placeholder that exits
with code `2` and names the milestone that will deliver it.

## CLI surface (Jalon 2)

```
uh --version            # print the version
uh doctor               # environment report, exit 0 (ollama absent = warning)
uh model list           # list models served by Ollama, exit 1 if Ollama is down
uh model pull <name>    # download a model with progress on stderr
uh chat                 # agent loop with file/terminal/memory tools (streamed)
uh providers list       # registered providers and their state
uh sessions             # placeholder -> Jalon 3, exit 2
uh config               # placeholder -> Jalon 3, exit 2
```

`uh chat` reads stdin; `--once` (or any non-TTY stdin) answers one line and
exits. Answers stream by default, `--no-stream` waits for the whole message.
In-session commands: `/exit`, `/model <name>`, `/tools`, `/clear`. Tool activity
goes to stderr, the answer to stdout, so the command stays scriptable:

```bash
echo "list the python files here" | uh chat --model qwen3:8b --once --workspace .
uh chat --provider omnirouter --model auto/fast      # force the gateway
uh model pull qwen2.5:0.5b --quiet                   # scripts: summary only
uh model pull big:70b --interactive                  # asks before downloading
```

`uh doctor` exits `0` when only optional components are missing (Ollama, a local
router config) and exits `1` when a blocking requirement is missing (Python
< 3.11, `httpx`, `git`, unwritable workspace).

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
