# Slash commands: Hermes inventory -> UH choices

Read-only inventory of the slash-command registry shipped with Hermes Agent
(`hermes_cli/commands.py`, `COMMAND_REGISTRY`, 105 `CommandDef` entries, 8
categories). Nothing was copied from that code base: UH's registry in
`uh/commands.py` is written from scratch, with its own handlers.

The question asked for each entry is: *does a local, provider-agnostic harness
need this?* A command is retained only if UH can make it work at this milestone
— no placeholders.

## Retained (implemented in `uh/commands.py`)

| Command | Aliases | Effect in UH | Why |
| --- | --- | --- | --- |
| `/help` | `/h`, `/?` | List commands (grouped), or `/help <cmd>` for one | Entry point; mirrors Hermes' `/help` |
| `/model [name]` | — | List providers' models, or switch model in the live session | UH has multi-model local; Hermes switches session-scoped |
| `/provider [name]` | — | Show or switch the provider chain | UH-specific: the harness is provider-agnostic |
| `/provider add <name>` | — | Add a custom provider (base_url, model) or a key for a catalogue entry | Same action as the web panel, same service layer |
| `/provider test <name>` | — | Send one `ping` (`max_tokens: 1`) to the provider | Panel's "Test connection"; no session content |
| `/key list|add|remove` | — | Presence and count, masked entry (`getpass`), deletion by index | Keys are write-only everywhere, CLI included |
| `/router [reset]` | — | Router settings and chain; `reset` clears cooldowns and breakers | Mirrors `uh router status`, plus the live pool |
| `/local-only [on|off]` | — | Nothing leaves the machine; `off` needs the recorded consent | Same gate as `allow_remote` in the panel |
| `/save [file]` | — | Write the session as Markdown (relative paths land in the workspace) | Local transcript, no network |
| `/export [md|json]` | — | Export the session next to the workspace | Same, machine-readable |
| `/tools` | — | List active toolsets and their tools | Same intent as Hermes `/tools list` |
| `/clear` | — | Clear the in-memory history, keep the session | Same as Hermes `/clear` minus screen repaint |
| `/workspace [path]` | — | Show the workspace and the writable paths, or move the session (and the file/terminal toolsets) to `path` | UH-specific: the workspace is the sandbox root, so it must be visible and movable |
| `/new` | `/reset` | Start a new session (fresh id, empty history) | Same as Hermes `/new` |
| `/sessions` | — | List stored sessions | Hermes `/sessions` browses history; UH lists the store |
| `/resume <id>` | — | Reload a session (full id, prefix, or short form) | Hermes `/resume` resolves a named session |
| `/config` | — | Show effective config and the source of each value | Hermes `/config` shows current configuration |
| `/doctor` | — | Environment report (same as `uh doctor`) | UH-specific: local prerequisites matter |
| `/exit` | `/quit` | Leave the console | Hermes `/quit` (alias `exit`) |
| `/history` | — | Show the conversation of the current session | Hermes `/history` |
| `/status` | — | Provider, model, session, message count, context size | Folds Hermes `/status` + `/context` |
| `/version` | `/v` | Print `uh <version>` | Hermes `/version` |
| `/title <text>` | — | Set the title of the current stored session | Hermes `/title` |
| `/retry` | — | Drop the last exchange and resend the last user message | Hermes `/retry` |
| `/memory` | — | Show the three sections of `MEMORY.md` | UH has a memory toolset; read-only view |

## Deliberately not retained

| Hermes command(s) | Why not in UH |
| --- | --- |
| `/login`, `/subscription`, `/topup`, `/usage`, `/insights` | Hermes account and billing; UH has no account and no token accounting |
| `/yolo`, `/approvals`, `/busy`, `/pause`, `/approve`, `/deny` | UH has no approval queue; its terminal toolset uses a documented blacklist instead |
| `/compress`, `/compact`, `/context`, `/undo`, `/rewind`, `/branch`, `/fork` | Context compaction and turn rewinding are not implemented; UH keeps every message (FTS search exists instead of compression) |
| `/bg`, `/queue`, `/steer`, `/goal`, `/loop`, `/plan`, `/moa`, `/review`, `/refine`, `/subgoal`, `/heartbeat` | Multi-agent, background and automation features: out of scope for a single-process local harness |
| `/skills`, `/curator`, `/bundles`, `/learn`, `/init`, `/cron`, `/blueprint`, `/suggestions`, `/plugins`, `/reload*` | UH has no skill system, plugin loader or scheduler |
| `/worktree`, `/diff`, `/rollback`, `/snapshot`, `/save`, `/prompt`, `/handoff` | Git/checkpoint/editor integration; the agent can already use its terminal and file toolsets for that |
| `/skin`, `/statusbar`, `/battery`, `/timestamps`, `/verbose`, `/focus`, `/footer`, `/redraw`, `/palette`, `/indicator`, `/pet`, `/hatch`, `/voice`, `/wake`, `/copy`, `/paste`, `/image` | Terminal-UI decorations and clipboard/voice features; UH's console is deliberately plain (a `[ui]` extra may add a status bar, nothing else) |
| `/whoami`, `/profile`, `/platforms`, `/platform`, `/restart`, `/start`, `/topic`, `/sethome`, `/discard`, `/egress`, `/debug`, `/update`, `/journey`, `/agents`, `/sessions` (Hermes variant) | Gateway/messaging/self-update plumbing specific to Hermes' deployment |
| `/btw`, `/stop`, `/rollback` | No side-channel turns, no background processes to kill, no filesystem checkpoints |

## Unknown command behaviour

An unknown `/xyz` prints a one-line error on stderr and proposes the closest
known name (difflib): `unknown command /modl - did you mean /model ?`. It never
consumes the line as a prompt: a leading `/` is always command intent.

## Jalon 7b.1 — parité console / CLI / web

The provider and key commands are not a second implementation: the console, the
CLI (`uh keys …`, `uh providers test …`) and the web panel all call
`uh/providers_admin.py`. A rule changed there changes in the three places at
once, and a refusal (unknown provider, oversized key, link-local URL, missing
consent) is worded identically.

Two consequences worth stating:

- **No key ever comes from a command-line argument.** `uh providers add` lost its
  `--key` option: `argv` is visible in the process list and kept in the shell
  history. The key is read from `UH_PROVIDER_KEY`, from stdin when piped, or
  from a masked prompt.
- **A key is never displayed**, not even truncated: the commands print
  "absent" or "n key(s)".

`/router reset` only exists in the console, where a live router instance exists;
the CLI cannot reset another process's cooldowns, and pretending otherwise would
be a no-op command.
