# UH V2 — the unified cockpit

Source of truth for the order of work. Written 2026-11-13, at UH **1.1.0**
(sandbox closed, console stable, UHB Niveau 2 wired: `uhb_status`,
`uhb_modes`, `uhb_route`).

Nothing below is a promise about dates. It is a promise about **order** and
about **what must be true before the next phase starts**.

---

## 1. Vision

**One console.** UH becomes the single entry point. `uh web` starts UHB — the
second brain — as a child process, waits for it to answer, and stops it on exit.
The user opens one thing, sees one window, and never manages two terminals.

**The cockpit shows the machine working.** The web UI is not a chat box with a
sidebar: it is an instrument panel. Each turn makes visible what happened —
the memory that was consulted (llmwiki, SiYuan, RAG), the route UHB chose and
why, the tools that ran and what they returned — as gauges, flows and cards
rather than prose.

**It repairs itself, and it dreams.** Like `hermes-home-vision`: a guarded
self-repair path (diagnose, propose, apply only what is reversible) and a
night-time process that reorganises memory when nothing else is running. Both
are background work with an audit trail, never silent writes.

**It is watched.** Wazuh's REST API is the alerting spine for processes and
files. Out of scope until phase 5, but the UI must leave room for it.

**The UI is a fusion.** The layout and density of `harness-maxxing` (three
zones, tool cards, a metadata line per turn) inside the visual language of
`Kiro v4.0` (tokens, two themes, collapsible panels, zero build). See
[`docs/UI_REFERENCES.md`](UI_REFERENCES.md) for what was measured in both.

---

## 2. Phases — the coding order

### P0 — done (1.0.0 → 1.1.0)

Sandbox (workspace rules, whitelist, `/workspace`, shell write/delete guard),
Ctrl+C, `uh upgrade`, usage metrics, provider errors, UHB Niveau 1 + Niveau 2.

### P1 — done (UX: console + web)

Nine tasks, each landed as its own commit with tests on a green suite. The table
below is the contract that shipped, not a plan: the console tasks (6, 8) and the
web tasks (1-5, 7, 9) were independent, so they landed in the order the day
allowed.

Three did not ship in P1 and are **carried into P2** with the sharper wording
that using the result produced: the slash-command chips with a tier gauge (2),
the session sidebar's folders (3, folder half only — delete shipped) and the UHB
status tab in the right panel (4). Task 5 (`uh web` starting UHB) shipped in P2
the same day it was promised; see below.

| ✔ | # | Task | Contract |
| --- | --- | --- |
| ✅ | 1 | Popular providers: **DeepSeek** and **Kimi** | DeepSeek is a built-in provider, so it is a *builtin* preset: key only, posts to `POST /api/providers/deepseek/key`. Kimi already exists as a custom preset (`https://api.moonshot.ai/v1`, `kimi-k2`). |
| ⏳ | 2 | Slash commands as **buttons with gauges** | `/api/commands` already lists the registry. Render the useful ones as chips above the composer; a chip that plans carries a tier gauge (free / eco / paid) taken from the last `uhb_route` decision, not from a hard-coded colour. |
| 🟡 | 3 | **Delete + folders** in the session sidebar | `DELETE /api/sessions/{id}` exists and cascades (`PRAGMA foreign_keys=ON` verified). Trash icon on hover with a confirm step. "Folder" = a `folder` key in session metadata, `PATCH /api/sessions/{id}` accepting it, sidebar grouping. |
| ⏳ | 4 | **UHB status widget** in the right panel | New tab "Second brain": `GET /api/uhb/status` (UH → UHB, loopback only) showing `connected`, `mode`, `version`, `harness`. The browser never calls UHB directly (CSP stays `'self'`). |
| ✅ | 5 | **`uh web` starts UHB** | Spawn only if the local UHB checkout exists and nothing already listens on the port; wait for `/status` with a timeout; terminate on exit (including Ctrl+C); `--no-uhb` to opt out. Failure to start is a warning, never a crash. Shipped as `uh/uhb_spawn.py`: loopback only, `CREATE_NO_WINDOW`, log in `$UH_HOME/uhb.log`, and a **job object** so the kill reaches the grandchild (`sys.executable` is a shim on Windows). |
| ✅ | 6 | **Explicit console header** | `_run_repl` prints three lines instead of today's single `STARTUP_TIP`: `Web UI: run 'uh web'`, `Update: run 'uh upgrade' (current: vX.Y.Z)`, `Session: /new for a new session · /help for commands`. The version comes from `uh.__version__`, not from installed metadata (a checkout and its wheel disagree). Respects `UH_QUIET`. |
| ✅ | 7 | **UHB status in the header** | When the `uhb` toolset exists (it is built only if `GET /modes` answers), the console adds `UHB: connected (vX.Y.Z) · mode: <mode> · <n> providers`. Absent toolset ⇒ no line, not a warning: a missing UHB is a normal state. One `status()` call at startup, already-bounded by the 1 s probe. |
| ✅ | 8 | **`/web` in the REPL** | **Name already taken**: `/web <query>` runs the direct web search (registry, `_cmd_web`). Contract: `/web` **without argument** starts `uh web` (server + browser) in a background thread and keeps the session alive; `/web <query>` keeps searching. A second `/web` reports the URL already served instead of binding twice. Console keeps working while the UI runs; `/exit` stops the server. |
| ✅ | 9 | **`uh install-shortcut`** (desktop button) | Windows-only subcommand (clear refusal elsewhere): writes `uh-web.ps1` under `$UH_HOME` that runs `uh web`, creates a Desktop `.lnk` via WScript.Shell pointing at it, with the Searching Murphy colours `#284543` (ink) / `#fdc502` (accent) and a gold **UH** monogram. No admin, no auto-start, prints the created path. The icon is generated in pure Python (zlib PNG inside an ICO, 16/32/48/256, no new dependency, no binary asset), and the launcher runs hidden — the browser is the only window. |

### P2 — the cockpit shell

Tokens first (`--bg`, `--surface`, `--border`, `--text`, `--muted`, `--accent`,
`--danger`, `--success`, `--warning`, `--radius`, light + dark), then the
message footer (`model · elapsed · tokens in/out · cost?`), tool cards with a
state colour (ok / refused / error), and an event flow view fed by the existing
SSE stream.

**Landed early, because using the fixed shell hurt** (2026-11-13):

- **the zones move**: two drag handles between the columns (`--sidebar-w`,
  `--right-w`), clamped 180-460 / 200-560 px, persisted in `localStorage`
  (`uh.layout.v1`), double-click to reset, arrow keys when focused, and the
  sessions column collapses with `«`;
- **delete a conversation**: trash icon on hover, one confirm step,
  `DELETE /api/sessions/{id}` (which cascades to the messages);
- **folders**: `folder` key in session metadata, `PATCH {folder}` accepting it,
  sidebar grouped with the named folders above "Sessions", `+ Folder` and the
  per-session folder button;
- **no console window**: the desktop shortcut runs the launcher hidden
  (`-WindowStyle Hidden`), logs to `$UH_HOME/uh-web.log` and shows a MessageBox
  on failure — the browser is the only window;
- **`uh web` starts UHB** (P1 #5, late): found through `UH_UHB_PATH` or the two
  usual layouts, waited for on `GET /status` (5 s), logged, invisible, and killed
  with the UI — including on a close-button kill, through a job object. A
  failure is one warning line; `--no-uhb` opts out;
- **drag a session into a folder**: rows are draggable, folder headers are the
  targets, and the empty-name header unfiles (the only way to undo a filing
  without typing);
- **rename or delete a folder** from its header. A folder is metadata, not a
  row: rename updates every session carrying the value, delete clears it — the
  conversations stay, unfiled, which is what the confirm says;
- **delete a single message** (`DELETE /api/sessions/{id}/messages/{mid}`,
  session-scoped, FTS trigger keeps the index honest): the wrong answer, not the
  whole day;
- **file attachments** with a 4 KB threshold: under it the text is inlined into
  the message, above it only name/size/path go out with a note. Images and videos
  are referenced and labelled honestly (the providers read text). Uploads are
  `$UH_HOME/uploads/<session>/`, a path outside that folder is refused, and
  deleting a session deletes its files;
- **context pruning** (`uh/context.py`): the history is never trimmed, the
  payload is. System prompt + first question + first answer + one summary line +
  the last five messages, tool results over 2 KB truncated, budget from
  `UH_CONTEXT_BUDGET` / config / 75% of the model's native window / 3000;
- **`read_pdf`**: PDF text through pypdf, page ranges, 50 pages and 10 KB per
  call, and the tool only exists when pypdf imports.

Still open, and this is the real P2 backlog:

- ~~the two web-side P1 leftovers~~ — landed late in P2: the **Second brain**
  tab (`GET /api/uhb/status` and `POST /api/uhb/route`, both proxied by UH so the
  CSP stays `'self'`) and the **command chips** (`/tools`, `/web`, `/new`,
  `/clear`, `/help`), which only became meaningful once `POST /chat` sent a
  leading `/` through `uh_commands.dispatch` instead of to the model;
- **the quickstart**: `pip install -e .` is still required before the desktop
  shortcut exercises a working copy. A release, or a `uh install-shortcut
  --from-checkout`, would remove the trap — the shortcut currently runs whatever
  `uh` is on PATH.

### P3 — memory made visible

Read-only first: the cockpit observes before it acts.

**Landed** (2026-11-13):

- **P3.1 — memory routes** (`ec88165`, `uh/memory_view.py`): the wiki is files,
  found through `UH_WIKI_DIR`, `wiki/`, `llmwiki/` or `uhb/hub/wiki`; notes are
  listed with size and date, served only after the path resolves inside the
  root, 200 KB per note. RAG is `index.faiss` + `chunks.jsonl` +
  `manifeste.json` on disk and UHB has **no** route for it, so the payload says
  what is built and `queryable: false` with the reason — it does not fake an
  endpoint. SiYuan reports `configured` and that configuration is necessary but
  not sufficient. `/api/memory/search` crosses `MEMORY.md`, the wiki and the
  FTS5 store in one answer;
- **P3.2 — the Memory tab** (`a006cca`): wiki list and reader, search grouped by
  kind, RAG and SiYuan states, `MEMORY.md` sections. Works with UHB down (a
  banner says which views are limited). No Reindex button: a test forbids the
  word in the JS, because a button that can only fail is worse than a sentence;
- **P3.3 — the Decision view** (`83e0eb8`, `uh/decision.py`): complexity, chain,
  chosen model, **served** model, tokens, budget, payload size, summary,
  duration and its average, recorded at the end of each turn in
  `sessions.metadata.decisions` (last ten) — no migration, a decision belongs to
  a session, not to a message.

**The Decision view is honest about what was not consulted; it must never invent
a datum.** UH does not route every turn through UHB: the router runs when the
model calls the `uhb_route` tool or when the user asks. A turn without it shows
"UHB was not consulted", and a plan requested from the tab is labelled "not
applied to the turn". Two guards pin that down: the panel may only read
`complexity` from the payload or from `/api/uhb/route`, and `ContextManager`
records how the last payload was built (`none` / `summary` / `trim`) so a turn
that dropped its middle is never described as summarised. The context bar
compares the **payload** with the budget, not the turn's cumulative usage.

Still open:

- **P3.5 — two routes in UHB**: `POST /rag/reindex` (+ `GET /rag/status`) and
  `GET /siyuan/blocks`. UHB is a separate repository: the change lands there, and
  only then can UH grow a Reindex button instead of an explanation;
- **P3.6 — wiring**: Reindex and Sync buttons in the Memory tab, once the routes
  exist;
- **summary fidelity**: the summary is written by the **current** provider,
  because routing it through UHB needs a resolver from a route `ref` to a
  (provider kind, model) pair. When that exists, a small model can summarise and
  a large one can keep the decisions.

### P4 — dream mode and self-repair

Self-repair: diagnose → propose → apply what `uh doctor` can verify, one
reversible step at a time.

**Dream mode** is a background pass over memory and documentation, with no human
in the loop while it runs. Five steps, in this order:

1. **read** — `llmwiki/`, `MEMORY.md`, SiYuan, the project docs;
2. **clean** — find duplicates and contradictions (same fact in three files,
   contradicting dates, a version hardcoded in prose); archive what it replaces
   under `dreams/archive/<date>/`;
3. **file** — propose a folder structure for the notes, and apply it only inside
   `dreams/`;
4. **review** — compare the roadmap against `git log`: tasks ticked but not
   committed, commits with no tick; report to `dreams/audits/<date>.md`;
5. **produce** — 5-10 improvement ideas, each with a title, two sentences, and
   the model that produced it, in `dreams/idees/<date>.md`.

What has to exist for that to be a command rather than a ritual:

- a **scheduler**: `uh dream --at 03:00`, `--now` for one pass, `--rollback` for
  the last one. A thread, not a Windows service;
- a **UHB mode** `dream`: read everywhere, write **only** in `dreams/`, `eco`
  tier (no paid model), `max_tokens` per pass. UHB enforces it; UH does not
  promise it;
- a **journal**: `dreams/journal.md`, one line per action (file read, file
  written, backup created, idea produced, model used);
- **reversibility**: a backup before every write, and `--rollback` restoring the
  previous pass from it;
- a **budget cut-off**: when the pass hits its token budget (`usage_snapshot()`),
  it stops cleanly and writes a partial report instead of pretending it finished.

Testable contract:

- `uh dream --now` on a workspace with three duplicated notes produces a report,
  archives the duplicates, appends to the journal, and leaves `llmwiki/` in a
  deterministic state;
- `uh dream --rollback` restores the state before the last pass;
- with UHB down, `uh dream` refuses with one clear line: the modes are the
  guard-rail, and a dream pass without them would write wherever it liked.

"What it does *not* do" is part of the contract: it does not modify `uh/`,
`tests/` or `pyproject.toml`, and its ideas are **proposals**, never applied
patches. Self-improvement stays `route → review → propose → human applies`.

**Multimodal, when a provider actually reads images.** Attachments label images
and videos "the model cannot see it" because UH sends text only. When a provider
adapter supports vision (Claude, GPT-4o, Gemini), the attachment should become a
`content` array in the OpenAI vision shape — `{"type": "image_url", "image_url":
{"url": "data:image/png;base64,…"}}` — built from the stored upload, and the note
becomes "sent inline". Until then the honest note stays: a path in a text message
does not make a picture visible.

### P5 — Wazuh and packaging

Wazuh REST integration (alerts feed in the cockpit), then the `uh-suite`
meta-package so one `pip install` brings UH + UHB.

**Opt-in auto-routing, not a default.** Today UHB is consulted only when the user
asks or when the model calls `uhb_route`; the Decision view says so. A future
`--uhb-auto-route` flag may route every turn through `/route`, but it changes
which model answers every question, so it stays opt-in and off.

---

## 3. Components check against `hermes-home-vision`

Read 2026-11-13. **"Present" means the code exists and is wired where stated** —
none of these was executed for this audit, and none is exercised by UH's 591
tests.

| Component | `hermes-home-vision` | UH | UHB | Verdict |
| --- | --- | --- | --- | --- |
| RAG (hybrid search) | `scripts/rag_hybride/` (indexer, serveur, audit) | — | `uhb/services/rag/` (`chercher.py`, `serveur_rag.py`) | **Present in UHB**, not surfaced in UH |
| SiYuan | `docs/siyuan/` (chercher, indexer, publier) | — | `uhb/adapters/second_brain.py` (`read_siyuan`, token `SIYUAN_TOKEN`, `POST /api/export/exportMdContent`) | **Present in UHB**, absent from UH |
| LLM wiki | `wiki/` (concepts, entities, SCHEMA, scripts) | — | `uhb/hub/wiki/` (raw, wiki, ingest/lint/query scripts) | **Present in UHB**, no UI |
| Decision AI | `plugins/intelligent_memory`, `anima-memoire-router` | `uhb_route` tool (1.1.0) | `uhb/hub/router.route()` + `hub/modes` + `POST /route` | **Present and wired** (UH asks UHB before spending) |
| Persistent memory | `plugins/intelligent_memory` | `uh/toolsets/memory.py` (`MEMORY.md`) | `uhb/core/memory.py` | **Present in both** |
| Dream mode | `dream/PROMPT.md` + nightly reports | — | — | **Missing** (P4) |
| Self-repair | `recovery_runbook.md`, health scripts | `uh doctor`, auto-heal skill (Hermes) | `GET /health` | **Partial** (P4 to close) |
| Process visualisation | docs only | — | — | **Missing** (P2/P3) |
| Wazuh | docs only (`ANIMA_ARCHITECTURE.md`) | — | — | **Missing** (P5) |

Two consequences for the plan:

- the memory layer does not need to be rebuilt, only **exposed**: RAG, SiYuan
  and the wiki are in UHB, behind no HTTP route today except the wiki scripts;
- the decision layer is already the strongest piece — P3 should visualise it
  before P4 adds anything that writes.

---

## 4. Constraints that do not move

- **No new runtime dependency** without a written reason; `httpx` and the
  standard library have carried the project this far.
- **The browser talks to UH only.** Every UHB call is proxied by UH on
  loopback; the web CSP stays `'self'`.
- **The suite stays green**: pytest, ruff, mypy at every commit.
- **No feature is "done" without a failure mode**: a dead UHB, an absent
  checkout, a missing folder, a refused tool — each needs a visible state, not
  an exception.

---

## 5. Out of scope, explicitly

- Wazuh integration and the `harness-maxxing` visual fusion: phase 5 and phase 2
  respectively. Not started.
- Multi-user, remote access, cloud sync: not a goal. UH is a local cockpit.


## 6. P6 - Cockpit UI/UX and visual telemetry (opened 2026-11-13)

**State: closed on 2026-11-13, shipped in 1.3.0.** Every item below is delivered
or explicitly declined with a measurement; the dockable consoles carry a written
list of what a real layout engine would still add.

The backend is done enough to be looked at: the question is now what the operator
sees while it runs. Items are listed with their state, not their ambition.

- **Report on the Desktop** - *delivered* (`--export-desktop`, copy to
  `~/Desktop/UH_Rapport_Nuit.txt`). A pass nobody opens is a pass nobody reads.
- **Splash screen** - *delivered*. Static markup in `index.html` (the mark, the
  wordmark, an indeterminate bar), so the first paint is not an empty page; the
  mark pulses while the shell wires itself and the first API calls land, then
  `app.js` fades the overlay and removes it. A 4 s timer ends it if `boot()`
  hangs - no click, because a splash you can dismiss is a splash that hid a
  failure - and `prefers-reduced-motion` stops the pulse.
- **Independent window** - *delivered*. A 12 px gutter on `body`, and `#layout`
  carries a 14 px radius, a border, a shadow and a slightly translucent
  background: the page reads as a desktop app, and the browser is still the
  runtime. A phone (<= 768 px) keeps the full-bleed layout; the right-panel
  overlay at <= 1200 px now sits inside the frame instead of over it.
- **Dockable consoles** - *v2 delivered* (the full engine is still not here, and
  the reason is measured). Two layers:
  1. the Configuration panel detaches as a whole (v1): dragged by its header,
     resized natively, magnetic to the left/right/bottom edges, docked back;
  2. **any configuration view** - API keys, Providers, Models, Second brain,
     Memory, Decision, Telemetry, Data - leaves the tab strip with ⧉ and becomes
     its own window: draggable, resizable (`resize: both`), magnetic, raised on
     click, content preserved because the view **is moved, not re-rendered**
     (listeners, scroll and CSS travel with the node). Several consoles can be
     open at once; ⟲ re-docks everything and clears the stored layout. The layout
     is remembered in `localStorage` (`uh.panes.v1`) - furniture, not data - and
     below 768 px the buttons disappear, the windows dock themselves and the
     console is the tabbed panel again.
  - **Why not Dockview/GoldenLayout**, measured on 2026-11-13: `dockview-core`
    8.9.0 has no dependencies but ships 402 KB minified, and at load it builds
    `<style>` elements and appends them to `document.head`
    (`dockview.esm.mjs:490`). Under `style-src 'self'` with no `'unsafe-inline'`
    those sheets are dropped - the same hole we refused for Mermaid. The
    `noStyle` build avoids the injection and loses the drag-overlay rules with
    it. For eight panes that move as DOM nodes, a 402 KB bundle plus a CSP
    exception is not a trade, it is a downgrade.
  - **What a real engine still buys** (not done, honestly): docking *inside* a
    split tree (drop a pane on the middle of another to split it in two), tab
    groups inside one window, and layering panes in the grid rather than over it.
    If that is ever wanted, the measurement above is the price tag.
- **Telemetry** - *delivered* (`uh/telemetry.py`, `GET /api/telemetry`, tab
  "Telemetry"). Tokens per day split local/cloud from the decisions already
  recorded on the sessions (`at` is now written with each turn; a record that
  predates the field is placed on its session's date and marked "≈"), the last
  ten passes from `dreams/schedule.log` (status, wall time, model, tokens), and
  CPU/RAM/VRAM sampled at request time - `nvidia-smi` cached five seconds, the
  CPU sampled off the event loop. Drawn as an SVG bar chart with no library and
  no CDN (the CSP is `'self'`). **Cost is not computed**: UH has no price table,
  so the payload says `cost: {value: null, reason}` and the panel prints the
  reason. Polling stops when the tab is not in front.
- **Second brain, drawn** - *delivered*, in SVG rather than Mermaid.
  `GET /api/memory/graph` returns seven nodes (MEMORY.md, wiki, RAG, SiYuan, the
  turn, sessions, dreams) and six edges, and an edge is `wired` only when UH
  really consults that source for a turn: MEMORY.md (the `memory` toolset) and
  the session history, nothing else. The three unwired ones are dashed and
  labelled ("pas d'outil", "sans route", "via UHB"), and the panel says
  "2 / 6 liens branchés". **Why not Mermaid**: it renders through inline
  `<style>` elements, which the CSP (`style-src 'self'`, no `unsafe-inline`)
  blocks, and vendoring ~1 MB of JS to draw seven boxes contradicts "no new
  runtime dependency without a written reason". The drawing is ours: rects,
  bezier curves, a flowing dash animation on the wired edges (stopped by
  `prefers-reduced-motion`), hover highlights the hovered node's edges, click
  prints the node's detail. Mermaid stays possible the day the graph needs
  arbitrary shapes, and would then need a deliberate CSP change.
- **Skills attached to a turn** - *delivered*. The composer's ⚑ button lists the
  .md skills, the badge above the input says which one is active, and
  `POST /chat` receives `skill: <name>`: the instructions ride on the **system
  message** (labelled "## Active skill: …"), never as a stored user message, so
  the transcript still says who said what. An unknown name is a `400` before the
  provider is called.
- **Conversation settings** - *delivered* (`uh/llm_params.py`, `GET
  /api/llm/settings`, card above the composer). Temperature, Top P, max tokens,
  frequency/presence penalties, reasoning effort and the `web_search` tool
  switch, drawn from one table in Python so a slider cannot promise a range the
  backend does not know. Nothing is sent while the master switch is off; when it
  is on, values are clamped server-side and stored on the session
  (`metadata.settings`, through `PATCH`), so opening a conversation restores the
  temperature it was written at. The panel prints what the provider will
  **ignore** (`reasoning_effort` on Ollama, reasoning on a non-reasoning model)
  instead of pretending every slider does something.
- **Maintenance** - temp files, caches, orphaned uploads, with the reclaimed space
  reported before and after. Deletion is explicit, never silent. *To do.*
- **Voice and text helpers** - TTS and dictation through the Web Speech API (no
  key, no cloud, degrades to absent where the browser lacks it), automatic session
  titles, prompt improvement, and an error explainer that reads a traceback and
  says which cause is likely. *To do, in that order.*

Constraint for every item above: `pytest -q` stays green, the nightly task keeps
working, and nothing here may require Ollama, a key, or a network call.


## 7. P7 - The daemon and the daydream (opened 2026-11-13)

The 03:00 task stays as a safety net, but it is not the idea. A report produced
while the machine sleeps is a report nobody asked for; the useful moment is the
pause - the coffee, the meeting, the twenty minutes away. The pass runs then,
quietly, and the operator decides what happens to it.

- **Idle trigger** - *delivered* (`uh dream --daemon`). `GetLastInputInfo` via
  ctypes, one check per minute, `UH_IDLE_MINUTES` (default 15). When the API
  cannot answer, the daemon reports `unsupported` and dreams nothing: guessing
  "you are away" is how a laptop starts working at the wrong time.
- **Below-normal priority** - *delivered*. The pass asks Windows for
  `BELOW_NORMAL_PRIORITY_CLASS`, so a daydream does not compete with what you are
  doing and does not spin the fans.
- **One absence, one pass** - *delivered*. After a pass the daemon waits for real
  input before dreaming again, and `--daemon-stop` ends the loop after the current
  pass (`--daemon-status` says what it is doing).
- **Dreams panel** - *delivered*, then repaired. In the Second brain tab: state
  ("Idle monitoring: 0/15 min", "Dreaming..."), the report title and summary, and
  three buttons - **Effacer** (delete), **Stocker**
  (`~/.uh/dreams/archived/`), **Exaucer** (checklist of ideas that produces a
  markdown action plan in `~/.uh/dreams/actions/`, never a change to the machine
  without a final confirmation). The panel is mounted by `dreams.js` from the top
  bar; that script is a classic script while `app.js` is an ES module, so it
  carries its own `dreamsEl` / `dreamsApi` helpers - reaching for the module's
  threw `el is not defined` inside `mountDreams`, which caught it, and the button
  and the badge never appeared.
- **"L'idee du jour"** - *delivered*. A discreet badge in the header that opens
  the Dreams panel on the latest idea. A notification, not a popup.
- **Endpoints** - *delivered*. `GET /api/dreams`, `GET /api/dreams/{stamp}`,
  `POST /api/dreams/{stamp}/archive|delete|fulfill`.

Constraint, unchanged: the daemon never writes to SiYuan, never reindexes, and
never requires Ollama - a pass that cannot run is logged and skipped.


## 8. P8 - Data safeguard and skills hub (opened 2026-11-13)

The interface can look like an application; what makes it a product is that the
data inside it belongs to the user and can leave. It already does: every
conversation, note, skill and report is a file under `$UH_HOME`. The browser
keeps two things - the column widths in `localStorage` and a session cookie - and
losing either costs nothing. P8 makes the files visible, portable and restorable.

- **Global export (ZIP)** - *delivered* (`uh/backup.py`, tab Data).
  `POST /api/backup/export` writes `$UH_HOME/backups/uh_backup_<stamp>.zip`
  holding `uh.db` (WAL checkpointed first, or the archive would silently miss the
  last turns), `MEMORY.md`, `config.toml`, `dreams/`, `skills/` and `uploads/`.
  `keys.json` is excluded by name and the manifest says why: a backup is a file
  that travels, and a credential inside it is a leak dressed as a feature.
  `GET /api/backup/download/<name>` hands the same bytes to the browser, because
  a path alone is useless to it.
- **Global import** - *delivered*. `POST /api/backup/import` takes the zip as the
  raw body (no multipart, no new dependency), refuses a member that escapes the
  home, snapshots the current home to `backups/uh_pre-import_<stamp>.zip` **before**
  writing anything, and removes stale `uh.db-wal`/`-shm` before replacing the
  store - a journal next to an incoming database replays the wrong pages. The
  panel says "restart UH": a live process holds handles on the old file. An
  import **restores**, it does not wipe: a file that is not in the archive is
  left where it is, and the panel says so instead of promising a clean slate.
- **Skills** - *delivered* (`uh/skills.py`, tab Data). One skill is one `.md`
  file: `# Title`, `> description`, `## Instructions`. Two roots -
  `$UH_HOME/skills` (yours, writable) and `uh/skills` (shipped, read-only) - and
  neither ships a native skill yet, so the panel says that instead of showing a
  badge nobody can find. `POST /api/skills` writes the template and refuses any
  name that is not a filename, so nothing walks out of the folder.
- **Skills that are actually used** - *delivered* (see P6, "Skills attached to a
  turn"). The block is built by `uh/skills.prompt_block`, labelled, and sent on
  the system message; the console can pass one too through
  `ChatSession(extra_system=...)`. What is *not* done: choosing a skill
  automatically from the prompt - that is routing, and it waits for the skill
  routing work rather than pretending to guess.
- **Tools view** - *delivered*. `GET /api/tools/status`: the toolsets built in
  this process, UHB connected *or its reason*, Ollama's three states, terminal
  on/off, and MCP `supported: false` with the reason. No green light that means
  "we hope".
- **Uninstall hook** - *delivered* (`uh uninstall`). The archive is written to
  `Desktop/UH_Backup_Final.zip` first; `--purge` deletes `$UH_HOME` only with
  `--yes`, and a failed backup stops the whole operation. The program is a
  package and the data is a folder, and the command says both.
- **Scheduled archive** - *to do*. A weekly copy is one thread; it waits for a
  demand, because a backup nobody asked for is noise and the button is one click.

Constraint for every item above: the archive is written by UH, read by any zip
tool, and restored by UH alone; nothing here needs a network, an account or a
cloud, and `pytest -q` stays green.


## 9. P9 - Distribution and branding (opened 2026-11-13)

**State: in progress.** Metadata delivered and now **live**: `uh-harness 1.3.0` on
PyPI (2026-11-13) carries the new summary ("uh-harness (UH): a local-first,
provider-agnostic agent harness for your desktop"), the ten specific keywords and
the README with the disambiguation section; `pip install uh-harness==1.3.0` in a
clean venv answers `uh 1.3.0` and `uh doctor` exits 0. piwheels had not rebuilt its
page at the time of writing (it still served 1.2.0 and the old summary - its sync
is periodic, and a pure-Python project gives it no wheel to build). What remains:
the shop page, and the same collision hunt on npm/crates/Homebrew.

A name that collides is a bug in the packaging. Measured on 2026-11-13: PyPI
carries **`universal-harness`** (author "AI King", AGPL-3.0, Beta) while this
project publishes **`uh-harness`** (MIT, `license_expression: MIT` on PyPI). Two
different projects; the shared word "harness" is the only thing they have in
common. Nothing to litigate, everything to disambiguate - a shop page or a search
result is where a user decides, not a README footnote.

- **Package metadata** - *delivered*. `description` names the package and the
  promise ("uh-harness (UH): a local-first, provider-agnostic agent harness for
  your desktop"); keywords are the specific ones an installer searches
  (`agent-harness`, `local-first`, `openai-compatible`, `tool-use`,
  `desktop-agent`, `sqlite`, `mit`) rather than the generic pair that collides.
- **README "Name disambiguation"** - *delivered*. States the independence, the
  licence of each project, and what each one is, at the bottom of the page where
  someone who scrolled that far is already comparing.
- **GitHub metadata** - *delivered*: the repository description names
  `uh-harness (UH)` and says "Not affiliated with the PyPI package
  universal-harness"; fourteen topics carry the specifics (`agent-harness`,
  `desktop-agent`, `sqlite`, `mit-license`, `tool-use`, `backup`, …).
- **Shop page** - *to do*. One screenshot per panel (cockpit, memory graph,
  telemetry, data/backup), the one-line promise, the install command, the licence
  badge. The panels are deliverable; the copy must not claim what the roadmap
  marks as "not yet".
- **Wheel contents** - *partly verified*: `tests/test_web.py` asserts that
  `index.html`, `app.js` and `style.css` ship in the package, so the cockpit is
  not an empty page after `pip install`. A full `python -m build` + install in a
  clean venv is *to do* before the next release.
- **Other ecosystems** - *to check before any push*: npm, crates, Homebrew and
  the usual model hubs. A collision found on npm is cheaper to fix before a
  launch than after.

Constraint for every item above: the naming work touches metadata and docs only.
No feature is renamed to look different, and `pytest -q` stays green.


## 10. P10 - The cockpit's window, and MCP (delivered 2026-11-13)

Two candidates were on the table: a native desktop shell (Tauri/Electron) and an
MCP client. Measured before choosing, on 2026-11-13 - both were then built the
same day:

- **MCP was absent, and the UI said so**: `GET /api/tools/status` answered
  `"mcp": {"supported": false, "reason": "UH has no MCP client yet: no MCP server
  can be attached"}`. The dot in the Data panel was honest, and it was the only
  capability on that screen that was not there.
- **A desktop shell is 80 % there without a new toolchain**: `uh install-shortcut`
  already writes a hidden launcher, a `.lnk` and a generated icon. What is missing
  is a window without browser chrome - which Chromium and Edge give with
  `--app=<url>` and nothing else.
- **Tauri is a toolchain, not a feature**: Rust (~1.5 GB), the WebView2 runtime,
  and code signing before Windows stops calling the installer a virus.

**Order followed** (GO given the same day; each step justified by what it adds):

1. **`uh web --app`** - *delivered*. `uh/web/browser.py` finds Edge or Chrome
   (three Windows install roots each, `which` elsewhere, `UH_BROWSER` wins) and
   launches `--app=<tokenised URL>` with `--window-size` and a profile under
   `$UH_HOME/web-app` - a shared profile would forward the URL to a running
   browser and lose the flag. No Chromium is not an error: the console says so
   and a normal tab opens. `install-shortcut` now passes `--app`, so one
   double-click is a window, not a bookmark.
2. **An MCP client** - *delivered* (`uh/mcp.py`, contract in
   [docs/MCP.md](MCP.md)). JSON-RPC 2.0 over stdio
   (`initialize` → `notifications/initialized` → `tools/list` → `tools/call`), one
   process per configured server, reused across turns, stopped at exit. Tools are
   exposed to the agent loop as `mcp__<server>__<tool>` (64-character cap,
   per-server allowlist), results truncated at 200 000 characters with a marker,
   one response line refused above 10 MB, a failed start remembered for 30 s so
   the cockpit's polling never waits on a dead server. Configuration in
   `config.toml` (`[[mcp.servers]]`: command, args, cwd, env, timeout, allow), a
   read-only table: `uh config set` refuses rather than dropping it. Every failure
   is a tool result with a `kind` (`configuration`, `transport`, `timeout`,
   `protocol`, `server`, `unavailable`) - never a crashed turn.
   `GET /api/tools/status` now answers `supported: true`, `configured`, and one
   entry per server with `state` = `connected` / `idle` / `failed(reason)`; the
   Data panel draws a row and a test button per server, and
   `POST /api/mcp/{name}/test` asks the server *now* rather than reading a cache.
3. **A real shell, later and only on demand** - pywebview (system WebView2) before
   Tauri: one dependency, the same UI, a tray icon and a real title bar. An
   installer plus signing belongs to the shop-page milestone (P9), because an
   unsigned `.exe` is a worse first impression than no `.exe`.

**State: steps 1 and 2 delivered on 2026-11-13** (see `tests/test_mcp.py` and
`tests/test_web_app_mode.py`: a real child process that initialises, lists, calls,
hangs, dies mid-call and writes 11 MB on one line). Step 3 stays postponed on
purpose: an unsigned shell is a worse first impression than none.

Still open, measured rather than guessed:

- **transports**: stdio only. HTTP/SSE needs a URL, a token and a threat model UH
  does not have; nothing here pretends otherwise.
- **methods**: `tools` only - `resources`, `prompts` and `sampling` are not
  exposed, because the agent loop has no place to put them yet.
- **approval**: the per-server `allow` list is the permission model. There is no
  per-call prompt; adding one is a P11 question about the UI, not about MCP.
- **discovery**: a server is only known because `config.toml` names it. No
  registry browsing, no suggestion by a model, no auto-install.

## P11 - Self-knowledge: the map, the outputs, and the dream cycle

**Delivered 2026-11-14.** The harness could not answer "where are my dreams?"
without inventing it, and a realisation had nowhere to land.

1. **`uh/system_map.py`** - the map is *generated* from the functions that own the
   paths (`uh_home`, `default_store_path`, `dream_store`, `skills`), so it cannot
   drift from reality the way a hand-written document does. `uh map` prints it and
   `uh map --write` drops `$UH_HOME/SYSTEM_MAP.md`. A compact `brief()` rides on
   the system prompt of every turn: names and paths, no directory scan, and before
   the skill's instructions - the task stays closest to the user message.
2. **An outputs folder** - `outputs_dir` in `config.toml` (default
   `$UH_HOME/outputs`). It is the only place a *realisation* writes, and `fulfil()`
   now delivers its plan there as well as keeping the record in `dreams/actions`.
3. **The dream reads the chats** - `sessions` is a source of the night pass: the
   last 20 session titles and their message counts, never the bodies. The pass runs
   unattended; a transcript is the most private file in the harness.
4. **The cycle, written down** in the map itself: chat -> store -> pass -> store or
   fulfil -> outputs.

Still open:

- **the "Exaucer" button** already exists (`POST /api/dreams/{stamp}/fulfill`); what
  it could not do until now was deliver outside `dreams/actions` - the copy into
  `outputs` is the change.
- **skills compression**: duplicates are visible in the map's `skills` folder, but
  merging two skills is a judgement call. A `uh skills --duplicates` report is the
  next step; auto-merging prose is not.
- **the map in the provider's own words**: today it is a fixed block. A model that
  can *edit* its map is a different trust question.

## P13 - Self-repair and model independence

**Opened 2026-11-14.** Two things a harness needs before it can be handed to
somebody who is not its author: it must be able to look at its own code, and it
must not be married to one model server.

1. **Self-repair, with a brake.** An MCP filesystem server pointed at UH's *own*
   checkout, a read-only allowlist by default, and a rule that a proposed patch is
   shown as a diff before it is written. The brake is the CI: a repair that does
   not pass `ruff`, `mypy` and `pytest` is not a repair, and the pull request is
   the only place a self-written patch may land. "Fix yourself" without that is
   how a harness deletes its own entry point at 03:00.
2. **Hugging Face as a first-class provider.** Local models today means Ollama.
   The next connector is `text-generation-inference` (OpenAI-compatible `/v1`) plus
   a plain `transformers` path for a single checkpoint, so a user with a GPU and a
   HF token needs no extra daemon. The provider interface already fits; what is
   missing is the catalogue entry, the model listing and the doctor line.
3. **No provider is the default story.** The cockpit already runs without Ollama -
   the status bar shows the version, not a vendor - and the next step is to make
   the first run ask *once* which provider to use, then never mention it again.
4. **Self-knowledge stays a map, not a memory.** `uh map` is generated from the
   code; the agent reads it, it does not remember it. A remembered path is a path
   that goes stale silently.

**State (2026-11-14):** step 2 exists - `uh/providers/huggingface.py` speaks
`/v1/chat/completions`, prefers a local TGI on loopback (no token), falls back to
the HF router, and was driven end to end through `uh chat --provider huggingface`
against a TGI-shaped server (blocking and streaming). Step 1 exists as `uh fix` +
a read-only `self-repair` MCP template: the diff must pass `git apply --check`
before it is shown, applies on a `fix/auto-<stamp>` branch, reverts when a
`--check` command fails, and never commits or pushes.

Not done yet: a button in the UI for `uh fix` (the command is CLI-only today, which
breaks the rule that a visible capability is activated from the UI), and the
first-run screen that asks for a provider once (step 3).

Still open after P13 as written:

- an approval step per self-written patch (today: the diff review by hand);
- a sandbox for the self-repair server that cannot see `~/.uh/keys.toml` or
  `mcp-secrets.toml` - a server that can rewrite the code must not be able to read
  the credentials in the same breath.

## Delivered 2026-11-14

What actually shipped (tags `v1.6.0` and `v1.6.1`, wheels on PyPI):

- **Hugging Face without Ollama** - `uh/providers/huggingface.py`, `/v1/chat/completions`,
  local TGI preferred (socket probe, no token), the router behind; the legacy
  `api-inference` task API is never used. Proven end to end, blocking and streaming.
- **`uh fix`** - a patch must start with `diff --git` and pass `git apply --check`;
  `--apply` works on `fix/auto-<stamp>` and reverts when a `--check` command fails.
  No commit, no push, ever.
- **"Réparer UH"** in the cockpit, with a diff modal (`docs/assets/self-repair.png`).
- **Cloud + MCP from the UI** - AWS S3 (`put_object`, 7 tools), Google Drive (read),
  a read-only `self-repair` template, and **any custom stdio server**; secrets in
  `mcp-secrets.toml` (0600), never in `config.toml`.
- **Connexions tab** - cloud, custom stdio, and HTTP/SSE entries in one place, third
  in the strip so it is visible without scrolling, with a "Nouveau" badge until a
  server exists.
- **HTTP/SSE entries recorded, not started** - `POST /api/mcp/http` writes
  `transport = "http"` and the stdio client skips it without a warning.
- **Aide modal + `?panel=<tab>` deep links** - the chat can answer with a link that
  opens the right tab, and the system prompt forbids answering a UI question with
  "run this command".
- **`uh map` / `SYSTEM_MAP.md`** - paths generated from the code that owns them.
- **`docs/INSTALL.md`** - the clean manual upgrade (WinError 32), and `uh upgrade`
  points at it when pip fails.
- Fixes: `data is not defined` on a successful `/api/tools/status` (the whole Data
  panel), `tomllib` on Python 3.10 (1.6.0 wheel unusable there), `uh fix` provider
  resolution, `/__init__.py` in the file list, CRLF patches that do not apply.

## Still open

- **HTTP/SSE transport** for the MCP client - the blocker for Positron, remote
  servers, and any hosted tool server. Everything else in P14 waits on it.
- Per-call tool approval (the `allow` list is a model of permission, not a prompt).
- SVG icons per tab, and the full "verre" theme (only its tokens are used today).
- The first-run screen covers "no provider" but not "no model for the chosen one".
- `uh skills --compress` merges mechanically; it does not rewrite prose.
- A "Tester" button for a cloud upload (the first real send is the test today).

## P14 - Positron and the HTTP/SSE transport

**P14.1 shipped 2026-11-14: `uh/mcp_http.py`.** The transport speaks both shapes
the ecosystem uses - Streamable HTTP (2025-03-26, one endpoint, JSON or SSE body,
`Mcp-Session-Id`) and the legacy HTTP+SSE stream Positron ships (one GET whose
`endpoint` event names the POST target) - with the same failure kinds as the stdio
client. A recorded entry now becomes *connected* from the panel: "Tester la
connexion" runs the handshake and lists the tools, a save runs the same test in
the same gesture, and the states are distinct (configuré / connexion en cours /
connecté (N outils) / jeton refusé / injoignable / protocole incompatible), each
with the sentence that says what to change. A client that drops mid-session is
detected by the watchdog (a `ping`, then 1-2-4…60 s of backoff).

The prerequisite held: **a remote tool never runs on its own**. `build_toolsets`
returns nothing without an approver, and every `tools/call` asks first - the web
cockpit puts the question on the turn's SSE stream (`approval`) with Autoriser /
Refuser; no answer inside 120 s is a refusal. A console turn (no approver) simply
has no remote tools.

Still open: a real Positron on this machine (not installed here - the transport is
proven against a fake server that speaks all three shapes, including a 401, a 302
and a closed stream); the Tauri question is deliberately after P14.1.

Two entries from the security review, traced rather than silently closed:

- **DNS rebinding on a non-literal host.** `uh/urlguard.py` decides "loopback" on
  the literal host and never resolves a name, so a name that resolves to
  `127.0.0.1` is still treated as remote: it needs `https` (certificate checked
  against that name) or the explicit `allow_insecure` checkbox, and every tool
  call from it still needs a click. Resolving at registration time and checking
  the IP would introduce a TOCTOU (the answer at registration is not the answer at
  connection), and pinning the resolved IP at connect time (IP + `Host`/SNI) is
  the only version that would actually hold - it costs a custom connection pool
  and a second name-verification path, for a threat that already requires the
  user to paste a hostile name and then approve every call. Decision: not
  implemented; the residual risk is documented here, and `_check` refuses
  redirects so an approved address cannot become another one after a 302.
- **`transport = "sse"` written by hand.** A hand-written `[[mcp.servers]]` with
  `transport = "sse"` is accepted and uses the legacy one-stream path only; the
  panel offers no button for it because `transport = "http"` already auto-detects
  the shape from the URL (a path ending in `/sse` is the legacy stream). What is
  missing is a UI that can *edit* an existing entry: today a mistake in the URL
  or the transport is corrected in `config.toml`, by hand.

Positron is Posit's data-science IDE built on **Code OSS**. Since **2026.10** it
ships an **experimental MCP server** (setting `ai.mcp.enabled`, command
"Positron MCP: Add to Coding Agent") that lets a third-party agent see the live
data, plots and R/Python consoles of a session.

UH never installs Positron: a user who has it connects to it, a user who does not
is not blocked.

## Tauri vs client MCP - the decision, not the build

**Criteria**, in the order they matter to this product: (1) first launch without a
terminal, a `.ps1` or a port number; (2) a permission model a buyer can read
(files, processes) instead of one inherited from the shell; (3) install weight and
update path; (4) security surface (a desktop app calls the local API without a
browser, so CSRF disappears - but the token and the loopback bind must stay);
(5) maintenance across three OS; (6) what a signed installer actually buys a
person who is being sold a skill, not a runtime.

**Cost.** Tauri means a Rust toolchain, per-platform signing, three installers and
a WebView that differs from the Edge/Chrome used in the proofs. It does **not**
touch the MCP client: that lives in the Python process either way, and P14.1 is
already shipped and tested. Staying in the browser costs nothing new but keeps the
launcher and the port visible on day one.

**Risks.** Tauri adds a layer to debug (WebView quirks, IPC, permissions) and turns
a Python package into a signed product with a release cadence. Not doing it means
the first contact stays a shortcut that opens a window - acceptable for a technical
buyer, weaker for a non-technical one.

**Recommendation (10 lines).** Keep the MCP client + browser cockpit for now:
P14.1 is done, the remaining user-facing gap (Positron, a token) is closed inside
the existing panel. Open Tauri only if a prospect asks for a signed installer or if
file/process access must go through an OS permission prompt; otherwise Rust,
signing, three installers and an update channel buy nothing the current launcher
does not already do. Re-evaluate after one real Positron connection and one
recorded self-repair demo, because that is when "it looks like an app" starts
mattering commercially.

## Ideas, not scheduled

- A **vitrine**: sales page + a GIF of the self-repair loop (recordable through the
  CDP harness already used for the proofs).
- **Multi-agent side by side**: several chats, several models, one window.
- **"Appliquer" that produces a real deliverable**: a dream's plan turned into a
  finished file in `UH Outputs` with a diff shown first.
- **Cost telemetry per turn** (tokens are counted; the price is not).
- **Sandbox for the self-repair server**: it must never be able to read
  `keys.toml` / `mcp-secrets.toml` in the same breath as it reads the source.
- **Hugging Face model catalogue** fetched from the router instead of five constants.
