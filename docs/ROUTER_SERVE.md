# `uh router serve` — local OpenAI-compatible endpoint

`uh router serve` exposes the provider chain UH already holds (stored keys,
fallback, cooldown, breaker) to any local program that speaks the OpenAI
protocol: an editor, a script, an OpenAI SDK. It is **not** a proxy to the
internet: it listens on loopback only, and its token is separate from the web
interface's.

```
uh router serve [--port 8780] [--host 127.0.0.1]
uh router token show
uh router token rotate
```

```
uh router serve - http://127.0.0.1:8780/v1
  token: C:\Users\<you>\.uh\router-token (copy it into the client, never into a URL)
  providers: local first, then the router (consent rules apply)
```

## Authenticate

The token lives in `$UH_HOME/router-token` (0600 where the OS allows), is created
on first use, and is printed by `uh router token show`. `uh router token rotate`
replaces it: every client holding the old one immediately gets `401`.

The **only** accepted form is:

```
Authorization: Bearer <token>
```

No cookie, no query parameter, no `X-Api-Key`. Cookies are refused on purpose: a
browser page must not be able to call this server on your behalf (no CSRF
surface).

## Point a client at it

Any OpenAI-compatible client, with these three values:

| Setting | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:8780/v1` |
| API key | the content of `$UH_HOME/router-token` |
| Model | `auto` (the chain), or a model id listed by `GET /v1/models` |

Environment variables, for tools that only know those:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8780/v1
export OPENAI_API_KEY="$(cat ~/.uh/router-token)"
```

```bash
curl -s http://127.0.0.1:8780/v1/models \
  -H "Authorization: Bearer $(cat ~/.uh/router-token)"
```

```bash
curl -s http://127.0.0.1:8780/v1/chat/completions \
  -H "Authorization: Bearer $(cat ~/.uh/router-token)" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"dis bonjour"}],"stream":false}'
```

Python SDK:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8780/v1",
    api_key=open(os.path.expanduser("~/.uh/router-token")).read().strip(),
)
print(client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "dis bonjour"}],
).choices[0].message.content)
```

## Verified with the real SDK

Exercised on 2026-11-12 with `openai` **3.49.0** installed in a throwaway venv
(outside the repository), against `uh router serve` and a simulated provider:
`models.list()`, `chat.completions.create()` non-stream, and the same call with
`stream=True` — chunks assembled to `bonjour !`, `finish_reason=stop` in both
cases. The output shape below is therefore checked against a real client, not
only against curl.

## What it does with your request

1. `model: "auto"` → the chain: a local model first when one is installed, then
   the free-provider router with its own fallback, cooldowns and breaker.
2. Any other model id → offered to the providers, which use it when they
   advertise it.
3. `tools` → only models that advertise tool support are tried (same rule as the
   agent). Without `tools`, any model may answer.
4. Privacy gate: without `allow_remote` **and** a recorded consent, only the local
   providers are called. The server never prompts (it has no terminal to ask in),
   so it simply cannot reach a remote provider until you have consented once.
5. A stream that breaks after tokens were sent ends with an explicit error event
   then `[DONE]` — it is **never** replayed to another provider.

## Threat model

| Threat | Mitigation |
| --- | --- |
| Another machine on the LAN uses your quotas | loopback bind only; a non-loopback `--host` is refused at startup, with no override |
| A web page calls the server (CSRF, DNS rebinding) | Bearer header only, no cookie; `Host` must be `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>`; no CORS header is ever emitted |
| A leaked web token is reused here | separate token file, separate lifetime, `rotate` independent of `uh web` |
| Your provider keys leak to a client | the client's `Authorization` is never forwarded; upstream calls use the stored key only; no other client header is relayed |
| A runaway script burns free quotas | 1 MiB body cap (`413`), 4 concurrent turns (`429` beyond) |
| Secrets in logs | one log line per request: method, path, status, provider/model, duration. No prompt, no completion, no key, no token. uvicorn's access log is disabled |
| Reading a file outside the workspace | there is no file access here at all: pure model calls |
| A malicious provider redirecting to steal the key | https required off loopback, redirects refused, link-local refused (same code as the router) |

Defaults worth knowing: body ≤ 1 MiB, ≤ 4 concurrent requests, upstream timeout
60 s (the router's), token 32 bytes URL-safe.

## Limits

- One token per machine, no per-client revocation: rotate and update your clients.
- No token accounting: `usage` in the response is zero, not measured.
- No rate limiting over time (only concurrency): a patient client can still use
  the whole quota.
- Loopback only: to reach it from another machine, use an SSH tunnel
  (`ssh -L 8780:127.0.0.1:8780 host`) — that keeps the bind local.
