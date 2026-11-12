# `uh web` — HTTP API contract

Read from the running code, not from memory: `tests/test_web_api_docs.py` fails
when a route exists in the app and is missing here, and when an element id used
by `uh/web/static/index.html` is missing from the id list below. Update this file
in the same commit as any route change.

## Transport and trust

| Rule | Value |
| --- | --- |
| Bind | `127.0.0.1:8765` by default; `--host` allows anything, with a warning |
| Auth | token, generated per process; `/?token=…` sets a `HttpOnly; SameSite=Strict` cookie, `Authorization: Bearer …` is also accepted |
| Missing or wrong token | `401 {"detail": "…"}` |
| `Host` header | must be the bind host or loopback, else `400 {"detail": "host not allowed"}` |
| `Origin` header | when present, must match `http://<host>:<port>`, else `400` |
| Headers on every response | `Content-Security-Policy: default-src 'none'; …` (no `unsafe-inline`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` |
| OpenAPI | disabled (`openapi_url=None`, `docs_url=None`) |
| Bodies | never logged; keys never appear in any response |

Error shape is always `{"detail": "<message>"}`. Messages never contain a key,
even when the failing input was one.

## Routes

| Method | Path | Body / query | Response |
| --- | --- | --- | --- |
| GET | `/` | `?token=` | HTML page (sets the cookie when the token matches) |
| GET | `/static/*` | — | the three interface files (or `--static-dir`) |
| GET | `/api/status` | — | `{version, provider, model, provider_error, sessions, store, schema_version, terminal, workspace}` |
| GET | `/api/commands` | — | `{commands: [{name, aliases, help, args, group}]}` |
| GET | `/api/sessions` | `?limit=` | `{sessions: [{…, short_id}]}` |
| POST | `/api/sessions` | `{title?}` | `{session: {…, short_id}}` |
| DELETE | `/api/sessions/{session_id}` | — | `{deleted: bool, id}` · `404` |
| PATCH | `/api/sessions/{session_id}` | `{title}` | `{id, title}` · `404` |
| GET | `/api/sessions/{session_id}/messages` | `?limit=` | `{session, messages, provider, model}` · `404` |
| POST | `/api/sessions/{session_id}/chat` | `{message}` | **SSE stream** (below) · `400` empty message · `404` · `503` provider unreachable |
| POST | `/api/sessions/{session_id}/stop` | — | `{stopped: bool, reason?}` · `404` |
| GET | `/api/search` | `?q=&limit=` | `{query, hits: [{…, short_id}]}` |
| GET | `/api/models` | — | `{provider, model, ollama: [names], providers: [kinds]}` |
| POST | `/api/model` | `{model, provider?, session_id?}` | `{model, provider}` · `400` |
| GET | `/api/providers` | — | `{providers: [{name, base_url, model, keys, verified, custom, in_chain}], key_entry_allowed, keys: {cloudflare: {configured, source, account_id, key_hints}, deepseek: {configured, key_hints}, omniroute: {detected, base_url, has_key, config_path}}}` |
| POST | `/api/providers/{name}/keys` | `{key}` | `{stored: true, keys: n}` · `400` · `403` off loopback · `404` unknown |
| DELETE | `/api/providers/{name}/keys` | `{index}` | `{deleted: true, keys: n}` · `400` · `403` · `404` |
| POST | `/api/providers/cloudflare/config` | `{account_id, token}` | `{stored: true, account_id, key_hint, source}` · `400` (account id must be 32 hex) · `403` · no full key in the answer |
| POST | `/api/providers/deepseek/key` | `{key}` | `{stored: true, keys: n, key_hints: [...]}` · `400` · `403` |
| POST | `/api/providers/omniroute/import` | — | `{imported: true, name, base_url, key_hints}` · `403` · `404` no OmniRoute config |
| POST | `/api/providers/custom` | `{name, base_url, model?, key?}` | `{created: true, name, base_url, model}` · `400` · `403` |
| POST | `/api/providers/{name}/test` | — | `{ok, status, error_kind}` · `404` |
| GET | `/api/router/settings` | — | `{allow_remote, consent, consent_date, strategy, extra_providers, warning}` |
| PUT | `/api/router/settings` | `{allow_remote, consent?, strategy?}` | the same object · `400` consent or strategy · `403` off loopback |

`403` on every write route comes from one rule: `uh web` must be bound to
loopback. There is no override flag.

**No response ever carries a full key.** The `keys` object of
`GET /api/providers` is the API-key panel's state: `configured`, the `source`
of the credential (`environment`, `keys.toml` or `none`), and at most a
**masked hint** per stored key (3 leading + 4 trailing characters, e.g.
`sk-…abcd`). A key that comes from the environment is reported as
`source: environment` with no hint at all: UH never wrote it and does not
echo it. Delete-by-index exists for the same reason: a key value must not
travel in a URL, which ends up in an access log.

`error_kind` is one of `auth`, `rate_limit`, `payment`, `server`, `timeout`,
`transport`, `redirect_refused`, `no_base_url` — the same classification the
router uses.

## SSE stream of `POST /api/sessions/{id}/chat`

Frames follow the usual `event: <name>\ndata: <json>\n\n` shape.

| Event | Data | Meaning |
| --- | --- | --- |
| `open` | `{session, model}` | the turn started |
| `token` | `{text}` | one streamed fragment |
| `tool` | `{name, …}` | one tool call, before and after |
| `message` | `{…}` | a whole assistant message when streaming is off |
| `error` | `{message}` | the turn failed; the message is already classified |
| `done` | `{session}` | terminal event, always sent, even after an error or a stop |

`POST …/stop` makes the stream end with `done`, not with an error.

## Element ids used by the current interface

`uh/web/static/index.html` + `app.js` rely on these ids; a replacement interface
served by `--static-dir` is free to use its own, but the ones below are the
contract of the bundled one:

```
composer  gauge-fill  gauge-label  input  keys-priority  keys-view  layout
menu-toggle  messages  model-select  new-session  palette  palette-input
palette-list  providers-close  providers-hint  providers-list  providers-open
providers-panel  providers-view  router-settings  search  search-results  send
session-list  session-title  sidebar  status-provider  stop  tab-keys
tab-providers  terminal-warning  theme-toggle
```

## Serving a replacement interface

```
uh web --static-dir ./my-ui
```

The folder is mounted at `/static` and its `index.html` is served at `/`, with
the **same** token, `Host`/`Origin` checks and CSP as the bundled interface (no
`unsafe-inline`, so the page must load its script from `/static/…`). A missing
`index.html` is a startup error, not a silent fallback.

## Router API (`uh router serve`)

A **second, separate** application: another port (8780), another token
(`$UH_HOME/router-token`), no browser interface. Checked against the code by
`tests/test_web_api_docs.py` like the routes above.

| Method | Path | Body / query | Response |
| --- | --- | --- | --- |
| GET | `/v1/models` | — | `{object: "list", data: [{id, object, owned_by, tools}]}` |
| POST | `/v1/chat/completions` | `{model?, messages, tools?, stream?}` | `chat.completion` object, or an SSE stream |

Rules that apply to both routes: `Authorization: Bearer <router token>` only
(no cookie), `Host` must be loopback plus the bound port, no CORS header, body
≤ 1 MiB (`413`), 4 concurrent turns (`429`), and the client's `Authorization`
never reaches a provider. Errors keep the OpenAI shape:
`{"error": {"message", "type", "code", "param"}}` with `400`, `401`, `413`, `429`,
`502` or `503`.

Streaming frames:

```
data: {"id":"chatcmpl-…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"…"},"finish_reason":null}]}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

A failure after tokens were emitted sends `{"error": {"code": "stream_error"}}`
then `[DONE]`: the stream is never replayed to another provider.
