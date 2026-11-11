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
- [ ] **Jalon 1 — Agent core.** Synchronous `run_turn` loop, toolsets
      (`file`, `terminal`, `memory`), in-memory message history, `uh chat`.
      No persistence yet: a session lives only as long as the process.
- [ ] **Jalon 2 — Local model layer.** Ollama client, local model discovery and
      pull (`uh model list`, `uh model pull`), free-router bridge with explicit
      fail-closed fallbacks.
- [ ] **Jalon 3 — Sessions and configuration.** Persisted sessions backed by a
      SQLite FTS5 store (`uh sessions`), layered config file and `uh config`,
      extended `uh doctor`.
- [ ] **Jalon 4 — Web UI.** Sessions and streaming chat in the browser, copied
      and adapted from Harness Maxxing.

Only Jalon 0 is done. Everything else is a placeholder that exits with code `2`
and names the milestone that will deliver it.

## CLI surface (Jalon 0)

```
uh --version            # print the version
uh doctor               # environment report, exit 0 (ollama absent = warning)
uh model list           # placeholder -> Jalon 2, exit 2
uh model pull <name>    # placeholder -> Jalon 2, exit 2
uh chat                 # placeholder -> Jalon 1, exit 2
uh sessions             # placeholder -> Jalon 3, exit 2
uh config               # placeholder -> Jalon 3, exit 2
```

`uh doctor` exits `0` when only optional components are missing (Ollama, a local
router config) and exits `1` when a blocking requirement is missing (Python
< 3.11, `httpx`, `git`).

## License

MIT — see [LICENSE](LICENSE).
