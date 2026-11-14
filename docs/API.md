# `uh web` — HTTP API contract

Read from the running code, not from memory: `tests/test_web_api_docs.py` fails
when a route exists in the app and is missing here, and when an element id used
by `uh/web/static/index.html` is missing from the id list below. Update this file
in the same commit as any route change.

## Transport and trust

| Rule | Value |
| --- | --- |
| Bind | `127.0.0.1:8765` by default; another `--host` is refused unless `--allow-remote` is given |
| Auth | token, generated per process; `/?token=…` sets a `HttpOnly; SameSite=Strict` cookie, `Authorization: Bearer <token>` is also accepted |
| Missing or wrong token | `401 {"detail": "…"}` |
| `Host` header | must be the bind host or loopback, else `400 {"detail": "host not allowed"}` |
| `Origin` header | when present, must match `http://<host>:<port>`, else `400` |
| `Sec-Fetch-Site` | `cross-site` is always `400`; a cookie-authenticated `POST`/`PUT`/`PATCH`/`DELETE` needs `same-origin` (or a matching `Origin`), else `403` |
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
| DELETE | `/api/sessions/{session_id}` | — | `{deleted, id, files_removed}` — messages cascade, uploads are removed from disk · `404` |
| PATCH | `/api/sessions/{session_id}` | `{title}` and/or `{folder}` | `{id, title, folder, session}` · `400` empty body · `404` |
| GET | `/api/folders` | — | `{folders: [{name, count}]}` — folders are metadata values, so a folder with no session does not exist |
| PATCH | `/api/folders/{name}` | `{name}` | `{name, previous, moved}` · `400` empty/unchanged/contains `/` · `404` unknown folder |
| DELETE | `/api/folders/{name}` | — | `{deleted, unfiled}` — the sessions are kept, unfiled · `404` |
| POST | `/api/sessions/{session_id}/upload?name=<file>` | raw bytes as the body (no multipart) | `{attachment: {name, path, size, kind, status, note}}` · `400` empty/refused · `413` over 20 MB |
| DELETE | `/api/sessions/{session_id}/messages/{message_id}` | — | `{deleted, session_id, remaining}` · `404` (unknown session or message) |
| GET | `/api/sessions/{session_id}/messages` | `?limit=` | `{session, messages, provider, model}` · `404` |
| POST | `/api/sessions/{session_id}/chat` | `{message, attachments?: [path], skill?: name}` | **SSE stream** (below) · `400` empty message, refused attachment, or unknown skill · `404` · `503` provider unreachable. `skill` names a markdown file from `$UH_HOME/skills` or the shipped `uh/skills`; its instructions ride on the system message for that turn only |
| POST | `/api/sessions/{session_id}/stop` | — | `{stopped: bool, reason?}` · `404` |
| GET | `/api/sessions/{session_id}/decision` | — | `{available, turn: {duration_ms, average_ms, turns}, prompt: {complexity, mode, reason, source, note}, model: {provider, model, served, substituted, chosen}, chain, usage, context: {budget, summarised_exchanges, kept_exchanges, summary}, uhb_consulted, tool_calls, turns}` · `404` unknown session |
| GET | `/api/memory/sources` | — | `{wiki: {available, label, path, notes, reason}, memory, rag, siyuan, sessions}` |
| GET | `/api/memory/wiki` | — | `{available, label, root, notes: [{path, name, size, modified, source}]}` |
| GET | `/api/memory/wiki/{note:path}` | — | `{path, source, size, text}` · `404` unknown or outside the wiki |
| GET | `/api/memory/rag` | — | `{available, queryable, reason, path?, chunks?, documents?, index_mb?, modified?}` — never queryable from UH: UHB has no RAG route |
| GET | `/api/memory/siyuan` | — | `{available: false, configured, reason, url}` — a UHB adapter, no route |
| POST | `/api/memory/rag/reload` | — | `{reloaded, service: {fragments?}, at}` · `409 {error, hint: "start it with: python uhb/services/rag/serveur_rag.py", kind: "service_offline"}` · `502 {error: "UHB unreachable", kind: "uhb_offline"}` |
| GET | `/api/memory/siyuan/blocks` | `?path=&limit=` | `{count, path, blocks: [{id, type, hpath, content}]}` · `400 {kind: "config_missing"}` · `401 {kind: "auth"}` · `502 {kind: "transport"}` |
| GET | `/api/memory/search` | `?q=<text>&limit=` | `{query, hits: [{kind: memory\|wiki\|session, title, path, snippet}]}` · `400` empty q |
| GET | `/api/memory/graph` | — | `{nodes: [{id, label, column, count, unit, state: on\|empty\|off, detail}], edges: [{from, to, label, wired}], wired}` — `state` is about the source (`off` = unreadable), `wired` is about the edge (UH really consults it for a turn) |
| GET | `/api/llm/settings` | `?session_id=` | `{provider, model, support: {provider, model, applied, ignored: [{name, reason}], supports_reasoning}, spec: {key: {kind, min?, max?, step?, choices?, default, label, hint}}, defaults: {enabled, values}, provider_keys}` — the panel draws itself from this; `support` says what the provider will drop |
| GET | `/api/uhb/status` | — | `{connected, version, mode, harness, providers, modes}` · `{connected: false, reason, log?, checkout?}` when UHB is down or disabled |
| POST | `/api/uhb/route` | `{prompt, mode?, complexity?}` | `{mode, complexity, chosen, chain: [{ref, tier, model_id}], reason, allow_paid}` · `400` no prompt · `503` UHB unreachable |
| GET | `/api/dreams` | — | `{dreams: [{stamp, status, tokens, summary, ideas}], daemon: {...}}` |
| GET | `/api/dreams/{stamp}` | — | one dream, including `report`; `404` if unknown |
| DELETE | `/api/dreams/{stamp}` | — | `{deleted}`; `404` if unknown |
| POST | `/api/dreams/{stamp}/archive` | — | `{archived, path}` under `~/.uh/dreams/archived/` |
| POST | `/api/dreams/{stamp}/fulfill?upload=<server>` | — | `{stamp, plan, applied: false, upload}` — writes a plan in `~/.uh/dreams/actions/` **and** in `outputs_dir`; with `upload`, also stores it through that cloud server's upload tool (`{ok, bucket, key}` or `{ok: false, error}`). Applying stays a human decision |
| POST | `/api/dreams/daemon/{action}` | `start` \| `stop` | starts the idle daemon (only on an explicit click) or ends it after the current pass |
| GET | `/api/system/ollama` | — | `{installed, running, models, boost: active\|available\|unavailable, reason, optional: true, url, savings: {local_passes, passes_logged, tokens_saved_estimate, basis}}` — always `200` |
| GET | `/api/system/capabilities` | — | `{tier, ram_gb, cpu_cores, gpu, os, arch, recommended_model, recommended_budget, local_possible, sentence}` |
| GET | `/api/telemetry` | — | `{generated_at, window_days, usage, system, dreams}` — `usage`: `{days: [{date, local, cloud, turns, estimated}], totals (same keys), outside_window, undated, turns_without_usage, cost: {value: null, reason}}`; `estimated` counts turns recorded before decisions carried an `at`, placed on their session's date; `system`: `{cpu_percent, cores, ram, vram, gpu, platform}` where each probe may be `null`; `dreams`: `{passes, lines, diagnostics}` read from `dreams/schedule.log` |
| GET | `/api/backup` | — | `{backups: [{name, bytes, modified}], dir}` — newest first |
| POST | `/api/backup/export` | — | writes `$UH_HOME/backups/uh_backup_YYYYMMDD_HHMMSS.zip`, returns `{path, name, bytes, files, includes, excludes, wal_checkpoint, created_at}`; `keys.json` is never included |
| GET | `/api/backup/download/{name}` | — | the archive as `application/zip`; `404` for any name outside `$UH_HOME/backups/` |
| POST | `/api/backup/import` | raw body: the zip | restores after snapshotting the current home, returns `{restored, skipped, safety, files, was, restart_required}`; `400` when the archive holds no UH data or a member escapes the home, `413` above 256 MB |
| GET | `/api/skills` | — | `{skills: [{name, title, description, origin, native, path, bytes, modified}], user, native, user_dir, native_dir, native_available}` |
| POST | `/api/skills` | `{title, description, instructions, name?}` | writes `$UH_HOME/skills/<name>.md`, returns `{name, path, bytes, title, description}`; `400` for a missing title/instructions, a name that could escape the folder, or an existing file |
| GET | `/api/tools/status` | — | `{toolsets: {name: [tools]}, ollama, uhb: {connected, reason}, mcp: {supported: true, configured, servers: [{name, command, connected, state, protocol, server, version, tools, reason?}], http: [{name, url, connected, state, label, tools, reason, advice}], http_reason?, problems?}, terminal, workspace}` · `state` is `connected`, `idle` (configured, not started) or `failed`; an HTTP server is never reported connected without a live client |
| GET | `/api/setup` | — | `{needed, provider, model, ollama: {running, installed, models}, cloud: [names], huggingface_models: [ids]}` — `needed` is true only when no local model answers **and** no provider key is stored |
| POST | `/api/setup` | `{choice: ollama\|huggingface\|cloud, provider?, key?, model?}` | `{saved, provider, model}` — writes `default_provider` / `default_model` through the config writer and the key through the provider panel · `400` unknown choice or provider, or a cloud choice without a key |
| POST | `/api/fix` | `{instruction}` | `{stamp, applies, error, files, path, notes, diff}` — asks a provider for a unified diff and **proves** it with `git apply --check`; nothing is written to the tree · `400` empty instruction · `502` no provider / model refusal |
| POST | `/api/fix/{stamp}/apply` | — | `{applied, branch: "fix/auto-<stamp>", reverted, changed?, failed?}` — re-reads and re-checks the stored diff, applies it on a new branch, runs `ruff` on `uh` and `tests`, and reverts the patch when that fails. Never commits, never pushes · `400` not a stamp · `404` unknown proposal · `409` the patch no longer applies |
| GET | `/api/mcp/cloud` | — | `{templates: [{id, label, what, can_upload, upload_note, docs, fields: [{key, label, secret, required, placeholder, help}]}], upload_targets: [{name, tool, label}], http_servers: [{name, url, transport, state, label, connected, tools, token, has_token, insecure, reason, advice}], http_note}` — never a stored value; `token` is the mask (`••••` + 4 characters) |
| POST | `/api/mcp/http` | `{name, url, token?, label?, allow_insecure?}` | `{name, transport: "http", url, reason, warning, insecure, token, secrets, connection: {state, label, tools, duration_ms, reason, advice}, state}` — records an HTTP/SSE MCP server (Positron, an IDE, a hosted tool server) as a `[[mcp.servers]]` block with `transport = "http"` **then asks it a handshake in the same gesture**: `state` is the result (`connected`, `auth`, `unreachable`, `incompatible`, `timeout`). The URL is checked by `uh.urlguard`: http(s) only, no credentials in the address, no fragment, and **plain HTTP to a non-loopback host is refused (`400`) unless `allow_insecure: true`** (the token would travel in clear). Token → `mcp-secrets.toml` (owner-only ACL); the answer carries its masked form only · `400` bad name / refused URL · `403` not a loopback bind |
| POST | `/api/mcp/custom` | `{name, command, args: [..], env: {KEY: value}}` | `{name, custom: true, config, secrets, parsed, problems, state}` — adds any stdio MCP server: one `[[mcp.servers]]` block, env entries whose **name** looks like a credential (`TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `CREDENTIAL`) go to `mcp-secrets.toml`, the rest stay visible in `config.toml` · `400` bad name / empty command / `env` not an object · `403` not a loopback bind |
| POST | `/api/mcp/cloud` | `{template, name, fields: {KEY: value}}` | `{name, template, label, can_upload, config, secrets, parsed, problems, state}` — appends (or replaces by name) one `[[mcp.servers]]`; secrets go to `mcp-secrets.toml` (0600) and are merged into the child's environment, never written to `config.toml` · `400` unknown template / missing or unexpected field / bad name · `403` not a loopback bind |
| POST | `/api/mcp/probe` | `{name, transport, url?, token?, command?, args?, env?, allow_insecure?}` | `{state, label, connected, tools, duration_ms, reason, advice}` — handshake against a candidate **without saving it** (5 s cap). The popup keeps "Enregistrer" disabled until this answers `connected` · `400` refused URL / missing command · `403` off loopback |
| POST | `/api/mcp/import` | `{text, filename?}` | `{servers: [{name, transport, command?, args?, env?, url?, token?}]}` — reads a JSON config (`{"mcpServers": {…}}`, a single server, or a list) and the documented YAML subset (maps, scalar lists, quoted strings); normalises names, never writes · `400` unreadable file / unknown shape / empty |
| POST | `/api/mcp/{name}/test` | — | an HTTP/SSE entry: `{name, transport, url, state, label, connected, tools, protocol, server, version, duration_ms, reason, advice}` (P14.1); a stdio entry: `{name, command, connected: true, state: "connected", protocol, server, version, tools}`. Nothing is served from the cache: the answer is what the server said this second · `404` unknown name · `502` a stdio server failed (timeout, crash, JSON-RPC error) |
| GET | `/api/mcp/approvals` | — | `{pending: [{id, server, tool, arguments, asked}]}` — remote tool calls waiting for a click. The SSE stream carries the same ticket as an `approval` frame; this route covers a page that missed it |
| POST | `/api/mcp/approvals/{ticket}` | `{allow: bool}` | `{resolved: true, ticket, allow}` — answers one pending call; anything but `allow: true` refuses it and nothing runs · `404` expired or unknown ticket · `403` off loopback |
| POST | `/api/dreams/run` | `{budget?}` | one pass now, in its own process: `{started: true, pid, budget, log}` · `400` budget not an integer · `409` a pass is already running |
| POST | `/api/uhb/start` | — | starts UHB from the panel (off the event loop, 25 s budget): `{connected: true, …status}` · `{connected: false, reason, log, checkout}` when it still does not answer · `400` `UH_UHB_URL` disabled · `503` the spawn itself failed |
| POST | `/api/uhb/stop` | — | stops the UHB this process started: `{connected: false, stopped}` |
| POST | `/api/models/pull` | `{name}` | `{started, name}` · `400` no name · `429` two pulls already running |
| GET | `/api/search` | `?q=&limit=` | `{query, hits: [{…, short_id}]}` |
| GET | `/api/models` | — | `{provider, model, ollama: [names], providers: [kinds], pulling: [names]}` |
| POST | `/api/model` | `{model, provider?, session_id?}` | `{model, provider}` · `400` |
| GET | `/api/providers` | — | `{providers: [{name, base_url, model, keys, verified, custom, in_chain}], key_entry_allowed, keys: {cloudflare: {configured, source, account_id, key_hints}, deepseek: {configured, key_hints}, omniroute: {detected, base_url, has_key, config_path}}}` |
| GET | `/api/providers/presets` | — | `{presets: [{name, label, base_url, model}], gateway_only: [{name, label, note}]}` · form defaults only, nothing is stored |
| POST | `/api/providers/{name}/keys` | `{key}` | `{stored: true, keys: n}` · `400` · `403` off loopback · `404` unknown |
| DELETE | `/api/providers/{name}/keys` | `{index}` | `{deleted: true, keys: n}` · `400` · `403` · `404` |
| POST | `/api/providers/cloudflare/config` | `{account_id, token}` | `{stored: true, account_id, key_hint, source}` · `400` (account id must be 32 hex) · `403` · no full key in the answer |
| POST | `/api/providers/deepseek/key` | `{key}` | `{stored: true, keys: n, key_hints: [...]}` · `400` · `403` |
| POST | `/api/providers/omniroute/import` | — | `{imported: true, name, base_url, key_hints}` · `403` · `404` no OmniRoute config |
| POST | `/api/providers/custom` | `{name, base_url, model?, key?}` | `{created: true, name, base_url, model}` · `400` · `403` |
| POST | `/api/providers/{name}/test` | — | `{ok, status, error_kind}` · `404` |
| GET | `/api/router/settings` | — | `{allow_remote, consent, consent_date, strategy, extra_providers, warning}` |
| PUT | `/api/router/settings` | `{allow_remote, consent?, strategy?}` | the same object · `400` consent or strategy · `403` off loopback |

`403` on a write route comes from one of two rules. First, the server refuses to
listen beyond loopback unless it was started with `uh web --allow-remote`: the
cockpit writes configuration and runs tools, and the token in the URL is its only
credential. Second, **a state-changing request authenticated by the cookie alone
is refused (`403`) unless it proves its own origin** (`Origin` matching the bound
address, or `Sec-Fetch-Site: same-origin`). A cookie is ambient authority: a page
in another tab would otherwise be able to `POST /api/mcp/http` and register a
server of its choosing (CSRF / DNS rebinding). A request carrying
`Authorization: Bearer` has no such problem and needs no origin.

**No response ever carries a full key or MCP token.** The `keys` object of
`GET /api/providers` is the API-key panel's state: `configured`, the `source`
of the credential (`environment`, `keys.toml` or `none`), and at most a
**masked hint** per stored key (3 leading + 4 trailing characters, e.g.
`sk-…abcd`). A key that comes from the environment is reported as
`source: environment` with no hint at all: UH never wrote it and does not
echo it. Delete-by-index exists for the same reason: a key value must not
travel in a URL, which ends up in an access log. An MCP token is reported the
same way (`••••` plus its last four characters), read from
`mcp-secrets.toml` at answer time and never from `config.toml`.

`error_kind` is one of `auth`, `rate_limit`, `payment`, `server`, `timeout`,
`transport`, `redirect_refused`, `no_base_url` — the same classification the
router uses.

## SSE stream of `POST /api/sessions/{id}/chat`

Frames follow the usual `event: <name>\ndata: <json>\n\n` shape.

| Event | Data | Meaning |
| --- | --- | --- |
| `open` | `{session, model, skill}` | the turn started; `skill` is empty unless one was attached |
| `token` | `{text}` | one streamed fragment |
| `tool` | `{name, …}` | one tool call, before and after |
| `approval` | `{id, server, tool, arguments, timeout}` | a remote MCP tool is waiting for a click; answer with `POST /api/mcp/approvals/{id}`. Nothing runs before the answer, and no answer by `timeout` is a refusal |
| `message` | `{…}` | a whole assistant message when streaming is off |
| `error` | `{message}` | the turn failed; the message is already classified |
| `done` | `{session}` | terminal event, always sent, even after an error or a stop |

`POST …/stop` makes the stream end with `done`, not with an error.

## Element ids used by the current interface

`uh/web/static/index.html` + `app.js` rely on these ids; a replacement interface
served by `--static-dir` is free to use its own, but the ones below are the
contract of the bundled one:

```
attach  attachments  command-chips  composer  files  folder-hint
gauge-fill  gauge-label
decision-panel  decision-view  hardware-card  help-open  input  keys-priority  keys-view
local-panel  local-view  tab-local  setup-banner
data-open  data-panel  data-view  tab-data
conn-badge  conn-view  tab-conn
memory-graph  skill-badge  skill-open
llm-badge  llm-open  llm-panel
pane-detach  pane-reset  pane-&lt;name&gt;  pane-body  pane-head
panel-dock  panel-float  panel-head
memory-panel  memory-view  models-view
tab-telemetry  telemetry-panel  telemetry-view
layout  menu-toggle  messages  model-advanced  model-select  new-folder  new-session  palette
palette-input  palette-list  panel-toggle  providers-close  providers-hint  providers-list
providers-open  providers-panel  providers-view  router-settings  search
remote-banner  search-results  send  session-list  session-title  sidebar
sidebar-toggle
splash  splitter-left  splitter-right  status-provider  stop  tab-keys  tab-models
tab-conn  tab-decision  tab-memory  tab-providers  tab-uhb  terminal-warning  theme-toggle  uhb-panel
uhb-view  version-warning
```

`splash` is the only element that exists before the script runs: the UH mark,
the wordmark and an indeterminate bar are static markup in `index.html`, so the
first paint is not an empty page. `app.js` adds `is-done` (a CSS fade) and then
removes the node; a 4 s timer does the same if `boot()` has not resolved, so a
hung API never leaves the operator looking at a pulsing mark.

`dreams.js` mounts its own elements on activation (`dreams-open`, `idea-badge`,
`dreams-panel`), so a failed dreams route cannot stop the chat from loading.

The layout is three CSS grid columns plus two drag handles
(`splitter-left`, `splitter-right`). The handles write `--sidebar-w` / `--right-w`
on `#layout`; the widths live in `localStorage` (`uh.layout.v1`) so a reload keeps
them, and `#sidebar-toggle` collapses the sessions column (`sidebar-collapsed`).

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
