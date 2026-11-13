# Changelog

All notable changes to UH (Universal Harness). Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning follows
[Semantic Versioning](https://semver.org/).

Conventions worth knowing:

- every release tag on `main` is annotated, **except** `v0.7.0`, `v0.7.1` and
  `v0.7.2`, which were created as lightweight tags from the GitHub release UI;
- pushing a tag publishes nothing: PyPI publication needs a **GitHub release**
  of that tag (see [docs/RELEASING.md](docs/RELEASING.md));
- versions marked *PyPI* below were actually published; the others exist only as
  tags or releases.

## [1.4.1] — 2026-11-13

The patch after the first evening of real use: the second brain starts, the panel
is a button, and the night pass knows what the harness can do.

### Fixed

- **The second brain starts again on Python 3.10.** `uh web` spawned UHB with the
  interpreter running UH; on 3.10 the child died inside `import` (`datetime.UTC`
  is 3.11+) and the cockpit could only say "uhb unreachable". `uhb_spawn` now
  picks an interpreter it has *asked* two questions of — `sys.version_info >=
  (3, 11)` and `import httpx, fastapi` — preferring the UHB checkout's own
  `.venv`, then `UH_UHB_PYTHON`, then the running interpreter, then whatever the
  machine has. A failed start now quotes the child's last lines instead of
  pointing at a log the user has to find.
- **No window opens on a connection error.** The launcher fired on a 1 s timer
  while uvicorn was still binding; on a cold start that was a blank window. It now
  waits for the server to answer (bare URL, no token, so the token is not spent by
  the probe) before opening anything.
- **The splash stays long enough to be read.** Boot resolving in ~120 ms removed
  it before it was visible; a 900 ms minimum, still capped at 4 s, and never a
  wait for anything.
- **A dream pass no longer calls a partial night "ok".** A source that could not
  be read now sets `partial` with `reason_kind = "sources"`; a source that is
  simply empty (no MCP server configured, no skill installed) is not a failure.
  `uh dream --now` exits non-zero when UHB was offline, even with a report written.

### Added

- **The second brain is a button, not a command.** The panel used to answer
  "UHB not connected. Run 'uh web' or set UH_UHB_PATH" - a shell instruction in a
  browser, for a feature the user can see. It now offers "Connecter le second
  cerveau" (`POST /api/uhb/start`, off the event loop, 25 s budget), tries once
  automatically when the panel opens, and a "Détails" disclosure shows the reason,
  the checkout it looked for and the last lines of `$UH_HOME/uhb.log`. When it is
  connected, a discreet "Arrêter le second cerveau" (`POST /api/uhb/stop`) switches
  it off: chat and tools keep working, which is the whole reason the two processes
  are separate.
- **A dream in one click.** `POST /api/dreams/run` starts one pass in its own
  process (never on the event loop, never twice: `409` while one runs) and writes
  it to `$UH_HOME/dreams/manual-run.log`; the Dreams panel gets an accent
  "☾ Faire un rêve maintenant" button and the header button is drawn in the accent
  colour so it is found without hunting.
- **Twelve native coding skills** (`uh/skills_native/`, shipped in the wheel):
  Python, JavaScript, TypeScript, HTML/CSS, Rust, C++/OpenGL, Java, PHP, Ruby,
  Pascal, React, Angular. Each is written for a senior reviewer — the traps, the
  security rules, and a review checklist — and they appear in the Data panel with
  the "Natif" badge, attachable to a turn from the chat.
- The dream pass sees them: `skills` is a source, so the night report knows what
  the harness can already do, and each configured MCP server is listed
  (`initialize` + `tools/list`, never a `tools/call`), so a proposed workflow names
  a tool that exists instead of one the model invented.

## [1.4.0] — 2026-11-13

A window, a protocol, and a night pass that knows what the harness can do.

### Added

- **`uh web --app`** — the cockpit opens as a window, not a tab: Edge or Chrome
  `--app=<url>` with its own profile under `$UH_HOME/web-app`, no tab bar and no
  address bar. `uh install-shortcut` passes the flag, so one double-click is an
  application; a machine without Chromium gets a sentence and a normal tab.
- **MCP client** (`uh/mcp.py`, contract in [docs/MCP.md](docs/MCP.md)) —
  JSON-RPC 2.0 over stdio: one process per `[[mcp.servers]]` entry,
  `initialize` → `tools/list` → `tools/call`, tools exposed to the agent loop as
  `mcp__<server>__<tool>`, a per-server `allow` list, 10 MB per message, 200 000
  characters per result, a 30 s backoff after a failed start, and every failure
  returned as a tool result with a `kind` instead of a crashed turn.
  `GET /api/tools/status` reports `supported: true` with per-server state;
  `POST /api/mcp/{name}/test` starts one server on demand, and the Data panel
  lists servers with a test button.
- **Dream pass reads the tooling too** (`uh/dream_pass.py`) — `skills` and `mcp`
  joined `siyuan` and `rag` as sources, and neither needs UHB: the pass
  inventories the installed skills and the configured MCP servers (with the tools
  of the ones already connected), and asks the model for `workflows` that name a
  real tool. Names the inventory cannot prove are dropped, and the pass never
  calls a tool — a 03:00 job that can write through somebody else's server with
  nobody awake is a data-loss story with a cron entry. With UHB down, the local
  sources are still inventoried and written; the model is not called.
  `uh dream --source` defaults to `siyuan,rag,skills,mcp`.

### Changed

- README rewritten around what 1.4.0 is: the cockpit first, the window, MCP and
  the night pass, with a real screenshot replacing the 1.3.0 one
  (`docs/assets/cockpit.png`: a turn answered by `qwen3:8b` through an MCP
  filesystem server), CI/licence/Python badges, and the test count corrected from
  638 to 1108.

## [1.3.0] — 2026-11-13

The cockpit release. UH stops being a chat with panels: it has a window, a splash,
consoles that leave the column, live telemetry, a backup you can hold in your hand,
knobs for the model, and a dream daemon that works when the machine is idle.

### Added

- **Dream daemon** (`uh dream daemon`) — a pass runs when the machine is *idle*,
  not when a clock says so: low priority, its own budget that actually stops it,
  XML scheduling with wake and battery conditions, a journal and snapshots. A stop
  is honoured at the next tick and the UI says so instead of pretending.
- **Three actions on a dream** — erase, store, or make a plan (`/api/dreams/{id}/
  fulfill`), and the pass report exports to the Desktop.
- **Splash screen** — the mark and the wordmark are static markup in the first
  paint, the pulse stops under `prefers-reduced-motion`, and a 4 s fallback
  removes the splash if boot hangs. Not clickable: a splash you can dismiss is a
  splash that hid a failure.
- **A window, not a tab** — 12 px gutter, 14 px radius, border, shadow, translucent
  panel surface; full-bleed below 768 px.
- **Telemetry** (`uh/telemetry.py`, `GET /api/telemetry`) — tokens per day split
  local/cloud from the decisions already recorded on the sessions, dream passes
  from `schedule.log`, CPU/RAM/VRAM sampled at request time. The cost is `null`
  with a reason, because UH has no price table and will not invent euros.
- **Dockable consoles** — any configuration view (API keys, Providers, Models,
  Second brain, Memory, Decision, Telemetry, Data) leaves the tab strip and becomes
  its own window: dragged, resized, magnetic to the edges, raised on click, the view
  **moved rather than re-rendered** so listeners and scroll travel with it. The
  layout is remembered in `localStorage`; ⧉ detaches, ⟲ re-docks everything.
- **Memory graph** (`GET /api/memory/graph`) — seven sources, six links, and a
  hard distinction between "has content", "exists but empty" and "not readable".
  Two of six links are real; the others say why ("pas d'outil", "sans route",
  "via UHB"). Drawn in SVG, not Mermaid (see Notes).
- **Conversation settings** (`uh/llm_params.py`, `GET /api/llm/settings`) —
  temperature, top_p, max_tokens, frequency/presence penalties, reasoning effort
  and the `web_search` tool switch, drawn from one table so a slider cannot
  promise a range the backend would refuse. Nothing is sent while the master
  switch is off; values are clamped and stored on the session; the panel prints
  what the provider will ignore.
- **Skills attached to a turn** — `POST /chat` takes `skill: <name>`; the
  instructions ride on the system message, never as a stored user message, and an
  unknown name is a 400 before the provider is called.
- **Data hub** — `GET /api/backup/export` writes a zip of the home (`keys.json`
  excluded, WAL checkpointed), `POST /api/backup/import` restores through a
  staging directory, `GET /api/backup/download/…` hands the file over,
  `GET/POST /api/skills` lists and creates the `.md` skills, `/api/tools/status`
  reports UHB, Ollama and MCP.
- **`uh uninstall`** — refuses to delete the home without a backup, `--purge`
  requires `--yes`, and `--to` chooses where the archive lands.
- **Branding for distribution** — the package description and keywords now say
  what this is (`agent-harness`, `local-first`, `openai-compatible`, `tool-use`,
  `desktop-agent`, `sqlite`, `mit`), the README carries a "Name disambiguation"
  section, and the GitHub description and topics were rewritten to match.

### Changed

- `PATCH /api/sessions/{id}` now accepts `settings`, so a conversation remembers
  the parameters it was written with.
- The `open` frame of the chat stream carries `skill` and `settings`: the client
  can show what a turn is actually using.
- A provider that does not implement `set_params` keeps working (the call is
  guarded): the parameters are an offer, not a requirement.

### Fixed

- **The dreams panel was dead** — `dreams.js` is a classic script and called
  `el()`/`api()` from the ES module, so the panel threw `ReferenceError` inside a
  swallowed `catch` and never mounted. The helpers are now local to the file.
  Three more bugs were behind it: a double `POST /fulfill`, a missing listener on
  the daemon switch, and a label synchronised in one direction only.
- **A lying node in the memory graph** — `memory_file()` returns the *default*
  section names when `MEMORY.md` is missing, so the node read "3 sections" next to
  a file that did not exist. Nodes carry `state` and a null count now.
- The layout test that sliced `app.js` up to the boot banner broke as soon as a
  section was inserted before it; it now stops at the next section marker.
- **The suite only ran on Windows, and it showed**: two dream tests patched
  `os.name` to exercise the Task Scheduler branch, which on Linux turns every
  `Path()` in the process into a `WindowsPath` - the run died at 27 % inside
  pytest's own reporting, without naming a test (`NotImplementedError: cannot
  instantiate 'WindowsPath'`). `dream_schedule` reads the platform through
  `_windows()` now, and the tests patch that. Three router tests also assumed
  `~/.omniroute/.env` existed - true on the maintainer's box, false on a runner -
  and state the environment they need. Both were found by running the suite under
  WSL before publishing, which is now part of the release gate.

### Notes

- **No Mermaid**: it renders through inline `<style>` elements, which
  `style-src 'self'` blocks, and it weighs ~1 MB for seven boxes. The graph is
  drawn by hand in SVG.
- **No Dockview**: measured on 2026-11-13 — `dockview-core` 8.9.0 ships 402 KB
  minified and builds `<style>` elements into `document.head` at load
  (`dockview.esm.mjs:490`), which this CSP drops. The pane layer moves DOM nodes
  instead.
- **Metadata renders at build time**: PyPI and piwheels show the README and the
  summary *as they were when the archive was built*, which is why the disambiguation
  only becomes visible on the index with this release.

## [1.2.0] — 2026-11-13

The cockpit stops being a shell around a chat. It now shows what UH remembers,
how it decided, and which machine it runs on — and two of those views can act.

### Added

- **Memory tab** — the wiki is files (found through `UH_WIKI_DIR`, `wiki/`,
  `llmwiki/` or `uhb/hub/wiki`); notes are listed with size and date and served
  only after the path resolves inside the root, 200 KB per note.
  `GET /api/memory/sources`, `/wiki`, `/wiki/{note}`, `/rag`, `/siyuan` and
  `/search`, which crosses `MEMORY.md`, the wiki and the FTS5 store in one answer.
- **Decision view** — `GET /api/sessions/{id}/decision`: prompt complexity and
  the reason UHB gave, the ordered chain with its tiers, the model chosen and the
  model actually served (an alias can resolve to another tag), tokens, payload
  against the context budget, the summary with the number of exchanges it
  replaces, and the turn's duration next to the average of the last five.
  Recorded at the end of each turn in `sessions.metadata.decisions` — no
  migration, and the decision belongs to a session, not to a message.
- **Two memory actions** — `POST /api/memory/rag/reload` and
  `GET /api/memory/siyuan/blocks`, proxied through UH so the browser never speaks
  to UHB (the CSP stays `self`). A dead RAG service answers 409 with the command
  that starts it; a missing token answers 400 naming `SIYUAN_TOKEN`; a dead UHB
  answers 502 `uhb_offline` — three failures, three fixes.
- **Second brain tab** — `GET /api/uhb/status` and `POST /api/uhb/route`, with a
  coloured dot per tier (free / eco / paid) and UHB's plan shown without spending
  a token.
- **Hardware detection** — `GET /api/system/capabilities` and a Models tab:
  RAM, cores, GPU, then a tier (`remote`, `small`, `medium`, `large`, `xl`), a
  recommended model and a context budget. `psutil` when installed, otherwise
  `GlobalMemoryStatusEx` or `/proc/meminfo`; no GPU tool on `PATH` is a normal
  answer. `POST /api/models/pull` downloads the recommendation in the background.
- **Responsive layout** — three breakpoints (1200 px: the panel becomes an
  overlay with a gear; 768 px: one column, 44 px touch targets, no drag handles),
  and a banner when the device is in the `small` or `remote` tier.
- **Drag a session into a folder**, rename or delete a folder from its header,
  delete a single message, resize the three columns, delete a conversation -
  which also deletes its uploaded files.
- **Command chips** (`/tools`, `/web`, `/new`, `/clear`, `/help`) that insert the
  command instead of running it, and slash commands now reach the registry from
  the web composer: `/tools` answers with the toolsets instead of being asked
  about as a string.
- **File attachments** with a 4 KB threshold — under it the text is inlined, above
  it only name, size and path go out, with a note saying so.
- **`read_pdf`** — PDF text through `pypdf`, page ranges, 50 pages and 10 KB per
  call, and the tool only exists when `pypdf` imports.
- **`uh install-shortcut`** — a desktop button: PowerShell launcher run hidden,
  gold UH monogram icon generated without a dependency, log file, and a MessageBox
  when `uh` is missing.
- **`uh web` starts UHB** and stops it with the UI, including after a hard kill,
  through a Windows job object. `--no-uhb` opts out.
- **Console** — a three-line startup header (Web UI / Update / Session), a UHB
  status line, and `/web` with no argument starts the web UI in a thread.
- **DeepSeek** is a built-in provider preset.

### Changed

- **Context pruning** (`uh/context.py`): the history is never trimmed, the payload
  is. System prompt + first question + first answer + one summary line + the last
  five messages, tool results over 2 KB truncated. Budget precedence:
  `UH_CONTEXT_BUDGET` → config → hardware tier → 75 % of the model's real window
  → 3000, with the tier capped by that window.
- **A summary is reused until the middle grows by four exchanges**, which turned
  eight summaries in a fifteen-turn session into two.

### Fixed

- A turn whose budget was too small to hold a summary reported "N exchanges
  summarised" with an empty summary: the last resort had dropped the middle
  without summarising. `ContextManager` now records how the last payload was
  built (`none` / `summary` / `trim`) and the panel never claims a summary the
  model did not receive.
- The context bar compared the turn's cumulative usage with the per-call budget,
  showing a false overflow (1480/700 on a healthy turn). It now compares the
  payload the pruner measured.
- The Memory tab told the reader to run `uhb/services/rag/indexer.py`, which does
  not exist: the index is compiled from the operator's own notes and is not
  shipped with UHB.
- The Memory tab reported "index not built" while the RAG service answered 4893
  chunks, and described SiYuan as unreadable after UHB grew the route. UHB's
  answer now wins when it exists, and the sentence names the route that reads it.

### Notes

- UHB must ship `GET /modes` and `POST /route` (1.1.0) **and**
  `GET /rag/status`, `POST /rag/reindex`, `GET /siyuan/blocks` (UHB #12).
  `/rag/reindex` reloads an existing index — UHB has no index builder, and the
  changelog does not pretend otherwise.
- No new required dependency: `psutil`, `pypdf` and `tiktoken` stay optional and
  change behaviour only when importable.
- 847 tests, 1 skipped; `ruff check .` and `mypy uh` clean.

## [1.1.0] — 2026-11-13

UH can now ask UHB what it would do *before* doing it. Two new read-only tools,
no token spent, and the toolset refuses to exist when the contract is missing.

### Added
- **`uhb_route(prompt, mode?, complexity?)`** — `POST /route`: the plan UHB
  would follow (complexity, score, signals, reason, ordered failover chain),
  without running a turn and without sending the prompt to a provider.
- **`uhb_modes()`** — `GET /modes`: every mode with the path globs it may read
  and write, the paths it denies, the tools it may call, its tier and whether
  it may use a paid model. `system_prompt` is dropped: it is UHB's instruction,
  not UH's, and it is the longest field of the payload.
- **Contract gate**: the `uhb` toolset is built only when `GET /modes` answers
  within one second. A UHB older than this contract contributes no tool at all,
  instead of tools that answer 404 when the model calls them.
- **Typed tool errors**: `invalid_request` (400), `unroutable` (409),
  `configuration` (500), `http`, `invalid_response`, `transport`, carrying UHB's
  own message so `unknown mode 'nope'; known modes: …` reaches the model intact.

### Notes
- UHB must be running: `python -m uhb.entrypoints.server --port 8101 --mode dev`.
  UH looks at `http://127.0.0.1:8101`; set `UH_UHB_URL` to use another port.
  Loopback only — a remote URL is refused, never contacted.
- Requires the UHB release that ships `GET /modes` and `POST /route`.

## [1.0.3] — 2026-11-13

The sandbox episode: a console started from `C:\Windows\System32` took the OS as
its workspace, `write_file` refused, and the model went round it with `terminal`.
Both halves are closed here, plus the workspace rules a user needs to work in
`~/Desktop` without friction.

### Added
- **Workspace resolution** (`uh/sandbox.py`): `--workspace`, then
  `UH_WORKSPACE`, then the cwd - **unless** the cwd is a system directory
  (`C:\Windows`, `C:\Program Files[ (x86)]`, `C:\ProgramData`, `/usr`, `/etc`,
  `/bin`, `/sbin`, `/System`, `/Library`), in which case `~/uh-workspace` is
  used, created on first use and announced:
  `[WARN] cwd is a system directory; using ~/uh-workspace instead.`
- **Write whitelist**: besides the workspace, writes may target `~/Desktop`,
  `~/Documents`, `~/Downloads`, `~/uh-workspace`, plus `UH_ALLOWED_PATHS`
  (`os.pathsep`) and a hand-written `[providers.file] allowed_paths` table.
  That table is read-only - the config writer is flat and refuses to rewrite a
  file containing one rather than dropping it. System directories are never
  writable, whatever the list says.
- **`/workspace [path]`**: prints the workspace and the writable paths, or
  moves the session and the `file`/`terminal` toolsets (and the system prompt).
  `memory` stays in `$UH_HOME`.
- **System prompt** forbids bypassing a sandbox refusal with `terminal` and
  gives the model the sentence to relay to the user.
- **Startup tip** names `uh web`, `uh providers add`, `/workspace` and `/help`.
- **README**: "Where does UH write?" - precedence, whitelist, refusal text, and
  the honest limit of the terminal guard.

### Fixed
- **`terminal` refuses obvious writes** outside workspace + whitelist
  (`>`, `>>`, `mkdir`, `md`, `touch`, `tee`, and the destinations of
  `cp`/`copy`/`mv`/`move`/`install`/`ln`) before the shell sees the line - the
  hole the incident went through.
- **Deletions are checked the same way**: `rm`, `rmdir`, `rd`, `del`, `erase`,
  `unlink`, `shred`, `truncate`. Removing a file outside the sandbox is refused
  at the same title as writing one.
- **`xcopy`, `robocopy`, `mklink`** destinations are checked where they really
  are (last, second and first argument).
- **Refusal messages** carry the allowed paths, the `system path` wording, and
  the two exits (`/workspace <path>` or `uh --workspace <path>`).
- **Test isolation**: the `~/.uh` snapshot guard no longer trips on a live `uh`
  session (store files excluded) and a `Store()` wrapper catches a real leak;
  every spawned CLI gets an isolated `UH_HOME` (a `uh doctor` run without one
  was checkpointing the developer's WAL).

### Notes
- `terminal` is a seatbelt, not a sandbox: `python -c`, a script created and
  then run, `cmd /c` or an editor still write freely. The file toolset is the
  enforced path; the README and the module docstring say so.
- `cmd.exe` does not expand `~`: in a shell command, use an absolute path or
  `%USERPROFILE%`.

## [1.0.2] — 2026-11-12

Keeps the console usable on a real machine: a strict provider payload, a model
without tool calling, a shell command typed at the prompt, and a test suite that
was writing into the developer's live store.

### Added
- **`uhb_status()` toolset** (UHB Niveau 1): `GET {UH_UHB_URL}/status`,
  loopback only (default `http://127.0.0.1:8101`), summarised as
  `{connected, version, mode, harness, providers, modes}` with names only. The
  toolset is built only when a 1 s probe answers, a remote URL is refused before
  any request, and a UHB that dies mid-session is a `{"error": "uhb
  unreachable"}` tool result, never an exception.
- **Usage metrics after each turn**: providers report the model they really
  served (`model`) and their counters (`usage`), normalised across OpenAI
  (`prompt_tokens`/`completion_tokens`/`total_tokens`) and Ollama
  (`prompt_eval_count`/`eval_count`). The console prints a dim line
  `omnirouter  qwen3:8b  1234 tok (in 312 / out 922)  total session 3456 tok`,
  or `tokens non rapportés`; `/status` gains `served:` and `tokens:`. Streamed
  answers request `stream_options.include_usage` and retry once without it if
  the gateway refuses the parameter.

### Fixed
- **DeepSeek `400 Invalid 'messages[N].tool_calls': empty array`**: an assistant
  message with `tool_calls: []` is replayed on the next turn, and DeepSeek
  rejects it. The empty key is now stripped from every provider payload (Ollama,
  the OpenAI-compatible providers, and the free router's transport) while the
  session history keeps its own shape.
- **Ollama models that cannot call tools** (tinyllama) now raise
  `model '<name>' does not support tool calling; try a different model or
  disable tools.` instead of a raw registry body; the `fallback:` line carries
  the same clause on its classified reason.
- **A shell command typed at `you>`** (`uh doctor`, `pip install ...`, `git
  status`, ...) is refused with a warning instead of being sent to the model:
  no provider call, no tokens, nothing appended to the history.
- **The test suite could write into the real `~/.uh`**: `tests/test_encoding.py`
  spawned `python -m uh doctor` without `UH_HOME`, so doctor opened the live
  store and its clean close checkpointed the WAL - rewriting `uh.db` and
  deleting `uh.db-wal`/`uh.db-shm` of a session that was open. Every spawned CLI
  now gets an isolated home, and a test pins that.

## [1.0.1] — 2026-11-12

Patch release: three console defects reported against 1.0.0, plus the upgrade
command the reported console was missing.

### Fixed
- **Ctrl+C while a provider streams no longer prints an httpx/httpcore
  traceback** and no longer kills the console. `KeyboardInterrupt` is a
  `BaseException`: the provider only catches `httpx.HTTPError` and the CLI's
  last-resort guard only catches `Exception`, so nothing stopped it. The turn
  is now dropped (`[interrompu]` on stderr, half-written line closed), the
  history is rolled back to before the turn, nothing partial is persisted, and
  the prompt comes back; `uh chat --once` returns 130 instead of a traceback.
- **The console shows the logo again.** `_run_repl` asked for the short banner,
  which prints the two-line ASCII art by construction, so no encoding ever
  mattered there. It now renders the full art (which still degrades to the short
  banner under 40 columns).
- **Unicode detection is a capability test**, not a substring search for "utf"
  in the encoding name: `LOGO_UNICODE.encode(encoding)`. A stream that can carry
  █ gets the logo (UTF-8 on modern PowerShell / Windows Terminal), a cp1252 or
  cp850 console and an unknown codec fall back to pure ASCII instead of raising.
- **Readable session header and spacing**: one blank line before every `you>`
  prompt and one between the question and the answer; the header is three dim
  lines (provider · model · stream / workspace · session / messages · context)
  so a long Windows path no longer garbles a single status bar.
- Status and error lines use rich when it is installed and the stream is a TTY
  (grey details, bold red errors), with markup escaped so a message such as
  `[interrompu]` prints literally; plain text otherwise, no ANSI off a TTY and
  `NO_COLOR` honoured.

### Added
- **`uh upgrade`**: reports the running and installed version, runs
  `python -m pip install --upgrade uh-harness` in the same interpreter, then
  reports `old -> new`. Refuses outside a virtualenv unless `--user` is passed,
  keeps the installed version when pip fails, forwards `--dry-run` to pip, and
  only re-execs `uh` with `--restart` (a re-exec of the old argv would loop).

### Notes
- `[interrompu]` is the one deliberately French string, documented in
  `uh/console.py`; every other user-facing message is English.
- **`1.0.0` has no section of its own**: it shipped as a version bump on top of
  0.9.0 (the README's "What 1.0.0 contains" lists the surface). Per-release
  blocks resume here.

## [0.9.0] — 2026-11-12 *(the release that carries 7a, 7b and 6b)*

### Added
- **Provider panel in `uh web` (Jalon 7a)**: list catalogue and custom
  providers, add and delete keys (write-only: only a count is ever returned),
  create a custom OpenAI-compatible provider, test a connection with a single
  `ping`, and switch `allow_remote` with an explicit consent box.
- **One service layer, three front-ends (Jalon 7b)**: `uh/providers_admin.py`
  holds the rules; the console, the CLI and the web panel call it, so a refusal
  is worded identically everywhere.
- Console and CLI commands: `/key list|add|remove`, `/provider add|test`,
  `/router [reset]`, `/local-only on|off`, `/save`, `/export`,
  `uh keys list|add|remove`, `uh providers test`, `uh docs`.
- **`uh router serve` (Jalon 6b)**: an OpenAI-compatible endpoint on loopback
  (`GET /v1/models`, `POST /v1/chat/completions`, stream and non-stream) using
  the stored keys and the existing fallback chain, with its **own token**
  (`uh router token show|rotate`, `$UH_HOME/router-token`).
- `uh web --static-dir`, to serve a replacement interface with the same token,
  `Host`/`Origin` checks and CSP.
- `docs/API.md` with a drift test, `docs/ROUTER_SERVE.md`, `docs/AUDIT_BRIEF.md`,
  `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, issue and PR templates,
  Dependabot configuration.
- A **pre-push gate** (`scripts/push_gate.py` via pre-commit) and pytest isolation
  that fails the run if a test writes into the real `~/.uh`.

### Changed
- `uh providers add` no longer accepts `--key`: a key on the command line is
  visible in the process list and kept in the shell history. It comes from a
  masked prompt, stdin, or `UH_PROVIDER_KEY`.
- The first-run suggestion prefers `lfm3` at the 4 GB tier: measured to call
  `read_file`, `terminal` and `memory_write` correctly, ~1.5 s per turn.

### Fixed
- `uh router serve`: the body cap counts bytes actually read (a chunked request
  has no `Content-Length`); a concurrency slot is held until the stream really
  ends, including on client disconnect; the access log names the provider of that
  request instead of a shared one.
- `SystemExit`/`EOFError` at a consent or provider prompt no longer crashes the
  console: the command cancels.

> **`v0.8.0` is tagged but was never published to PyPI.** It predates 7a, 7b and
> 6b — publishing it would have shipped the router alone — and a PyPI version can
> never be replaced. `0.9.0` is the first release that carries the panel, the key
> commands and `uh router serve`. Same for `v0.7.0` and `v0.7.1`, whose publish
> runs failed and which never reached the index.

### Tooling
- One line-ending convention: `.editorconfig` (LF), `core.autocrlf=false`
  documented in `CONTRIBUTING.md`.

## [0.8.0] — 2026-11-12 *(tag only, never published)*

### Added
- **Free-provider router (Jalon 6a)**: `uh/router/` with a data-only catalogue of
  six OpenAI-compatible free providers, each carrying its verification status
  and the date it was checked.
- Key rotation (several keys per provider), cooldowns driven by `Retry-After`,
  a circuit breaker (3 consecutive failures → 20 s, doubling up to 5 min), and
  `priority` / `round-robin` strategies.
- Streamed failover with a trace of the provider and model that actually
  answered; no silent retry once tokens were printed.
- Tool-capability filter read from the provider's real `/models` answer
  (`supported_parameters`), never assumed.
- `uh providers free`, `uh router status`, and a global `--local-only` flag.
- Remote access is opt-in: `allow_remote` (default false), recorded consent, and
  a refusal when stdin is not a terminal.

### Changed
- `keys.toml` accepts several keys per provider (`keys = [...]`); the previous
  single `key` field is still read.
- Entries marked `partial` in the catalogue are labelled `UNVERIFIED` and stay out
  of the automatic chain until explicitly enabled with
  `router_extra_providers`.

### Fixed
- `--local-only` no longer prints a spurious "unknown config key 'allow_remote'".
- `uh router status` no longer reports a provider as ready when all its keys are
  cooling down.

## [0.7.3] — 2026-11-12

### Added
- `ruff`, `mypy` and `pre-commit` configuration; a `lint` workflow runs them on
  every pull request and on `main`.
- README: quickstart, "Why UH?", and a screenshot taken from the real web UI.
- CONTRIBUTING.md.

### Fixed
- Publishing workflow now gates on tests, checks the release tag against
  `pyproject.toml` and `uh.__version__`, and runs `twine check`.
- A truncated line in the README (`authorization` header sentence).

## [0.7.2] — 2026-11-12 *(PyPI)*

### Added
- Python 3.10 support through `uh/_compat.py` and the `tomli` backport.
- The PyPI publishing job, alongside TestPyPI.

### Changed
- `doctor` accepts Python 3.10 (it previously required 3.11).

## [0.7.0], [0.7.1] — 2026-11-12 *(tags only, never published)*

These two tags were created from the GitHub release UI and are lightweight.
Both publishing runs failed (`Fail-fast in unsupported environments`, the
environment was passed as an action input instead of at job level), so neither
version reached PyPI or TestPyPI.

- `v0.7.0` — publish workflow: environment moved to job level, PyPI job prepared
  but commented.
- `v0.7.1` — Python 3.10 support, PyPI job enabled.

## [0.6.0] — 2026-11-12 *(PyPI)*

### Added
- **Local web UI (Jalon 5)**: `uh web` with streamed SSE chat, session list,
  full-text search, command palette, context gauge, per-session model, light/dark
  themes, served from three static files with no build step.
- Token authentication (cookie `HttpOnly; SameSite=Strict`), `Host`/`Origin`
  checks, strict CSP, terminal toolset off unless `--allow-terminal`.
- Sessions CRUD, `/api/search`, `/api/models`, `/api/status`.
- CI: a `web` job across the three operating systems.

### Changed
- `uh` without arguments opens the console on a TTY; piped output still prints
  help.

## [0.5.1] — 2026-11-11

### Fixed
- Windows CI: tests now read child-process output as UTF-8 instead of the
  runner's cp1252 locale, which killed the reader thread.

## [0.5.0] — 2026-11-11

### Added
- **Console experience (Jalon 4)**: banner, status bar, and a slash-command
  registry (`uh/commands.py`) with aliases, closest-match suggestions and help
  groups.
- Zero-configuration first run: an installed Ollama model, else a model sized to
  the detected RAM (never downloaded without a confirmation), else a local
  router, else a guided free-key setup.
- `uh providers add|remove`, keys stored in `$UH_HOME/keys.toml` (0600 where the
  OS allows).
- UTF-8 stdio on Windows (`_configure_stdio`), CI matrix 3 OS × Python 3.11–3.13.

## [0.4.0] — 2026-11-11

### Added
- **Sessions and configuration (Jalon 3)**: SQLite store with FTS5, WAL and
  cascading deletes; `uh sessions list|show|search|delete|resume`.
- Layered configuration (environment > project `uh.toml` > user `config.toml` >
  defaults), `uh config show|get|set|path`.
- One-shot migration of `~/.uh/MEMORY.md` into the store (idempotent, keeps a
  `.bak`).
- `uh doctor` extended with store, schema version and free disk.

### Changed
- Session ids are shown as `HHMM-xxxx` (the first characters alone were only the
  date and two sessions a day looked identical).

## [0.3.1] — 2026-11-11

### Added
- ASCII/Unicode banner on interactive sessions only (TTY), `UH_ASCII=1` to force
  ASCII, `UH_BANNER=0` to disable.

## [0.3.0] — 2026-11-11

### Added
- **Local model layer (Jalon 2)**: streamed answers, `uh model pull` with
  progress, OmniRouter as an OpenAI-compatible backup, and a traced fallback
  chain.
- `uh providers list` with reachability per provider.

## [0.2.0] — 2026-11-11

### Added
- **Agent core (Jalon 1)**: synchronous `run_turn` loop with a hard
  `max_iterations` budget, and three toolsets — `file` (workspace-confined),
  `terminal` (single command, timeout, documented blacklist), `memory`
  (markdown sections).
- `uh chat` with streamed output on stdout and tool activity on stderr.

## [0.1.1] — 2026-11-11 *(TestPyPI rehearsal)*

Lightweight tag used to rehearse the publishing pipeline; no feature change.

## [0.1.0] — 2026-11-11

### Added
- **Skeleton (Jalon 0)**: installable `uh-harness` package, `uh --version`,
  `uh doctor`, the documented command surface with explicit "not implemented
  yet" placeholders, MIT licence, README.
