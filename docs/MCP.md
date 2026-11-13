# MCP in UH

UH speaks the [Model Context Protocol](https://modelcontextprotocol.io) as a
**client**, over the stdio transport: every server is a child process UH starts,
talks to in newline-delimited JSON-RPC 2.0, and stops when it exits.

That is the point of the feature. File, terminal, memory and web search are UH's
own toolsets; MCP is how somebody else's process - a filesystem server, a
database, a browser - becomes a tool the model can call without UH
reimplementing it.

## Configure a server

`~/.uh/config.toml` (or `./uh.toml`), hand-written:

```toml
[[mcp.servers]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/me/notes"]
```

Every field:

| key | required | meaning |
| --- | --- | --- |
| `name` | yes | letters, digits, `_`, `-`. Part of every tool name the model sees, so a space is refused rather than silently rewritten |
| `command` | yes | resolved on `PATH` first (`npx` is `npx.cmd` on Windows), then used as-is |
| `args` | no | list of strings |
| `cwd` | no | working directory of the child |
| `env` | no | extra environment variables, merged over UH's own |
| `timeout` | no | seconds per request, default `30` |
| `allow` | no | tool names this server may expose. Absent = all of them; present = an allowlist, and a tool not named there is never advertised |

`[mcp]` is a **read-only** table: `uh config set` refuses to rewrite a file that
contains it, because UH's serialiser is flat and a writer that silently dropped
the server list would be worse than one that refuses. Edit the file.

## What the model sees

Each server contributes a toolset named `mcp:<server>`, and every tool is
exposed as `mcp__<server>__<tool>`:

```
mcp__filesystem__read_file
mcp__filesystem__list_directory
```

The prefix is not decoration: two servers may both publish `read_file`, and a
name in a transcript must say which process answered. Names are capped at 64
characters, which is what providers accept.

A toolset is only built when the server completes the handshake. A server that
does not answer contributes **no** tool - the same rule as UHB: a tool that fails
on every call is worse than an absent one. The failure is reported in
`GET /api/tools/status`, never hidden.

## Limits, and why they exist

| limit | value | reason |
| --- | --- | --- |
| one response line | 10 MB | a runaway server must not take the harness down; the line is drained and refused |
| one tool result | 200 000 characters | the model's context is the budget that matters; the cut is marked in the text so nobody wonders |
| request timeout | 30 s (`timeout`) | a hung server costs one call, not the session |
| retry after a failed start | 30 s | the cockpit polls the status endpoint; one dead server must not mean one timeout per poll |
| processes | one per configured server | reused across turns; stopped at exit |

Image and audio blocks are described (`[image: image/png, 41200 base64
characters]`), never inlined: base64 in a context window is tens of thousands of
tokens that mean nothing to a text model.

## Failures

Nothing raises out of a tool call. Every failure is a tool result the model can
read, with a `kind`:

| `kind` | meaning |
| --- | --- |
| `configuration` | the entry in `config.toml` is unusable (missing command, bad name) |
| `transport` | the process is gone, or closed its input |
| `timeout` | it did not answer `initialize` / `tools/list` / `tools/call` in time |
| `protocol` | it answered something that is not JSON-RPC, or more than 10 MB |
| `server` | it returned a JSON-RPC error (the message is quoted) |
| `unavailable` | a recent start failed and the 30 s backoff is still running |

## The cockpit

`GET /api/tools/status` answers:

```json
{
  "mcp": {
    "supported": true,
    "configured": 1,
    "servers": [
      {
        "name": "filesystem",
        "command": "npx",
        "connected": true,
        "state": "connected",
        "protocol": "2024-11-05",
        "server": "server-filesystem",
        "version": "0.6.2",
        "tools": ["read_file", "list_directory"]
      }
    ]
  }
}
```

`state` is `connected`, `idle` (configured, this process has not started it yet)
or `failed` (with a `reason`). The Data panel draws one row per server and one
button per server.

`POST /api/mcp/{name}/test` starts that server, lists its tools and stops it
again. The answer is never served from the process cache: a green dot there
means *this second*. `404` for an unknown name, `502` when the server itself
failed.

## What is not implemented

Said plainly, because a client that pretends to be complete is worse than one
that lists its gaps:

- **only `tools`**, not `resources`, `prompts` or `sampling`: the three methods
  UH's agent loop can actually use;
- **only stdio**, not the HTTP/SSE transports: a remote server would need a URL,
  a token and a threat model UH does not have yet;
- **no `logging` notification handling**: the child's stderr is kept (last 4 KB)
  and quoted in errors; its protocol log messages are ignored;
- **no OAuth**, no per-tool approval prompt: the allowlist in `config.toml` is
  the permission model.

## Tests

`tests/test_mcp.py` runs a real fake server (`sys.executable`, a real pipe, real
JSON-RPC) that also hangs, exits mid-call, returns a JSON-RPC error and writes
11 MB on one line. No network, no installation.
