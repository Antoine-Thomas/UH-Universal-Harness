# Free-provider router (Jalon 6a)

UH ships **no key**. The router is a way to use keys *you* obtained, with a
local-first default: Ollama answers when it can, the router is only reached when
you enabled it and no local model is available.

Code: `uh/router/` (catalogue in `catalog.toml`, transport, pool, policy).
Nothing here is derived from OmniRoute or another router: OmniRoute stays what
it was, an external provider.

## What the catalogue says, and how sure we are

Every entry carries `verified`, `checked_at` and `limits`, and the date is the
day the facts were read on the provider's own documentation. Free tiers change
without notice: `uh providers free` prints the same fields, so drift is visible.

| Provider | Endpoint | Auth | Status (2026-11-12) | What was verified |
| --- | --- | --- | --- | --- |
| OpenRouter | `https://openrouter.ai/api/v1` | bearer | verified | `:free` catalog variants via `GET /api/v1/models`; `429` + `Retry-After`, `402` = credit; a mid-stream 429 arrives as an SSE chunk with `finish_reason: error` |
| Cerebras | `https://api.cerebras.ai/v1` | bearer | verified | `POST /v1/chat/completions`, Bearer auth, model ids from the API reference examples |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | bearer | verified | `POST /v1/chat/completions`, namespaced model ids (`deepseek-ai/…`) |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | bearer | **partial** | the official compatibility page returned HTTP 504 during the check; endpoint and Bearer scheme come from Google's own documented example |
| Mistral | `https://api.mistral.ai/v1` | bearer | **partial** | `/v1/chat/completions` documented; the base URL is inferred from the API reference host, and the free-tier caps were not confirmed |

Not in the catalogue on purpose: anything whose terms forbid this use, and
anything we could not verify at all.

**`partial` entries (Gemini, Mistral today) are not trusted automatically.** The
CLI labels them `UNVERIFIED` — in `uh providers free` and in `uh router status`,
where the `chain` column says `no` — and they stay out of the routing order until
you opt in explicitly:

```
uh config set router_extra_providers gemini,mistral
```

A key alone is not enough: an endpoint we could not confirm on the provider's own
documentation is not something a fallback chain should reach on its own.

What was **not** done: no legal review of any free-tier terms. That is why the
first remote call requires an explicit consent (below), and why the README says
free tiers may keep or train on prompts.

## Routing

```
Ollama (local)  →  OmniRouter (local gateway)  →  router (remote free providers)
```

The router is only added to the chain when at least one catalogue provider has a
key in `$UH_HOME/keys.toml`.

Inside the router:

- **strategies**: `priority` (catalogue order, the default) and `round-robin`
  (`uh config set router_strategy round-robin`);
- **keys rotate**: several keys per provider are tried in order, so one exhausted
  key does not cost the provider;
- **cooldowns**: a failure parks the key for `Retry-After` when the server sends
  one, otherwise 30 s;
- **circuit breaker**: after 3 consecutive failures the provider opens for
  20 s, doubling up to 5 min, then closes on the first success (half-open probe);
- **tools filter**: with tool calling active, only models that advertise tool
  support are used — the lesson from a 0.5b model that called a tool and then
  answered with nothing. For static catalogue entries that is `tools = true`; for
  discovery providers (OpenRouter) it is read from the **real** `GET /models`
  response, where each entry lists `supported_parameters` and `"tools"` in that
  array is the only signal. A missing field counts as "no tools": UH never
  assumes a discovered model can call a function;
- **trace**: the provider and model that really answered land in
  `RouterProvider.last_trace`, shown by `uh router status` and in the web status;
- **no silent retry after output**: if a stream breaks once tokens were already
  printed, UH stops and says so instead of duplicating text.

## Privacy

- `allow_remote` (config) / `UH_ALLOW_REMOTE` (env) / `--local-only` (CLI, which
  always wins) decide whether a remote call may happen. Default: **off**.
- The first remote call additionally needs a recorded consent
  (`remote_consent` in the config). In a terminal UH shows what is at stake and
  asks; off a TTY it refuses rather than guess.
- Keys are never printed, logged, returned by the web API or written anywhere but
  `$UH_HOME/keys.toml` (0600 where the OS allows). `uh router status` shows
  counts, cooldowns and error kinds — never a key, never a prompt.

## Commands

```
uh providers free          # catalogue + limits + where to get a key; in a TTY, stores one
uh router status           # per-provider: keys, ready/cooling, last error
uh --local-only chat …     # force local-only for one command
uh config set allow_remote true
```

## Known gaps (next step: 6b)

- No local HTTP endpoint yet: `uh router serve` (OpenAI-compatible, `127.0.0.1`
  + token) is 6b.
- Tool support comes from the catalogue or from the provider's own `/models`
  answer; a gateway that omits `supported_parameters` will therefore look
  tool-less to UH, which is the safe direction.
- No pricing or quota accounting: only "did it answer".
- A turn is not retried after partial output, by design.
