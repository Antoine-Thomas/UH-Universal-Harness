# Audit brief — for the model that will audit and redesign the interface

**Keep this file up to date at every milestone.** It is the entry point for
someone who has not read the code: it says what the system is, what must never
change, how to run it, and what is admittedly unfinished. An out-of-date brief is
worse than none — a redesign that breaks an invariant here is a regression, not a
taste choice.

State at the time of writing: `main` after Jalon 6b.1, version **0.9.0** in
`pyproject.toml`, `uh-harness` on PyPI up to 0.7.2 until the `v0.9.0` release is
published (`v0.7.0`, `v0.7.1`, `v0.8.0` are tags that were never published).
Tests: 307 passed, 1 skipped.

---

## 1. What UH is

A provider-agnostic agent harness in Python, MIT, local-first, one mandatory
runtime dependency (`httpx`). It runs three surfaces over the same core:

- a **console** (`uh`, or `uh chat`) with slash commands and streamed output;
- a **CLI** (`uh doctor`, `uh keys …`, `uh sessions …`, `uh router …`, `uh web`);
- an optional **local web interface** (`uh web`), token-protected, loopback by
  default, with a **provider panel** (keys write-only, custom providers,
  connection test, `allow_remote` with consent).

## 2. Module map

| Module | Role | Talks to |
| --- | --- | --- |
| `uh/cli.py` | argument parsing, exit codes, thin command handlers | everything below |
| `uh/console.py` | `ChatSession` (history, chain, toolsets, store) and `Console` (rendering) | agent, store, providers |
| `uh/commands.py` | slash-command registry and handlers (`/provider`, `/key`, `/router`, `/local-only`, `/save`, `/export`, …) | `providers_admin`, session |
| `uh/agent.py` | the turn loop: model call, tool calls, iteration budget | providers, toolsets |
| `uh/providers/` | `ollama`, `omnirouter`, `openai_compat` | network (local by default) |
| `uh/router/` | free-provider router: `catalog.toml` (data), `catalog.py`, `pool.py` (rotation, cooldown, breaker), `client.py` (HTTP + SSE + error classes), `policy.py` (consent, `allow_remote`), `provider.py` | remote HTTP, **only with consent** |
| `uh/router/serve.py` | `uh router serve`: a second ASGI app, OpenAI-compatible, own token, loopback only | local clients (editors, scripts), providers through the chain |
| `uh/providers_admin.py` | **the single implementation** of provider/key/settings administration | `keys`, `router`, `config` |
| `uh/keys.py` | `$UH_HOME/keys.toml`, several keys per provider, atomic write, 0600 where possible | filesystem |
| `uh/config.py` | layered config (env > project `uh.toml` > user > defaults) | filesystem |
| `uh/store.py` | SQLite + FTS5, versioned schema, sessions and messages | `$UH_HOME/uh.db` |
| `uh/toolsets/` | `file` (workspace-confined), `terminal` (timeout, blacklist), `memory` | filesystem, shell |
| `uh/web/app.py` | FastAPI app, token, Host/Origin, CSP, routes, SSE | store, session, agent |
| `uh/web/providers_api.py` | HTTP translation of `providers_admin` (no rules of its own) | `providers_admin` |
| `uh/web/static/` | `index.html`, `app.js`, `style.css` — no build step, no framework | `/api/*` |
| `scripts/push_gate.py` | pre-push gate: ruff, mypy, pytest | git hook |

### Data flow of one web turn

```
browser → POST /api/sessions/{id}/chat  (cookie or Bearer token)
        → app.create_app: Host/Origin check, CSP headers
        → ChatSession.run_text in a worker thread
        → provider.chat (ollama | omnirouter | RouterProvider)
        → toolsets (only if enabled; terminal is off unless --allow-terminal)
        → emit() → asyncio.Queue → SSE frames → browser
        → Store: messages persisted as they are produced
```

### Trust boundaries

1. **Terminal ↔ UH.** The operator is trusted; `argv` is *not* a safe place for a
   secret (process list, shell history) — hence no `--key` anywhere.
2. **Browser ↔ UH web.** The token is the only authentication. Loopback bind, a
   `Host`/`Origin` check, `SameSite=Strict` cookie, strict CSP with no inline
   script, `X-Frame-Options: DENY`.
3. **Local programs ↔ `uh router serve`.** Another application uses your quotas
   with a token of its own (`$UH_HOME/router-token`), loopback only, Bearer
   header only, with body and concurrency caps. This boundary is *not* the browser
   one: no cookie, no CORS, no HTML.
4. **UH ↔ remote providers.** Off by default (`allow_remote=false`). Consent is
   recorded, and every call can carry a key and the user's prompt: this is the
   most sensitive boundary in the project.
5. **UH ↔ filesystem/shell.** File tools are confined to the workspace; the
   terminal toolset is a blacklist plus a timeout, not a sandbox.
6. **Keys at rest.** `$UH_HOME/keys.toml`, permissions best-effort, never in git
   (`.gitignore`), never in logs, never in a response.

## 3. Invariants — do not break these

Each line names where it is enforced and the test that proves it. If you change
an invariant deliberately, change the test **and** this list in the same commit.

| Invariant | Enforced in | Proved by |
| --- | --- | --- |
| Keys are **write-only**: no route, command or log returns a key, not even truncated | `uh/providers_admin.py`, `uh/web/providers_api.py`, `uh/commands.py` | `test_known_key_never_appears_in_any_response`, `test_add_key_stores_without_echoing_it`, `test_known_key_never_appears_in_any_command_output` |
| No key ever comes from a command-line argument | `uh/cli.py` (no `--key`), `providers_admin.read_secret` | `test_cli_refuses_a_key_in_argv` |
| Token + `Host`/`Origin` checks on every API call | `uh/web/app.py` | `test_api_requires_a_token`, `test_bad_token_is_rejected`, `test_host_and_origin_are_checked` |
| CSP without `unsafe-inline`; no inline script; `X-Frame-Options: DENY` | `uh/web/app.py` | `test_security_headers_and_no_inline_script`, `test_static_assets_are_served_with_security_headers` |
| Writing a key is refused (403) when `uh web` is not bound to loopback — **no override flag** | `uh/web/providers_api.py` | `test_key_entry_refused_off_loopback`, `test_settings_refuse_off_loopback` |
| The terminal toolset is **off** in the web UI unless `--allow-terminal` | `uh/web/app.py` | `test_tool_events_and_terminal_disabled_by_default`, `test_terminal_can_be_enabled_explicitly` |
| `allow_remote=false` by default; enabling it requires recorded consent | `uh/router/policy.py`, `providers_admin.update_settings` | `test_settings_default_to_local_only`, `test_enabling_remote_without_consent_is_refused`, `test_consent_required_off_tty` |
| No key in logs, errors or traces | all of the above | the same "known key" tests; the uvicorn access log was checked by hand during 7a |
| `https` required off loopback; link-local (169.254.0.0/16) refused; redirects refused | `providers_admin.validate_base_url`, `test_connection` | `test_refused_base_urls`, `test_probe_refuses_a_redirect`, `test_custom_provider_validation` |
| SQLite schema is versioned; reopening is idempotent and a newer schema fails closed | `uh/store.py` | `test_fresh_store_applies_schema`, `test_reopen_is_idempotent`, `test_fail_closed_on_newer_schema` |
| One implementation of provider/key rules for the three surfaces | `uh/providers_admin.py` | `test_providers_commands.py`, `test_web_providers.py` (both call the same layer) |
| `uh router serve` binds loopback only, with no override | `uh/router/serve.py` | `test_non_loopback_bind_is_refused` |
| The router token is separate from the web token, in its own file, compared in constant time | `uh/router/serve.py` | `test_token_is_created_once_and_rotated`, `test_wrong_token_is_401` |
| `uh router serve` accepts `Authorization: Bearer` only (no cookie), and checks `Host`; no CORS | `uh/router/serve.py` | `test_cookie_alone_is_not_authentication`, `test_forged_host_is_refused`, `test_valid_request_sets_no_cors_header` |
| A client's `Authorization` is never forwarded upstream | `uh/router/serve.py` | `test_client_authorization_never_reaches_the_provider` |
| `uh router serve` caps the body by **bytes read**, not by `Content-Length` (chunked requests are capped too) | `uh/router/serve.py` | `test_chunked_body_without_content_length_is_capped`, `test_body_too_large_is_413` |
| A concurrency slot is held until the stream **really** ends, including on client disconnect | `uh/router/serve.py` | `test_streaming_keeps_its_concurrency_slot_until_the_stream_ends`, `test_client_disconnect_releases_the_slot` |
| The access log names the provider of **that** request (no shared trace) | `uh/router/serve.py` | `test_log_line_names_the_provider_of_that_request` |
| `uh router serve` answers a real OpenAI client | `docs/ROUTER_SERVE.md` | exercised by hand with `openai` 3.49.0 (not in CI: the SDK is not a project dependency) |
| A broken stream is reported, never replayed | `uh/router/serve.py` | `test_mid_stream_failure_is_explicit_and_never_replayed` |
| The documented API matches the code | `docs/API.md` | `test_every_route_is_documented`, `test_no_documented_route_is_invented`, `test_router_routes_are_documented` |

Rules that follow from the above, worth repeating to a designer: **the panel must
not add a "show key" affordance**, must not put a key in a URL, must not weaken
the CSP for convenience (no CDN, no inline style), and must keep the key-entry
form disabled when `key_entry_allowed` is false.

## 4. Running everything

From the repository root. Windows uses `Scripts/`, macOS and Linux `bin/`; better,
activate the venv and call the tools by name.

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev,web]"

pytest -q                                  # the whole suite
PYTHONUTF8=0 pytest -q                     # Windows console without UTF-8 mode
ruff check uh tests scripts
mypy
pre-commit run --all-files
pre-commit install --hook-type pre-push    # the push gate
python scripts/push_gate.py                # same gate, by hand

uh doctor
uh web --no-open --port 8765               # then open the tokenised URL it prints
uh web --static-dir ./my-ui                # serve a replacement interface
uh router serve                            # OpenAI-compatible endpoint on 8780
uh router token show                       # the token a local client must use
```

`pytest` needs no network: every provider call is mocked with
`httpx.MockTransport`. `UH_HOME` must not be set when running the suite (the push
gate strips it for exactly that reason).

## 5. Known limits and things that were never tested for real

- **No JavaScript tests.** The interface is verified by API tests plus a headless
  Chrome screenshot taken by hand. A redesign should bring its own harness.
- **The real HTTP path of a free provider has never been exercised.** The router
  and the connection test are covered by simulated transports only: no real key
  was ever used. Treat the first real call as an experiment.
- **`uh router serve` has no per-client tokens.** One token per machine: revoking
  access for one client means rotating and updating all of them.
- **No time-based rate limiting** on `uh router serve`: only concurrency (4) and
  body size (1 MiB) are capped, so a patient client can still spend the quota.
- **The OpenAI SDK check is manual**, not in CI: `openai` is deliberately not a
  project dependency, so a future breaking change in the SDK would only be seen
  by re-running the check by hand.
- **`usage` is not measured** by `uh router serve`: the OpenAI usage block is
  zeros, not tokens counted.
- **No cost accounting.** Tokens are not counted, prices are not known.
- **The terminal toolset is a blacklist plus a timeout**, not a sandbox: it is
  off by default everywhere, and enabling it is an explicit decision.
- **One maintainer, one platform tested by hand** (Windows 11). CI covers
  ubuntu/macOS/Windows for the tests, not the interface.
- **`uh web` has no multi-user notion**: one token per process, no roles.

## 6. Free to redesign, frozen by contract

**Free** — appearance, layout, typography, colours, component structure, the
markup of `uh/web/static/*`, the JS architecture (as long as it stays build-free
or the build is documented), the copy, the navigation, the empty states, the
keyboard shortcuts. A replacement interface can be served today with
`uh web --static-dir`, which keeps the token, the `Host`/`Origin` checks and the
CSP.

**Frozen** — the routes and their semantics in `docs/API.md` (additive changes
are fine, silent changes are not), the invariants in section 3, the write-only
nature of keys, the consent gate, the loopback rules (key entry in the panel, the
whole `uh router serve`), the SQLite schema (additive migrations only), and the
"no secret in a response, a log or a URL" rule. Changing any of those is a security decision, not a design one: it
needs a written justification in the commit message and a test that fails without
the change.

## 7. How to keep this document honest

At the end of each milestone: update section 1 (state), section 5 (limits that
were lifted or discovered), and section 3 **only** when an invariant changed on
purpose — with its new test. `docs/API.md` is guarded by a test; this brief is
not, so it is the one document that depends on discipline.
