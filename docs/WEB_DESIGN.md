# UH web interface — design

**Conception originale, non dérivée du code de Harness Maxxing.** The feature
list below was inspired by the *public README* of that project; no file of its
`web/` or `server/` tree was read, copied or translated. Everything here is
written for UH and shipped under MIT. The name "Maxxing" and its logos are not
used anywhere.

Harness Maxxing is under a Fair Source Licence (not permissive). Copying even a
fragment would force that fragment — and any derived work — under its terms,
which would end UH's MIT status. Rewriting from a feature list keeps UH's
licence clean.

## Features targeted (from that public list, implemented from scratch)

| Feature | How UH does it |
| --- | --- |
| Sessions in columns | left column: list with id, title, provider, message count; click to open |
| Tool-call cards | each `tool_call`/`tool_result` SSE event becomes a collapsible card, error results highlighted |
| Context gauge | bar fed by `GET /api/sessions/{id}/messages`, computed as characters / 4 and labelled "estimate" |
| Model per session | `POST /api/model` with `session_id`; the mapping lives in memory and survives a reload of the page |
| Full-text search | `GET /api/search?q=` on the SQLite FTS5 index built in Jalon 3 |
| Command palette | Ctrl/Cmd-K opens the same registry as the console (`uh/commands.py`), fetched from `GET /api/commands`; running a command maps to its API equivalent |
| Stop button | `POST /api/sessions/{id}/stop` sets a flag checked at every streamed token |

Deliberately **not** copied: visual design, component structure, session
storage format, any React/TypeScript tooling. The front-end is three static
files (HTML + CSS + ES modules) with **no build step** and no third-party
JavaScript.

## Architecture

- `uh/web/app.py` — FastAPI application factory and routes.
- `uh/web/sse.py` — SSE frame writer (`event:` + `data:` + blank line).
- `uh/web/static/` — `index.html`, `app.js`, `style.css`, shipped in the wheel
  as package data (`[tool.setuptools.package-data]`).
- Reuses `Store`, `ChatSession` and the provider layer unchanged: **no SQLite
  schema change**.
- `fastapi`/`uvicorn` live in the optional `[web]` extra; the core stays on
  `httpx` alone.

The chat turn runs in a worker thread (the agent loop is synchronous) and pushes
events into an `asyncio.Queue` with `call_soon_threadsafe`; the SSE response is
an async generator draining that queue. Tokens therefore arrive live, while tool
cards are emitted once the turn's tools have returned (the agent executes tools
inside `run_turn`; a per-tool hook would have meant forking that loop).

## Security model

- Binds `127.0.0.1` by default. A non-loopback `--host` prints a warning: the
  UI is then reachable from the network and the token is the only protection.
- 32-byte token generated with `secrets.token_urlsafe(32)` at startup, printed
  in the URL. `GET /` accepts `?token=`, sets an `HttpOnly; SameSite=Strict`
  cookie and serves the page; every `/api` route requires that cookie or
  `Authorization: Bearer <token>` and answers 401 otherwise
  (`secrets.compare_digest`, no timing leak).
- `Host` header must be a loopback name with the configured port; any other
  value is refused with 400 (DNS-rebinding guard). `Origin`, when present, must
  be the same loopback origin (CSRF guard). No CORS middleware is installed.
- Strict CSP (`default-src 'none'`, `script-src 'self'`, no `unsafe-inline`), so
  `index.html` carries no inline script and no inline style; `X-Frame-Options:
  DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
- The **terminal toolset is off** unless `--allow-terminal`; the UI shows a
  banner when it is enabled. File tools stay inside the configured workspace.
- No key is ever returned by the API: `/api/status` reports provider and model
  only, never `keys.toml` contents.

## Limitations (stated, not hidden)

- Tool events are emitted after the tool ran, not before.
- `stop` takes effect at the next streamed token; a blocking (non-streaming)
  provider finishes its turn first.
- One process, in-memory per-session model map; no multi-user accounts.
