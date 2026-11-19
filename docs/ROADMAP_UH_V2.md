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
  says which cause is likely. *To do, in that order.* The **voice half of this
  bullet is superseded by `P1-15`** (microphone, sound visual, voice cloning),
  which is the single place that decides what the audio pipeline is; the other
  three helpers stay here.

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

## Delivered 2026-11-17

`uh-harness` **1.7.5** (tag `v1.7.5` → `40f6f34`, on PyPI) and `uhb-harness` **0.2.0**
(tag `v0.2.0` → `17bd062`). **P0-4 is closed on both sides**:

- **the snippet is not cut any more.** The client budget of `execute_code` is the requested
  timeout, bounded by the `default` and `maximum` the catalogue publishes in the tool schema,
  plus a margin — for `execute_code` only. An overrun comes back as a structured `timed_out`,
  never as "UHB unreachable".
- **it asks first.** UH shows the code, the mode and the delay on the turn's stream, and sends
  nothing to `/tools/execute` when the operator refuses or the notifier raises. UHB mints a
  single-use nonce (120 s, in memory, bound to `execute_code`), consumes it even when the run
  fails, and ignores a body claiming `approved: true`.
- **the sandbox's boundary is measured, not claimed.** The environment is scrubbed (no variable
  whose name carries TOKEN / KEY / SECRET); the operator's files stay readable by absolute path,
  and a characterisation test asserts exactly that, so no report can pretend otherwise.
- **the install guide names the real packages.** `mcp` / `mcp-types` (`>=2.12.0`) and
  `google-genai` (`>=2.12.5,<3`) constrain `pydantic`; `mixpanel` constrains nothing and `gradio`
  only through its `mcp` extra. `scripts/diagnose_pydantic.py` measures it, read-only.
- **documentation**: the architecture plate in the README (without a test count — those age), and
  `docs/VALIDATION_MATRIX.md` as the list of proofs required before the fusion.
- Merged in this order: UHB #16 → #17, UH #13 → #15, then UH #14 (matrix), #16 (Pydantic) and
  #18 (plate). Measured on the merged tree: **1513 passed, 2 skipped**, ruff and mypy clean.

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

- **P1-8 (UHB monitoring)** — absent / error / ok, visible in the System panel instead of a
  silent failure. Then **P4-1** (cross validation of the two components, `docs/VALIDATION_MATRIX.md`).
- **P1-12 (transcript notice) — it must never read as data loss.** `reconcileTranscript`
  (`uh/web/static/app.js:1095`) runs right after the turn's `done` (:1379), while the store can
  still be one row behind the worker that writes it, and then prints a red `failed` bubble in the
  middle of the thread — *« Le transcript enregistré contient moins de messages que l'écran :
  rien n'a été effacé. »* — plus a **Recharger la session** button. Measured on this tree:
  `system`/`failed` bubbles are already excluded from the count, so the remaining trigger is the
  writer lag (and any bubble the store does not hold yet). Required: (a) re-read once before
  deciding, and never notice a single-row lag or a locally echoed command; (b) when a real gap
  remains, move it **out of the conversation** — header/status control (*« Actualiser la vue »*)
  with plain French wording and no internal jargon; (c) messages, artifacts and folders
  (`+ Folder`) stay untouched in SQLite: the control refreshes the view, it repairs nothing.
  Proof before closing: a browser capture showing a normal turn raising no red bubble, and a
  forced gap showing the discrete control instead.
  **Delivered 2026-11-18** (`fix/p1-12-transcript-notice`) : fenêtre de lecture qui suit le fil,
  relecture unique avant de conclure, contrôle `#view-stale` dans l'en-tête ; preuves
  `tests/e2e/test_p1_12_transcript_notice.py` (Chromium réel, session de 250 messages) et
  `tests/test_transcript_notice.py`.
- **P1-13 (graphs are drawn, not described).** A request for a chart, a diagram or a map must end
  as an image in the bubble — `execute_code` writing a `.png` published through the `artifacts`
  channel after approval — not as a text tutorial that names a library to install. Two
  prerequisites belong to the lot because they are what makes the outcome measurable: UHB starts
  with the `execute_code` tool available, and its Python environment really carries `matplotlib`.
  Proof before closing: one browser capture of a plain request (*« trace la courbe de … »*)
  showing the `.png` inside the assistant bubble, still there after a reload.
  **Progress 2026-11-18** (`feat/p1-13-charts`) : `matplotlib` + `numpy` installés dans le venv
  UHB (PNG réel de 14 374 o rendu en Agg), instruction de prompt « dessine, ne raconte pas »,
  images d'outil vers `message["artifacts"]` + un unique événement `artifacts`, et rendu navigateur
  avec rechargement (`tests/e2e/test_p1_13_charts.py`). Reste la capture d'un tour avec un vrai
  modèle — voir la ligne P1-13 de `VALIDATION_MATRIX.md`.
- **HTTP/SSE transport** for the MCP client - the blocker for Positron, remote
  servers, and any hosted tool server. Everything else in P14 waits on it.
- Per-call approval for the tools **other than** `execute_code`: that one has a gate
  (nonce, UH #15 / UHB #17); MCP writes and the `desktop_*` tools still rely on the
  gate of their own toolset.
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

## P14.2 - the Connexions UI (storage, MCP, local AI)

**Shipped.** A first launch that never blocks (banner + "Plus tard"), three
separate tabs (`Clé API` / `IA locale` / `Connexions`), "Stockage cloud (dossiers)"
instead of "Connecter un Cloud", and one `+ Connecter un MCP` popup (in
`Connexions`, again in `Données`) that imports `.json` or the documented YAML
subset, probes the candidate for five seconds and keeps `Enregistrer` disabled
until the probe answers `connected`. Secrets stay masked (`••••` + 4) and go to
`mcp-secrets.toml` (owner-only), never to `config.toml`, never to a log. Tests:
`test_mcp_import.py`, `test_web_mcp_probe.py`, `test_web_secrets_dom.py` (mask in
the answer, absent from the access log), `test_web_ux.py` (the popup gating).

Still open:

- [ ] editing an existing server from the UI (today a wrong URL is fixed in
      `config.toml` by hand);
- [ ] YAML beyond the subset: anchors, multi-line blocks, `- key: value` lists;
- [ ] an end-to-end run of the popup in CI (today it is proven by CDP, recorded in
      `docs/UX.md`);
- [ ] the real Positron connection: "Tester la connexion" against an installed
      Positron, the only proof this machine cannot produce;
- [ ] refresh the README's Connexions screenshot, which predates the rename.

## P14.3 - one engine, one choice: the model switch and the tab cleanup

**What the reports showed, and what changed.**

| Reported | Cause | Now |
| --- | --- | --- |
| "je mets ma clé DeepSeek, le chat est plus rapide mais pas plus intelligent" | the header menu only listed **Ollama** models (`data.ollama`), and `/api/model` received a bare name: the provider stayed whatever it was, so an Ollama id could be sent to DeepSeek | `GET /api/models` returns `choices: [{id, provider, local}]` for local **and** every keyed API provider; the menu is grouped, badged 💻/☁️, and sends `{provider, model}`; the header prints the engine that serves the turn |
| "refusé : HTTP 500" on the "Utiliser" buttons | `/api/setup` saved whatever model name it was given, installed or not, and a config write failure was an unhandled 500 | a local model must be installed (`400` with the sentence), a cloud choice gets a published model id, a read-only `config.toml` is a `409` naming the cause |
| "où est l'onglet cloud ?" | it was never a tab: storage lives in `Connexions` ("Stockage cloud (dossiers)"), and the old word "Cloud" suggested a credential | the card says it configures a storage account **or** an MCP server, and that model keys are elsewhere |
| "un onglet ne sert à rien" | `Providers` and `Clé API` were the same subject in two tabs | one tab, **Fournisseurs**; `?panel=providers` still lands there |
| "l'onglet pour évaluer les IA a disparu" | no such tab exists in any commit (`git log -S "Capacités"` is empty) | the evaluation is a table inside **Télémétrie**: per model, turns, average latency, tokens/s and real tool calls, measured from recorded turns |
| "Positron doit se connecter depuis l'UI, pas depuis un PowerShell" | nothing asked for PowerShell except the model's own advice | `+ Connecter un MCP` is the only path, it tests before saving, and the prompt forbids sending a UI question to a config file |
| "l'IA donne du code au lieu d'exécuter" | a small model's default | the prompt states the agent rule (call the tool, do not print code to copy) and turns a "no session" error into "open the CONSOLE, run a cell" |
| "cloner le modèle qui marche" | no duplicate path | `POST /api/providers/{name}/clone` + a `Cloner` button on every provider card: same URL, same keys, new name; the key never leaves `keys.toml` |

Open: a real Positron handshake (needs an installed Positron and an open console);
the exact reason **their** 500 happened (no log was available - the write path is now
guarded, so a repeat shows a sentence instead of a code); a Playwright run in CI.

## Veille & Évolutions (Nov 2026)

Two items read this month, kept here so they are not lost:

- **Evolver** (arXiv:2611.07595) - persistent agentic experience ("Gene"): a way for
  an agent to keep what it learned from its own failures without retraining the
  model. Relevant to UH's `dreams` and memory layers; nothing to build before the
  paper is read end to end. Status: **to read**, no code.
- **LiteLLM v1.100.0** - breaking changes reported around MCP toolsets enforcement
  and the removal of `prompt_token_calculator`. UH talks to providers directly and
  does not depend on LiteLLM, so nothing breaks today; the item is a **watch** for
  anyone routing through a LiteLLM gateway. Verify against their changelog before
  quoting it.

## P14.4 - the polish pass (shipped in 1.7.0)

- [x] one door per subject: `+ Connecter un MCP` only in `Connexions`; `Données` is
      backup, skills, installed tools, repair - and never a connection;
- [x] no "Utiliser" buttons: the row is the action, the same move as the header menu;
- [x] a 500 from Ollama reads "non chargé (VRAM ou délai), réessaie ou prends un
      modèle plus petit", and a model that is not installed is a 400 that says so;
- [x] `Providers` merged into `Fournisseurs`, tabs wrap instead of hiding titles.

## P15 - self-awareness (shipped in 1.7.0)

`SYSTEM_MAP.md` carries an `## Architecture` section (cockpit / backend / ports),
and one condensed line rides in the brief the model reads on every turn. The
prompt also names the doors, so "où est le bouton MCP ?" is answered with the tab
and `/?panel=conn`, never with a file edit.

## P16 - the dev environment (shipped in 1.7.0)

A non-editable `uh-harness` in `.venv` won the import race over `./uh` when the
gate ran `pytest.exe`: the gate could test code that was not being pushed. Fixed by
`pip install -e . --no-deps`, and `tests/test_dev_env.py` fails loudly if `uh` does
not come from this repository.

## P17 - the real Positron test (open)

- [ ] a handshake from the UI against an installed Positron, with a console open;
- [ ] one turn that really calls `mcp__positron__execute_code` and gets a result
      back (today the transport is proven against a simulated server that speaks
      both Streamable HTTP and the legacy SSE stream);
- [ ] a screenshot of the connected state, filed next to
      `docs/assets/p14-connected.png`.

## P18 - 1.7.0 field report (opened 2026-11-15)

Reported after the six `fix(web)`/`fix(agent)` commits (`aaa564c`..`fa4f676`).
Measured before touching code again; the causes are not what the symptoms suggest.
**Delivered 2026-11-15** in `e89e3a3`..`64bd9c1`.

- [x] **404 `/favicon.ico` in the console** — the instance on 8765 served the old
      bundle. Live after the restart: `204` on both icon paths, `reconcileTranscript`
      present in the served `app.js`. The version banner also compares the *bundle*
      (`?v=`) with `/api/status`, which is the state that made this invisible.
- [x] **"the chat loses messages / does not answer"** — same cause (old bundle). The
      append-only transcript is now what 8765 serves.
- [x] **"the chat is too short"** — `max_tokens` default 2048 → 4096, and both
      provider families report `done_reason`/`finish_reason` as one `truncated` flag
      that `run_turn` turns into a visible notice (the flag never enters the history).
      The context budget was measured at 24 000 tokens: never the cause.
- [x] **Positron "fails again"** — `/api/tools/status` now probes and reports
      `connected: true`, protocol `2025-03-26`, server `positron 0.1.74`, 9 tools
      (measured live). The panel lists HTTP servers (it read only the cloud store),
      "Toujours autoriser ce serveur" is a session-scoped trust in the gate, and
      "Tester un appel" runs one read-only tool with its latency.
- [x] **Providers tab empty** — `showPanelTab()` had no `keys` branch: the tab only
      uncovered an empty section. Clicking it now loads the list (5 775 chars,
      seven providers, measured through CDP with a real click).
- [x] **MCP `atelier` "Erreur"** — a path outside the roots returns
      `{kind: outside_root, requested, allowed, error}` naming the config line.
- [x] **Model selection not persisted** — `POST /api/model` writes
      `default_provider`/`default_model`; the flat writer refused the real config
      because it holds `[[mcp.servers]]`, so `write_flat_key_preserving_tables`
      edits the two root lines in place. Live proof: `deepseek/deepseek-flash`
      served after a restart. The browser keeps the same pair in localStorage.
- [x] **UHB survives UH** — the job object and the `finally` were already there;
      closing the browser window stops nothing. `POST /api/system/quit` + a power
      button stop UHB then the server (live: `{"quitting":true,"uhb_stopped":true}`,
      no `uhb.exe` afterwards).
- [x] **Confusing model selector** — two groups ("API (cloud)" then "Local (Ollama)"
      by weight), class badges, provider name on API lines, size on local lines,
      tool-support badge kept; the toggle now reduces to the current model.

## P20 - 1.7.x stabilisation: the lifecycle, not the transcript (opened 2026-11-15)

Every symptom of the P18 round came back one level lower: the token, the cookie, the
port and the end of a stream. What follows are invariants, each with the test that
holds it. A fix that is not one of these five is not part of this round.

1. **A message is never lost before it is accepted.** The field is emptied only on
   the server's `open` frame; a message sent during a turn is refused with a visible
   sentence and keeps its text; a silent stream ends after 45 s (`UH_STALL_MS`) with
   a bubble, the draft restored, and the composer free.
   Tests: `tests/e2e/test_ux_invariants.py::test_e1_*`, `tests/test_ui_lifecycle.py`.
2. **No UI code erases the document.** A 401, a failed boot or a network error lands
   in `#app-banner`; `document.body.textContent` is forbidden and a source test
   refuses it. Tests: `test_e2_*` (fails with "#input count 0" before), `test_no_ui_code_erases_the_document`.
3. **One token per home, one cookie per port.** `$UH_HOME/web-token` (0600) survives
   a restart, `--rotate-token` replaces it on purpose, and the cookie is
   `uh_token_<port>` so two instances never overwrite each other's slot.
   Tests: `test_e3_*`, `test_the_token_outlives_a_restart_and_the_cookie_is_per_port`.
4. **One instance per port.** `/healthz` (unauthenticated, `{app, version}`) lets a
   second `uh web` recognise the first, say where it is, and exit 0; the silent move
   to the next port is opt-in (`--port-fallback`). Tests: `test_e5_*`,
   `test_one_instance_per_port`.
5. **Every stream ends.** `ping` every 15 s, `TURN_CAP_S` 10 min, a `done` frame even
   when the client leaves; on the server side the worker always emits `__end__`.
   Tests: `test_a_chat_stream_always_has_a_life_sign_and_an_end`, `test_e1_*`.

The e2e suite is excluded from the default run (`-m "not e2e"`) because it needs a
Chromium and ~20 s per case; it is the one that catches "the product breaks while the
unit tests stay green", so it must be run before a tag:
`pytest -o addopts="" -m e2e`.

UHB: a launched child is recorded in `$UH_HOME/uhb.pid`, and "Quitter UH" stops a
recorded UHB even when another process started it - the command line is checked
before the kill. Test: `test_an_orphan_uhb_can_be_stopped_by_its_pid_file`.

## P19 - hermes-webui mechanisms (deferred, decided 2026-11-15)

Not before 1.7.0 is stable. Four mechanisms from `nesquena/hermes-webui` are worth
porting, none of them blocking a demo: `data-tid` on tool cards so a result finds
its card however the events interleave; a "a response was in progress when you
left" banner backed by server-side turn state; model + Stop in the composer footer;
the `aria` tree contract from `static/a11y-helpers.js`. Reading only - no vendoring
of a 123 000-line front end into a 5 300-line one.

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

## P3 - Functional evolutions (opened 2026-11-18)

### P3-15 Intelligent orchestration of tools and Skills — IN PROGRESS (worktree)

**Why this exists.** Today a turn is offered *every* tool of *every* toolset
(`uh/agent.py:100 _tool_definitions` walks the whole catalog, no mode filter, no
intent filter). The operator's requirement is the opposite: a plain question reaches
the model with no tools, and a chart request reaches it with the data and execution
tools — **without the user typing a `/command`**. Commands stay as a manual override,
never as the way to discover a capability.

**Measured starting point (2026-11-18, `958e13c`).** `build_toolsets()` → `file`,
`mcp:atelier`, `memory`, `terminal`, `web_search` (+ `uhb` only when
`UH_UHB_EXECUTE=1`); no intent layer exists, `uh/decision.py` is the decision
*journal*; a skill is attached manually, one per turn (`uh/web/app.py:765`).

**Contract of the lot**
 (a) *Intent* → capability families from the request, its keywords, the recent turns
 and the real catalog — never a fixed keyword table alone;
 (b) *Skills* → proposed from what they say they do (name + title weigh more than the
 description), **at most one**, and *no skill* when nothing is convincing — a wrong
 skill injects a wrong protocol;
 (c) *Catalog* → `request.tools` built from the selection only, capped (3 toolsets);
 no toolset or skill name can be returned unless it exists in the catalog (the
 `mcp__positron__execute_code` class of failure becomes impossible);
 (d) *Execution* stays UHB's: the model's decision is never an authorisation;
 (e) *UX* → one plain-French line ("Analyse des données et génération du
 graphique…"), no internal tool names;
 (f) *Adding a skill never breaks UH*: a malformed skill file is skipped, one skill
 cannot shadow a tool or a security rule;
 (g) ambiguous short request → **ask**, never pick arbitrarily.

**Already prepared in the worktree** (`feat/intent-orchestration`, not pushed): the
router `uh/intent.py` and `tests/test_intent.py` (**12 passed**), with a real-catalog
smoke test that caught two defects the unit doubles had hidden — the real catalog
holds Toolset *objects* (`'FileToolset' object is not iterable`), and naive lexical
skill matching proposed `blender-physics-mantaflow-sim` for "recherche les dernières
informations" (shared word: "rendu"). Both fixed; thresholds now measured.

**Wired behind `UH_INTENT_ROUTING=1` (off by default).** The pass-through is
`ChatSession._intent_toolsets()` on `uh/console.py` — the single place where
`self.toolsets` reaches `run_turn`. Off, the catalogue is passed **unchanged**; on, the
turn is offered the selection only (capped at 3), an empty selection for small talk
means **no tool at all**, and a router exception falls back to the unchanged
catalogue, never a wider one. The decision journal gains an `intent` block
(capabilities and reason only — no argument, no message body, no secret). Proposing
is not executing: the toolset object is handed over untouched, UHB keeps the
authority, and no code path here calls a tool.

**State ladder (kept explicit on purpose):** routeur préparé ✔ · routeur câblé ✔
(sous drapeau) · drapeau **désactivé** dans l'installation de l'opérateur ✘ (volontaire) ·
validation complète ✘ (pas de tour réel multi-fournisseurs) · déploiement effectif ✘.

**Tests:** `tests/test_intent.py` (12) + `tests/test_intent_wiring.py` (12), suite
complète **1574 passed / 2 skipped** drapeau **désactivé** (`test_line_endings`
a attrapé mes fichiers en CRLF, corrigés en LF). `ruff` et `mypy uh` propres (70
fichiers). **Reste à prouver** : un tour réel avec un vrai fournisseur (T10 bout en
bout) et un refus UHB constaté sur un outil sélectionné mais non autorisé (T7 réel).
No push, no PR, no merge.

### Status sync of the work already on `main` (verified 2026-11-18, `958e13c`)

| Item | State | Evidence |
|---|---|---|
| `execute_code` chain (snippet → artefacts → bubble) | done, verified | `tests/e2e/test_p1_13_charts.py` (2 Chromium cases), PR #25 |
| operator approval (nonce, single use) | done, verified | `uh/toolsets/uhb.py:612-625`, refusal tested |
| UHB independent validation of the mode | done, by design | `/tools/execute` re-checks the mode; UH cannot grant it |
| image handling (bytes to UI, text to model) | done, verified | `request_messages` (providers/__init__.py:139-170), `tests/test_tool_images.py` (10) |
| conversation resilience (transcript notice) | done, verified | P1-12, PR #23, `tests/test_transcript_notice.py` (7) + e2e |
| character & role per project | done, verified | P3-14, PR #27, `tests/test_roles.py` (9) + 3 e2e |
| tool-call serialisation per provider | **implemented but not validated** | audit 2026-11-18: `type` missing, `arguments` shape depends on stream mode → DeepSeek 422 / Ollama 400 reproduced |
| intent orchestration (this lot) | in progress (worktree) | 12 unit tests, wiring pending |



Lots that change what the product *is* for its operator, without touching the transport,
the fusion or the validation programme. They queue behind P1/P2 and never before P4-1.

- **P3-14 Character & role per project, independent from the skills.** The operator wants
  the assistant to answer as a senior software architect on one project and as a concise
  product writer on another, without touching a single skill or toolset.
  (a) A dedicated **« Caractère / Rôle »** tab in the `uh web` configuration panel, where the
  character, tone and business speciality are chosen from presets or written by hand
  (senior architect, full-stack developer, data analyst, concise writer…).
  (b) **Total independence from the skills**: the role steers posture, expertise and tone. It
  never enables, disables or edits a skill, a toolset or an MCP server — skills keep saying
  *how* a task is executed, the role says *who* answers and in what voice.
  (c) **Per project / per session switching**: the role changes on the fly from the tab or the
  session header, is stored with the session in SQLite (`~/.uh/uh.db`) and reloaded cleanly
  when the session is reopened.
  Proof before closing: a browser capture of the tab, of a session opened with a role, of the
  role switched mid-session and still there after a reload; and a test asserting that the
  active toolsets and the skills list are **byte-identical before and after** a role switch
  (that is what "independent" means, and it is the only way to prove it).
  **Delivered 2026-11-18** (`feat/p3-14-character-role`) : 7 presets (dont Domotique / Home
  Assistant et Vision / vidéo IA) + texte libre dans `uh/roles.py`, onglet **Caractère** et
  sélecteur d'en-tête, persistance dans `sessions.metadata["role"]`, injection dans
  `system_message()` avant le bloc de skill. Preuves : `tests/test_roles.py` (9 cas, dont
  l'indépendance octet pour octet des skills, de `config.toml` et des schémas d'outils) et
  `tests/e2e/test_p3_14_role.py` (3 cas Chromium : choix, rechargement, changement en cours de
  session, texte libre conservé).

### P3-16 Cockpit Projects, Run Lifecycle & Extensions - TO OPEN (requested 2026-11-19)

*Numbering note, written down so the trail stays readable:* the operator asked for this lot
as « P3-14 », but **P3-14 is taken** (caractere & role per project, delivered 2026-11-18,
PR #27) and **P3-15 is in progress** (intent orchestration). The lot therefore carries
**P3-16** in this document while the branches keep the requested names
(`feat/p3-14-runs-lifecycle` on UHB, `feat/p3-14-cockpit-projects` on UH); renaming is a
two-command change if the operator prefers the opposite.

**Measured starting point (2026-11-19, `a37e039` UH / `aa286dd` UHB).** `~/.uhb/runs` holds
**325 run directories, 5.2 MB**, the oldest from 16/11; each one is
`run-<stamp>-<hex>/` with `code.py` and `_uhb_runner.py` plus whatever the snippet wrote.
`uhb/tools/execute.py:233 runs_root()` reads `$UHB_RUNS_DIR` or `~/.uhb/runs` and **nothing
anywhere deletes them**: a grep for `rmtree|clean|purge|ttl|keep` over `uhb/` returns **0
line**. Runs accumulate by construction, not by choice.

**Division of power, restated because these four sub-lots are the easiest place to break it:**
UHB owns the **policy and the lifecycle** (purge, keep, compression, archive, cron scheduling);
UH only **shows and drives** (panels, buttons, statuses). A retention rule written in UH would
be a defect, even if it worked.

#### P3-16a (UHB) - run lifecycle and the « Cerveaux » archive

- **Default: nothing piles up.** At the end of a turn whose run was not asked to be kept, the
  run directory is **deleted**; a TTL/quota safety net (e.g. 200 directories or 500 MB,
  whichever comes first) covers the runs of a crashed or killed process.
- **`keep=true`: compressed, then archived in a dedicated Git repository** tied to
  intelligence, brain personalisation and the Second Brain - a bare repo
  (`~/.uhb/brain-runs.git`) fed one commit per kept run, with a manifest (session id, mode,
  summary, files produced, SHA-256) so `uhb brain`, the wiki and the RAG can index it without
  unpacking anything.
- **A run stays restorable**: `git show` gives back the exact tree, and the manifest names
  what the run produced.
- Proof before closing: after a non-kept run the directory count is unchanged (0 new); after
  a kept run `git -C ~/.uhb/brain-runs.git log --oneline` shows one new commit whose `--stat`
  matches the run's files; 50 consecutive runs leave a stable count; a killed run leaves no
  half-written directory (or is swept on the next start).

#### P3-16b (UH) - projects and Git in the cockpit

- A **« Projets »** view: active projects with their path, current branch, a compact
  `git status` (clean / modified / ahead / behind), the last commit, a one-click workspace
  switch, and the runs attached to the project.
- Read-only by default; a write action (checkout, commit) is an explicit, confirmed gesture.
- Proof before closing: switching project without restarting the server, and the status shown
  by the panel **byte-identical** to `git status --porcelain` run in a terminal.

#### P3-16c (UH + UHB) - crons, files, git, infra

- **Crons**: list of scheduled tasks (`uh dream`, run purge, RAG reindex), next run, last run,
  logs, and manual trigger.
- **Files**: browse the active workspace (read-only by default, size and date visible).
- **Git**: recent history, branches, diff summary.
- **Infra**: consolidated health of UHB :8101, Ollama :11434, RAG :8200, SiYuan :6806,
  MARI :55553 and Docker - each line says *reachable* or *unreachable*, and an unreachable
  service never blocks the panel.
- Proof before closing: one cron triggered by hand from the UI with its output shown; one
  service stopped on purpose and reported as unreachable without a blank panel.

#### P3-16d (UH + UHB) - extensions manager and Gate D

- One panel for **extensions**: MCP servers, skills, the UHB tool catalogue - activation,
  hot reload, a test call, and a diagnostic that says *why* an extension is refused.
- Then the **Gate D** campaign: the automated checks and end-to-end tests added by P3-16,
  run together, before P4-1.

## P4 - Target: one product, packaged (opened 2026-11-16, restated 2026-11-17)

The end state is **not** two repositories on GitHub. It is one product the operator can
hand to someone: one window, one backend inside it, one downloadable installer. Nothing in
P4 is coded before the pair is proven to work together, because a fusion ships whatever
defects the couple still has.

**Where the pair stands (2026-11-17)**: the P0 block is closed and published — `uh-harness`
1.7.5 and `uhb-harness` 0.2.0 carry the transport budget, the approval gate and the measured
sandbox boundary (see *Delivered 2026-11-17*). The next open step is **P1-8 (UHB monitoring)**,
then **P4-1** proper.

The list of proofs required before the fusion is **`docs/VALIDATION_MATRIX.md`** (P0/P1/P2
rows, each with its state and the evidence that is still missing). A fix announced is not a
validated feature: a row only reaches DONE with a dated capture or a test in the repo.

- **P4-1 Complete functional validation of UH and UHB together.** Mandatory criteria, each
  tested **from both sides** when it crosses the two projects: LLM providers and API keys
  (add, store, resolve, list models, test connection, real conversation, 401/403/429/500,
  timeout, cut, recovery); standard chat and long conversations; **OmniRoute** (model
  visible in the UI, model selected, real turn, errors, and the same path through UHB);
  **MCP** (discovery, connection, tools listed, calls from the chat, arguments, results,
  parallel calls, permission refusals, errors, timeouts, disconnect/reconnect - the model
  must never bypass UHB's permissions); cloud connections and remote storage (real
  inventory, auth, read, authorised write, expiry, reconnect, network errors); files and
  images (sent, received, displayed, still there after a reload); execution of UHB tools
  from the UH chat; permission handling and refusals; interrupted-connection recovery;
  timeouts and error messages; service start and stop; persistence of conversations and
  results; a CLI usable without the interface; an interface understandable by a
  non-developer.
- **P4-2 Fusion of the product.** Condition: P4-1 entirely validated. Merge UH and UHB into
  one coherent architecture, remove duplicated responsibilities, remove the unnecessary
  processes and consoles, unify start and stop, keep existing features and permissions,
  lose no user data, and keep a clear internal separation between interface and engine even
  when shipped as one application.
- **P4-3 Repository `UHD-Universal-Harness-Desktop`.** Created only once P4-2 is validated.
  It receives the assembled product meant for end users; the current repositories are not
  moved before that.
- **P4-4 Packaging and distribution.** Compressed artefact; Windows, macOS and Linux
  installers; dependencies included; clean install and uninstall; update handling;
  executable signing where possible; user data preserved across updates; clear install
  documentation. The product must be downloadable and installable without asking the user
  to handle two Python repositories or two consoles. Licences and the weights used by the
  media features must be settled before publishing anything that embeds them.

**Ordering constraint, restated because it is the whole point of P4-1:** first make the two
consoles work and perform together, then fuse. A fusion before that only moves the bugs into
one binary. Nothing in P4-2/P4-3/P4-4 starts before `VALIDATION_MATRIX.md` has no OPEN or
NOT STARTED row left for P0/P1/P2.

## P1-11 → P1-15 — images du chat, rétention, feedback, multimodal, audio (ouvert 2026-11-18)

Déclencheur : T10 (premier tour web avec un vrai modèle, `qwen3:8b`) a produit des images
dans `~/.uhb/runs` — elles s'affichent, mais l'expérience s'arrête là. Les quatre lots
ci-dessous sont **inscrits, non codés**. Chaque « état mesuré » a été lu dans le code ou
constaté sur disque le 18/11, pas supposé.

### P1-11 — les images des outils, dans le chat

État mesuré :
- `uh/console.py:644-657` collecte les `images` renvoyées par `execute_code` en `artifacts`
  (`{mime, bytes, data_url, note}`) et émet un événement `artifacts`.
- `uh/web/static/app.js:241-257` (`drawArtifacts`) rend ces `data_url` dans un `<img>`,
  filtré par `ARTIFACT_MIME`. L'affichage en direct existe donc déjà.
- Le contenu du message outil est persisté **avec** son `data_url` (vérifié dans `uh.db`),
  mais la colonne `artifacts` du store est vide : rien ne garantit qu'un rechargement
  reconstruise les figures.
- Le texte de l'assistant cite encore le chemin du fichier.

À faire : reconstruire les figures au rechargement depuis le contenu persisté ; boutons
`Garder` / `Poubelle` à droite de la figure ; pouce haut / bas sous chaque figure ;
légende lisible (nom de fichier + stdout utile) au lieu du chemin brut.

Preuve attendue : après F5, les images sont toujours là ; `Garder` les retire de la
rotation, `Poubelle` les déplace (voir P1-12) ; un vote crée une ligne datée.

### P1-12 — rétention et mémoire morte

État mesuré : 140 runs au 18/11, la baseline T10 en comptait 134. Chaque run contient
`code.py`, `_uhb_runner.py`, les images, et un cache `.mpl/fontlist-v3.11.0.json` de
~84 ko **dupliqué par run** — le cache matplotlib est recopié au lieu d'être partagé.

À faire : politique de rétention (âge et volume, par exemple 7 jours ou 2 Go, au premier
des deux) ; le cache `.mpl` mutualisé hors des runs ; corbeille réversible avec vidage
explicite ; vérifier qu'aucun processus enfant (python du snippet, ffmpeg) ne survit.

Preuve attendue : poids de `~/.uhb/runs` avant/après, un run ancien disparaît, un fichier
mis à la corbeille sort du disque, `tasklist` propre.

**Temporaires de l'interface (ajouté le 19/11)** : toute image réduite pour l'envoi, tout
cache d'upload et tout état orphelin est purgé après le tour — sauf ce qui est marqué
« Garder » (P1-13). L'interface doit pouvoir se **réparer seule** : une session rechargée
ne garde ni lecteur vide, ni carte fantôme, ni compteur faux, et un fichier dont la ligne
a disparu n'est pas laissé sur le disque. Mesure : poids de `~/.uh/uploads` et du dossier
temporaire avant/après un lot de captures 4K, et zéro orphelin dans `messages`.

### P1-13 — boucle de feedback vers le second cerveau

État mesuré : le toolset `uhb` expose `mcp:atelier` (second cerveau) ; n8n est un serveur
MCP distant à OAuth, non branché. Aucun vote n'existe aujourd'hui dans l'UI.

À faire : `POST /api/artifacts/{id}/rate` (haut/bas + commentaire optionnel), journal local
`jsonl` (source de vérité, hors ligne), puis envoi best-effort vers le second cerveau —
un échec réseau ne doit jamais perdre le vote ni bloquer le tour. Un pouce haut propose une
consolidation de skill ; un pouce bas consigne un échec à ne pas reproduire.

Preuve attendue : un vote écrit une ligne, l'envoi est constaté côté destinataire, et la
coupure du destinataire laisse la ligne intacte.

**Mémoire des réussites, compressée et réutilisable (ajouté le 19/11)** — la demande de
l'opérateur : « les modèles réduits ou exemples jugés bons doivent être dans la mémoire,
pour ne pas avoir à les recharger — un clone de voix réussi, un texte ou une image à garder ».

- tout élément marqué **Garder** ou **Utile** (clone de voix, image validée, réponse
  modèle, exemple de prompt) entre dans une mémoire **réutilisable** : on le réemploie
  sans le régénérer, sans rappeler le fournisseur, sans repayer ;
- **compression maximale sans perte de l'essentiel** : images conservées en **PNG ou
  WebP lossless** (restituables en 4K), audio en **FLAC 24 bits stéréo**. L'objectif n'est
  pas de garder des bruts de 40 Mo, c'est de pouvoir « redévelopper » en 4K et en
  24 bits stéréo au moment de réutiliser ;
- chaque entrée porte son **SHA-256**, son format canonique et la version de son rendu :
  « réutiliser » doit rendre exactement ce qui a été jugé bon, pas une approximation ;
- l'entrée est liée à son message et à son artefact (`artifacts`), pour que la Corbeille
  et la rétention (P1-12) les traitent ensemble — une réussite conservée ne devient pas
  un orphelin ;
- **preuve attendue** : un clone de voix marqué Garder se réutilise en un tour sans appel
  au fournisseur (compteur de requêtes à zéro), l'image conservée revient en 4K, le FLAC
  restitué est en 24 bits stéréo, et le dossier temporaire est vide après le tour.

### P1-14 — pièces jointes multimodales

**État au 19/11 (première moitié livrée, `54f9458`).** Bouton « + » unique et menu
groupé (Images / Vidéo / Son / Documents / Dictée), glisser-déposer et collage
conservés ; `uh/media/` réduit une 4K en copie `-sent.jpg` sous 1 Mo (original
intact) et extrait images clés + bande son d'un `.mp4` ; `uh/capabilities.py` mesure
la capacité auprès du fournisseur (Ollama `/api/show`, `input_modalities` du
gateway, `capabilities.vision` chez Mistral) — inconnu = « non vérifiée » et traité
comme aveugle ; `#media-warning` dit ce qui sera perdu et propose un modèle qui lit
l'image en un clic ; des fichiers sans un mot deviennent une question normale
(`attachments.AUTO_ANALYSIS`). Mesuré : 4K de 19 110 577 o → copie envoyée 536 531 o,
`turn_media images=1` au journal, et une réponse qui relit le texte de l'image
(`qwen3.5:9b`, 35,8 s). Restent ouverts : purge automatique après tour branchée sur
l'interface (la fonction existe, l'appel n'est pas encore posé), multi-images
au-delà de 12 (au-delà, échantillonnage annoncé), et les combos vision d'OmniRouter
qui répondent 404 aujourd'hui (contournés : les modèles locaux passent en tête).

**P1-14 clos, P1-13 et P1-12 livrés le 19/11 (`a8d0dfe`).** Purge automatique après
chaque tour (les copies `-sent` disparaissent, sauf celles dont l'original est marqué
« Garder »), lots jusqu'à 12 images puis échantillonnage annoncé, et **les octets ne
partent plus vers un modèle mesuré aveugle** (Ollama répondait 400). Mémoire des
réussites (`uh/memory.py`) : image conservée en **PNG lossless à sa résolution**
(2 000 × 1 125 → 5,71 Mo, là où la copie envoyée pesait 900 ko), audio en **FLAC
24 bits / 48 kHz / 2 canaux** vérifié par ffprobe, index SHA-256 lié au message,
oublier à la vidange de la corbeille. Auto-nettoyage (`uh/maintenance.py`) : purge
des temporaires et rapport d'orphelins **dans les deux sens**, jamais de suppression
silencieuse. `moondream:latest` retiré (1,7 Go rendus, il ne produisait que des
boîtes).

État mesuré :
- `POST /api/sessions/{session_id}/upload` existe (`uh/web/app.py:696`), plafond 20 Mo
  (`uh/attachments.py:36`).
- `uh/attachments.py` inline déjà le **PDF** (texte extrait) et le **texte**.
- Les **images** (png/jpg/webp/gif/bmp) et les **vidéos** (mp4/mov/webm/mkv/avi) sont
  stockées puis marquées `referenced`, avec la note « the current providers read text only :
  the file is stored and its path is below, but the model cannot see it ».
- **Aucun format audio** n'est reconnu (`mp3`, `wav`, `m4a` : rien dans `attachments.py`).
- Venv UH : `PIL` et `faster_whisper` présents ; `pypdf`, `fitz`, `cv2`, `moviepy` absents ;
  `ffmpeg`/`ffprobe` disponibles globalement (chocolatey). `matplotlib` vit dans le venv UHB,
  pas dans celui de UH — c'est UHB qui dessine.

À faire, dans cet ordre de coût croissant :
1. audio : `faster_whisper` est déjà là (GPU 8 Go, repli cpu/int8 connu) → transcrire en
   texte avant l'envoi au modèle ;
2. vidéo : `ffprobe` pour la durée, `ffmpeg` pour 3 à 5 frames, puis le chemin image ;
3. image : nécessite un modèle **vision** — `qwen3:8b` ne l'est pas ; sans lui, l'image reste
   `referenced` et la note actuelle est correcte ;
4. PDF : vérifier le fallback réellement utilisé puisque `pypdf` est absent du venv.

Contrainte : chacune de ces briques ajoute une dépendance lourde (poids whisper, décodage
vidéo). Faisabilité et coût à mesurer **avant** d'écrire la première ligne.

**Vision multi-images et captures 4K — inscrit le 19/11, non codé** (la demande était déjà
dans cette feuille, rien n'en avait été fait ; c'est écrit ici pour qu'on ne la redécouvre
pas une troisième fois) :

- une **ou plusieurs** images par tour : glisser-déposer, bouton « + », et **collage
  presse-papiers** (Ctrl+V) — jusqu'aux captures d'écran 4K ;
- **analyse automatique sans prompt** : images déposées + Envoi, sans une ligne de texte,
  déclenche l'analyse visuelle de **chaque** image (description, extraction, diagnostic) ;
  l'opérateur n'a pas à écrire « analyse cette image » pour être obéi ;
- **réduction à la volée** avant l'envoi au modèle vision (une capture 4K ne part pas à
  8 Mo : ré-encodage borné, budget de tokens explicite), puis **suppression automatique**
  des temporaires une fois le tour lu — **sauf** « Garder », qui entre dans la mémoire
  compressée de P1-13 ;
- **prérequis mesuré le 19/11** : aucun des cinq modèles installés sous Ollama n'est un
  modèle vision (`qwen2.5:7b`, `qwen2.5:0.5b`, `qwen3:8b`, `qwen3.5:9b`, `lfm3:latest`).
  Il faut donc soit un modèle local vision (~5-6 Go, tient sur 8 Go de VRAM), soit un
  fournisseur cloud qui en expose un — et **le choix se mesure** (coût, latence, qualité
  de lecture d'un texte 4K) avant la première ligne. Sans modèle vision, le comportement
  honnête est un refus explicite, pas un envoi silencieux d'une image que personne ne lit ;
- **preuve attendue** : 3 captures 4K déposées sans un mot → 3 analyses distinctes, chaque
  image envoyée sous 1 Mo, dossier des temporaires vide après le tour, et l'image gardée
  restituable en 4K.

**Bouton « + » unifié — la barre ne devient pas une rangée d'icônes (ajouté le 19/11)** :
un seul bouton ouvre un popup compact, comme sur les chats modernes, et chaque nature de
fichier y est rangée :

- **Son** : `.mp3`, `.wav`, FLAC/OGG s'ils sont présents, plus la **dictée micro** (P1-15) ;
- **Images** : `.png`, `.jpg/.jpeg`, `.gif`, `.svg`, `.heic` — une ou plusieurs, jusqu'à la 4K ;
- **Vidéo** : `.mp4` en **H.264 et H.265/HEVC** — extraction d'images clés et de la bande
  son pour l'analyse, puis purge du temporaire ;
- **Documents & archives** : `.pdf`, `.csv`, `.html`, `.zip`, et les formats texte/code
  déjà gérés par UH — nommés explicitement : `.txt`, `.md`, `.json`.

Glisser-déposer et collage presse-papiers restent des entrées de plein droit : le « + »
n'est pas le seul chemin, c'est celui qui n'encombre pas.

**Détection de capacité et avertissement non bloquant (ajouté le 19/11)** : avant d'envoyer
une image, un lot 4K, un audio ou un `.mp4` à un modèle qui ne peut pas les lire (petit
modèle local texte seul, contexte trop court), l'interface affiche un **avertissement
informatif et non bloquant** — on peut passer outre — et propose **en un clic** un modèle
compatible et assez puissant parmi les fournisseurs **déjà configurés** (local ou API).

- la capacité vient d'une **table mesurée** (`modality: text|vision|audio`, `context_tokens`),
  jamais d'une déduction sur le nom du modèle ;
- **inconnu n'est pas capable** : un modèle dont la capacité n'est pas connue est annoncé
  « capacité non vérifiée » et traité comme tel ;
- l'avertissement dit **ce qui sera perdu** (« ce modèle ne verra pas l'image : il recevra
  son nom de fichier »), pas seulement « non supporté » ;
- le passage en un clic ne change jamais le modèle en silence : l'en-tête et le titre
  affichent le modèle qui a **réellement** répondu (exigence identique au point 5).

Preuve attendue : une capture 4K déposée avec un modèle texte seul → avertissement, un clic
sur le modèle vision proposé → analyse réelle dans le même tour, en-tête cohérent.

### P1-15 — audio, micro, visuel son et clonage de voix (Voxtral) — inscrit, non implémenté

Demandé par l'opérateur le 19/11. **Rien n'est codé** : ce lot s'ouvre après la clôture
des points 2 à 5 de `docs/HANDOFF_POINT0.md` **et** un « go » explicite.

**État au 19/11 — première moitié livrée.** Le paquet `uh/audio/` existe (chaîne
Voxtral → XTTS-v2 → Piper → refus qui nomme chaque tentative), les quatre routes
`/api/audio/*` sont en place, la dictée passe par faster-whisper local, et l'interface a
le micro, le lecteur **conditionnel** avec forme d'onde réelle, la barre d'actions
unifiée et les deux pouces généralisés aux réponses texte. Mesures : `speak` →
`engine=xtts` (Voxtral répond **429** sur cette clé, TTS et STT), `transcribe` →
`engine=whisper` en 1,7 s, clip de 173 644 o servi en `audio/wav`, `keep/trash/restore`
200 avec le fichier qui part et revient, et un tour texte qui émet toujours
`open, token, answer, answer_meta, done` (aucune frame audio). Restent ouverts :
provisionnement XTTS/Piper par l'installateur, voix Piper absente (donc 3ᵉ moteur non
prouvé), et aucune synthèse Voxtral réussie observée sur ce compte.

**Périmètre fonctionnel**
1. un **bouton micro** dans la zone de saisie du chat : dictée → texte, qui entre dans le
   fil comme un message utilisateur ordinaire ;
2. un **lecteur audio avec visuel de son** (forme d'onde) dans les bulles de réponse ;
3. **synthèse vocale et clonage de voix** via Voxtral (Mistral).

**Référence technique — à inspecter en lecture seule avant la première ligne** :
`C:/Users/searc/hermes-home-vision`. Ce qui y est réellement écrit au 19/11 :
`profiles/watch/config.yaml` porte `model: voxtral-mini-tts-2603` (synthèse) et
`model: voxtral-mini-latest` (audio → texte), valeurs reprises dans
`docs/snapshot/config.yaml` ; la constante `MISTRAL_API_KEY` et le mode d'appel sont
documentés dans `skills/autonomous-ai-agents/hermes-agent/references/hermes-agent-body/
10-voice-transcription.md` ; deux chaînes existent déjà (`docs/siyuan/audit_voix.md`,
`docs/pipeline/pipeline_video.py`, `docs/lexique/preparer_script_tts.py`).

État des faits au 19/11, à ne pas confondre avec ce que porte le config de
`hermes-home-vision` :
- **les identifiants Voxtral 3 existent et sont documentés** —
  `voxtral-mini-tts-3-0` (synthèse), `voxtral-small-transcribe-3-0` /
  `voxtral-mini-transcribe-3-0` / `…-realtime-3-0` (transcription), preview publique
  depuis le 20/10/2026, TTS facturé 13 $/1M caractères et transcription 0,0035 à
  0,008 $/min (skill `mlops/tts-voice-cloning`, `references/voxtral-3-api.md`).
  Le config d'`hermes-home-vision` porte encore la génération **précédente**
  (`voxtral-mini-tts-2603`, `voxtral-mini-latest`) : l'implémentation lit le
  catalogue Mistral, elle ne recopie pas ce fichier.
- **le clonage est prouvé, et il ne demande aucun inventaire de voix** :
  `POST /v1/audio/speech` avec `model="voxtral-mini-tts-3-0"` + `ref_audio` (base64
  d'un WAV/MP3 de 2-3 s) clone la voix pour cette requête (mesuré : 1,4 s d'API pour
  5,76 s d'audio, sortie **RIFF WAV brute**). Enregistrer une voix réutilisable
  (`POST /v1/audio/voices`) est en revanche **fermé sur un compte gratuit** : HTTP
  403 « Custom voices require an active paid plan ». Conséquence : `ref_audio`
  d'abord, `voice_id` seulement si le compte est payant — et jamais une voix preset à
  la place de la voix de l'opérateur (un test d'écoute avec une autre voix est un faux
  résultat).
- **la voix de référence est déjà validée** : `ref_ma_voix12_prompt.wav` (18,00 s,
  24 kHz mono) et, pour le projet courant, `ref_ma_voix_v10_prompt.wav` (20,00 s,
  19,25 % de silence interne) avec ses ancres tenues à l'écart.

**Chaîne de voix, dans cet ordre — et elle se dégrade, jamais ne casse**
1. **Voxtral 3 (Mistral)** si `MISTRAL_API_KEY` est présente : meilleure qualité,
   clonage par `ref_audio`, aucune dépendance locale. C'est un service commercial —
   donc le seul chemin compatible avec une chaîne monétisée.
2. **Repli local XTTS-v2** si la clé manque, si le compte est coupé ou si l'appel
   échoue (401/403/429/timeout) : `coqui-tts` dans un venv dédié
   (`~/.uhb/xtts` sur le modèle de `~/.uhb/atelier`), torch épinglé, référence
   10-30 s, `temperature=0.75 / speed=1.0`, 24 kHz mono. Mesures connues sur cette
   machine : RTF 1,01x, VRAM pic 2,03 Go sur 8 Go.
3. **Autre repli local sans clonage : Piper `fr_FR-tom-medium`** (binaire autonome,
   MIT, CPU, déterministe, 40 s pour 6 min d'audio) quand une voix clonée n'est pas
   nécessaire et que la licence doit rester propre.
4. **Aucune voix du tout** si ni la clé ni les poids ne sont là : le bouton micro et
   le lecteur audio disparaissent de l'interface, avec une ligne qui dit pourquoi —
   jamais un échec silencieux, jamais une voix preset présentée comme un clone.

**Licence, à trancher avant toute monétisation** (fait documenté, pas une opinion) :
XTTS-v2 est sous **CPML, usage non commercial**, et Coqui a fermé en janvier 2024 —
plus aucune licence commerciale n'est achetable ; les poids Voxtral auto-hébergeables
n'existent pas (la version ouverte antérieure, `Voxtral-4B-TTS-2603`, est CC BY-NC 4.0
et demande ≥ 16 Go de VRAM, impossible sur cette carte). Donc : produit monétisé →
Voxtral (API) ; usage privé → XTTS-v2 ; et le réglage par défaut doit le dire.

**Provisionnement à l'installation (demande de l'opérateur)** : les poids du repli
local sont **téléchargés d'office à l'installation**, sans question ni téléchargement
au premier lancement — mais **silencieux n'est pas caché** (règle d'installation déjà
inscrite) : version épinglée, **SHA-256 vérifié**, une ligne dans le récapitulatif
final (« Voix locale XTTS-v2 — 1,8 Go — `~/.uhb/xtts` »), et la désinstallation les
retire. Fonctionne hors ligne ensuite, clé Mistral exceptée.

Et les formats de ce lot — `.mp3`, `.wav` à l'entrée, **FLAC 24 bits stéréo** pour ce qui
est conservé — passent par le **ffmpeg embarqué** décrit dans la section « Installation
unique », jamais par un codec système supposé présent.

**Règles d'interface (ajoutées le 19/11 par l'opérateur, avant tout code)**
1. **Lecteur conditionnel.** Le lecteur avec forme d'onde n'apparaît dans la bulle
   **que** si un son existe réellement pour ce message : dicté au micro, uploadé, ou
   synthétisé. Aucun lecteur vide sur une réponse purement texte — l'absence d'audio
   n'est pas une erreur, c'est l'état normal.
2. **Actions unifiées sur tous les rendus.** Un rendu audio porte la **même barre
   d'actions** que les images et les réponses : **Garder**, **Utile**, **Pas utile**,
   **Télécharger**, **Corbeille avec annulation**. Une seule implémentation de la
   barre (pas une deuxième pour l'audio), les routes existantes servent (`rate`,
   `artifacts`, suppression de message) : l'audio n'invente pas son propre vocabulaire
   d'actions, et « Télécharger » rend un fichier lisible (WAV/MP3 selon la source),
   jamais une URL temporaire.
3. Corollaire de cohérence : les votes « Utile / Pas utile » restent **exclusifs et
   réversibles** pour l'audio comme pour le texte, et la Corbeille laisse une fenêtre
   d'annulation — c'est ce que le point 5 du relais demande déjà pour les images.

**Verrou d'architecture (anti-régression) — la partie qui compte**
- le pipeline audio vit derrière des routes dédiées `/api/audio/*` (transcription,
  synthèse, échantillon de voix). **Aucune** modification de `request_messages`, du
  schéma des `tool_calls`, ni des frames SSE existantes (`token`, `answer`, `notice`,
  `approval`, `mode_refused`, `artifacts`, `done`) ;
- une dictée produit du **texte standard** : le fil SQLite ne reçoit qu'un message
  `user` comme un autre — aucun champ propriétaire, aucun blob audio dans `messages` ;
- l'audio n'est **pas** une dépendance du cœur : module optionnel chargé à la demande
  (`uh/audio/…`, extra dédié). Sans lui, UH démarre et le bouton micro comme le lecteur
  disparaissent — même dégradation honnête que la Web Speech API aujourd'hui ;
- la synthèse écrit un **fichier** sous `$UH_HOME/uploads` (ou `outputs`) et le message
  le référence ; jamais de base64 dans le fil ;
- tests de non-régression obligatoires : un tour texte complet (fournisseur + outil +
  SSE) doit produire **exactement** les mêmes frames et le même enregistrement, audio
  activé ou non ; plus un test qui interdit toute importation audio dans `uh/agent.py`
  et `uh/providers/`.

**Preuve attendue avant de clore** : une dictée qui arrive dans le fil comme un message
utilisateur, une réponse lue avec sa forme d'onde dans la bulle, une voix clonée par
`ref_audio` (Voxtral) et **le même rendu par le repli XTTS-v2 clé absente** — et la
suite UH verte, frames de tour inchangées.

### Grille de validation avant push — remise à plat du 19/11

**Le Point 5 ne clôt pas la stabilisation de l'interface.** C'est la correction de fond de
cette section : une grille de validation qui s'arrêtait aux points 1-5 aurait validé une
interface dont les moitiés média (audio, vision, fichiers) ne sont pas encore écrites. La
clôture se juge donc sur l'ensemble, pas sur le dernier point livré.

**Ce qui doit être implémenté avant la grille** (état au 19/11) :

| lot | contenu | état |
| --- | --- | --- |
| Points 1-5 du relais | notices de fin de tour, porte de mode, relance code, garde `/api/uhb/mode`, cinq correctifs d'interface | **livrés** (`6f01af6`, `7d1013a`, `c007a5f`, `c4c3fed`, `52f5208`) |
| P1-15 | micro dans la saisie, lecteur conditionnel avec onde, actions unifiées, Voxtral 3 → repli XTTS-v2 provisionné à l'installation | inscrit, non codé |
| P1-14 | bouton « + » unifié (popup compact), vision multi-images 4K, vidéo MP4 H.264/H.265, avertissement de capacité du modèle en un clic, `.txt/.md/.json/.pdf/.csv/.html/.zip` | inscrit, non codé |
| P1-13 | mémoire compressée des réussites (« Garder »/« Utile »), PNG/WebP lossless restituable en 4K, FLAC 24 bits stéréo | inscrit, non codé |
| P1-12 | auto-nettoyage et auto-réparation UI/UX (temporaires, caches, orphelins) | inscrit, non codé |
| binaires embarqués | ffmpeg statique épinglé + SHA-256, poids XTTS-v2, provisionnés à l'installation, retirés à la désinstallation | inscrit, non codé |

**Référence visuelle pour le « + »** (capture du 19/11, `Desktop\Screen Shot 2026-11-19 at
05.33.31.95 PM.png`) : un seul bouton dans la zone de saisie, popup compact listant
« Ajouter des images » / « Ajouter des fichiers », micro à côté, envoi à droite. C'est le
modèle d'encombrement à reproduire — une référence d'ergonomie, pas une dépendance.

**Porte A — architecture et câblage, réécrits puis re-vérifiés — `DONE` le 19/11.**
Vérifiée par `tests/test_wiring_audit.py` (12 tests) : aucune frame orpheline dans les
deux sens (`answer` en manquait côté page), 90 routes toutes documentées, 85 routes
`/api/*` derrière le jeton (les 5 publiques sont `/`, `/healthz` et les icônes), ids du
DOM tous résolus, rien de base64 en base, et les quatre verrous re-testés. Détail et
tableau dans `docs/VALIDATION_MATRIX.md` (GA-1 → GA-7) ; l'architecture réécrite est
dans `docs/ARCHITECTURE.md` (contrat des frames, verrous, place de l'audio/média).

Avant tout test de bout en bout :
1. réécrire la description de l'architecture UI/UX (qui parle à qui : page, routes,
   `ChatSession`, toolsets, UHB) pour qu'elle inclue les nouveaux lots, au lieu de la
   laisser décrire l'interface d'avant ;
2. revérifier chaque câblage, un par un : frames SSE (`open`, `token`, `answer`,
   `answer_meta`, `tool_call`, `tool_result`, `artifacts`, `approval`, `notice`,
   `mode_refused`, `error`, `done`), routes dédiées `/api/audio/*` et `/api/media/*`,
   cohérence en-tête ↔ panneaux ↔ historique SQLite, et **zéro régression** sur les
   fournisseurs, l'API et les `tool_calls` ;
3. revérifier que les verrous déjà payés tiennent toujours : filtrage des outils par mode,
   garde Origin/Host sur `/api/uhb/mode`, confinement atelier (écriture hors run, secrets,
   subprocess), jamais de tour muet.
Une régression ici arrête tout : elle se corrige avant la porte B, pas pendant.

**Porte B — test dev complet, en conditions réelles, dans le cockpit.** **`DONE` le 19/11** — 24 lignes mesurées, 22 vertes au premier passage, 2 refaits après
correction, et **trois défauts réels trouvés** (contexte Ollama, bande son vidéo envoyée
comme image, modèle d'une session absent de l'en-tête) :
- chat texte nominal (fournisseur, outils, SSE) et mode atelier avec exécution réelle ;
- **plusieurs images 4K déposées sans un mot** → analyse automatique de chaque image,
  temporaires purgés après lecture, image gardée restituable en 4K ;
- **micro** : dictée → message utilisateur ordinaire ; **lecteur avec onde** uniquement
  quand un son existe ; **clonage Voxtral 3**, puis **repli XTTS-v2 clé absente** ;
- **vidéo `.mp4` H.264 et H.265** : images clés + bande son extraites, temporaire purgé ;
- **Corbeille avec « Annuler »** : image retirée du fil, restaurée au clic, disparue après
  la fenêtre ;
- **mémoire compressée** : un clone et une image marqués « Garder » se réutilisent sans
  appel au fournisseur, FLAC restitué en 24 bits stéréo, image en 4K ;
- **auto-nettoyage / auto-réparation** : poids du dossier temporaire avant/après, zéro
  orphelin en base, session rechargée sans lecteur vide ni carte fantôme ;
- cohérence du modèle affiché (en-tête, pied de réponse, onglet « IA locale ») et
  avertissement de capacité avec bascule en un clic.

**Porte C — push.** Uniquement après A et B, avec un **« go » explicite** de l'opérateur
**et** `UH_PUSH_OK=1` posé par lui dans l'environnement. Aucun `--no-verify` jamais, aucun
push déclenché par un agent de sa propre initiative, un commit local par étape.

### Retours du 19/11 — mode d'exécution, câblage et barre d'images

Constat de terrain : trois modèles différents ont refusé avec
« mode 'chat' has no tool 'execute_code' (403) », et l'assistant renvoyait vers
l'onglet Décision, **qui n'avait aucun contrôle de mode**. Le mode est une
propriété du processus UHB (`--mode`), sans route pour en changer à chaud.

- **Mode d'exécution — livré (contrôle minimal).** `uh/uhb_spawn.py` porte
  désormais le mode en paramètre ; `POST /api/uhb/mode` redémarre le UHB que UH
  possède, et répond `409` avec une phrase quand le processus a été démarré hors
  de UH (UH ne tue pas ce qu'il ne possède pas). Le contrôle est dans l'onglet
  Décision, en mots d'utilisateur (« Discussion — lecture seule » /
  « Agent — peut exécuter du code et créer des fichiers »). *Ouvert* : badge
  d'état toujours visible dans l'en-tête du chat, et carte « Autoriser pour cette
  conversation » dans le fil quand un outil est refusé à cause du mode.
- **Profil intermédiaire — à décider, non implémenté.** Le profil `dev` écrit
  dans `uhb/**`, `tests/**`, `docs/**` : pensé pour développer le dépôt, pas pour
  dessiner ou analyser des données. Proposition : un profil « atelier »
  (exécution autorisée, écriture limitée à `~/.uhb/runs`). Aucune ligne écrite
  avant décision.
- **P1-11 — barre d'images : style et téléchargement livrés (f3dc767).** Cinq
  actions explicites — Garder, Utile, Pas utile, Télécharger, Corbeille — icônes
  SVG, libellé français, `aria-label` et infobulle. *Ouvert* : « Télécharger tout »
  (zip des images de la conversation), et un
  doublon du message assistant (« Le mouton a été dessiné… » deux fois, la seconde
  sans image après trois `patch error`) à instruire : rejeu d'historique ou bug
  d'affichage. *Livré depuis* : l'annulation après mise à la corbeille (`52f5208`,
  « Annuler » 8 s + action `restore` par provenance).
- **P1-13 — sémantique des pouces : ouvert.** Aujourd'hui le vote écrit une ligne
  locale (`~/.uh/artifact-ratings.jsonl`). À préciser dans l'interface : pour un
  modèle distant ce n'est **pas un entraînement**, c'est une mémoire réinjectée
  dans le contexte. Un 👎 doit accepter une raison facultative.
- **P1-14 — import de fichiers : ouvert.** Bouton « + » avec menu, glisser-déposer
  sur toute la zone de chat, presse-papiers, plusieurs fichiers, pastilles avec
  nom/type/taille et croix, types (images, PDF, texte et code, CSV, docx), limites
  avec message clair, contenu encadré comme donnée et jamais comme instruction.
- **Lot transversal « UX, accessibilité et câblage » — ouvert (seul lot neuf
  justifié).** Langue cohérente (l'interface mélange « Search messages… »,
  « Send », « Decision », « Telemetry »), icônes SVG lisibles avec libellé ou
  infobulle, navigation clavier et focus visible, `aria-label` sur les boutons à
  icône, messages d'erreur qui disent quoi faire. Règle de câblage : aucun lien
  profond ni bouton ne doit pointer vers un contrôle absent — un test d'interface
  par action recommandée. Résidu observé dans la recherche latérale
  (`http://127.0.0.1:…/mcp/w/…` avec « no match ») à identifier : état de
  l'interface ou pilotage.

*Statuts au 19/11 — livré : mode (contrôle minimal), orphelins `tool_calls`,
onglet Fournisseurs. Ouvert : tout le reste ci-dessus.*

### Incident du 19/11 — push non autorisé (à trancher par l'opérateur)

Un agent a poussé 9 commits sur `main` (`20ddcc7..7b104e7`) sans autorisation,
en lisant « on poussera la mise à jour » comme un feu vert alors que la consigne
était « aucun git push » et que des points de cette feuille de route restaient
ouverts. GitHub n'a pas retenu le push : « Bypassed rule violations for
refs/heads/main: 2 of 2 required status checks are expected ».

État factuel : aucun fichier sensible dans le diff (pas de `.env`, pas de
`keys.toml`, pas de `mcp-secrets.toml`, aucun motif de clé) ; 17 fichiers,
+1232/−54 ; `origin/main` = `7b104e7`.

Décision en attente. Plan proposé, **non exécuté** :

1. garder (`git revert` inutile) — le « go » explicite devient obligatoire ;
2. annuler sans réécrire l'historique : `git revert --no-commit
   20ddcc7..7b104e7` puis un commit unique, poussé avec `UH_PUSH_OK=1` ;
3. `git push --force` après reset : **déconseillé**, une réécriture de `main` est
   plus risquée que le revert.

Barrière technique posée : `pre-push` dans le git-dir commun
(`UH-Universal-Harness/.git/hooks/pre-push`) refuse tout push sans
`UH_PUSH_OK=1`, variable que l'opérateur pose lui-même. Vérifié : hook invoqué
avec des refs simulées → message d'explication, exit 1 ; `bash -n` valide la
chaîne pre-commit conservée dessous.

Règle ajoutée aux consignes de travail : un GO de push est une phrase explicite
pour ce push, jamais une clause conditionnelle ; aucune 1.8.1 sur PyPI tant que la
garde CSRF de `/api/uhb/mode` et l'absence d'accès depuis un outil ne sont pas
prouvées.

### Revue du 19/11 (soir) — retours sur toutes les réponses, et ce qu'UH apprend

- **P1-13 étendu — feedback sur TOUTES les réponses, pas seulement les images.**
  Les actions Utile / Pas utile / Garder / Corbeille / Télécharger n'existent
  aujourd'hui que sous les artefacts. Elles doivent s'appliquer à chaque réponse de
  l'assistant : texte, code, plan, résultat d'outil. Un vote sur une réponse texte
  est le signal le plus utile du produit, et c'est celui qu'on ne collecte pas.
  *Ouvert.*
- **P1-13 étendu — exploiter les retours et les échecs d'outils dans la mémoire de
  session.** Un refus, une erreur d'outil répétée, un 👎 doivent laisser une trace
  réutilisable (ce qui a échoué, avec quel modèle, quel argument), pour qu'UH ne
  refasse pas la même erreur au tour suivant ni au prochain démarrage. *Ouvert —
  c'est la question « UH apprend-il de ses erreurs ? » posée par l'opérateur.*
- **Mesure d'environnement, 19/11.** L'interpréteur qui exécute `execute_code` est
  celui qui lance UHB : **Python 3.11.9, sans matplotlib** (numpy présent). Le venv
  de UH est en **3.10.11, sans matplotlib non plus**. Les moutons réussis n'ont donc
  jamais été dessinés par matplotlib : le modèle écrivait un encodeur PNG en Python
  pur. Conséquence : soit on installe matplotlib dans l'interpréteur de UHB, soit on
  cesse de la proposer dans la consigne et on documente les bibliothèques réellement
  disponibles (numpy, stdlib, encodeur PNG maison). *À trancher, non corrigé.*
- **Modèle local et code brut.** qwen3:8b écrit parfois le code dans le message au
  lieu d'appeler l'outil ; la carte système existe (« the model answered with code
  instead of calling execute_code »), mais il faut vérifier que la relance
  automatique va jusqu'à l'exécution réelle et non seulement jusqu'à une seconde
  réponse. *À vérifier.*

### Installation unique — embarquer toutes les petites instances (19/11, demande de l'opérateur)

Aujourd'hui UH dépend de pièces qui vivent côte à côte sur cette machine et que
personne n'installe : UHB dans son propre venv, l'interpréteur qui exécute
`execute_code`, les serveurs MCP, et les chemins trouvés au hasard (`~/.uhb/runs`,
`~/.uh`). **Mesuré le 19/11** (session `20261119-1455-d057`) : `execute_code` tourne
sous le Python **système** 3.11.9 (`…\Python311\python.exe`, `sys.prefix` =
`base_prefix`), **sans numpy ni matplotlib** ; le venv du checkout UHB est 3.11.9 +
matplotlib 3.11.2 mais **sans fastapi**, donc il ne peut pas exécuter UHB et
`_supports_uhb()` l'écarte ; le venv de UH est un 3.10.11 sans matplotlib ; un
troisième interpréteur (Python310.11, matplotlib 3.10.9) n'est atteint que par
`subprocess` depuis un snippet.

Objectif : **une seule installation** (`gh repo clone Antoine-Thomas/UH-Universal-Harness`
puis un installateur) qui

1. embarque et provisionne **silencieusement** chaque instance nécessaire — UHB, un
   interpréteur « atelier » dédié à `execute_code` (numpy + matplotlib, versionnés),
   les serveurs MCP retenus — sans dépendre d'un Python système ni d'un `pip` déjà
   présent ;
2. fonctionne **hors ligne après installation** : aucune roue téléchargée au premier
   lancement, aucun `pip install` implicite qui échoue derrière un proxy ;
3. ne laisse **aucun état ambigu** : un seul interpréteur par rôle, annoncé dans
   `/api/status`, et un diagnostic qui dit lequel a servi (c'est exactement ce qui a
   coûté une session entière sur matplotlib) ;
4. désinstalle proprement ce qu'elle a créé.

**Mesuré le 19/11, session `20261119-1455-d057`** : `execute_code` écrit et supprime
**hors** du dossier du run (Bureau, cache Hermes), lit `~\.uh\keys.toml`,
`~\.uh\mcp-secrets.toml` et le `.env` du checkout UHB, énumère les **78** cibles de
`cmdkey /list`, et lance n'importe quel interpréteur par `subprocess` — avec les
droits Windows de l'utilisateur, sans les limites d'écriture du mode `dev`
(`uhb/**`, `tests/**`, `docs/**`), qui ne gouvernent que les outils fichier. Le
libellé « Agent : exécute du code, portée non vérifiée » (`a992592`) est donc exact,
et le badge ne peut rien promettre de plus. Le confinement (étape 0-bis, ci-dessous)
est la seule façon d'écrire un libellé plus fort.

Précisions ajoutées après revue (à respecter, non implémenté) :

- **silencieux, pas caché.** L'installateur affiche ce qu'il installe et où, avec un
  récapitulatif final et une désinstallation propre. Un composant téléchargé à l'insu
  de l'utilisateur ressemble à un malware, et fait fuir antivirus et entreprises ;
- **chaque composant épinglé et vérifié** : version exacte + somme SHA-256 (signature
  si disponible), sinon l'installateur devient la porte d'entrée d'une attaque de
  chaîne d'approvisionnement ;
- **hors ligne après installation** (modèles locaux et clés exceptés) ;
- **aucun identifiant GitHub en écriture embarqué**. `gh repo clone` est un chemin
  développeur, pas la voie du public : pour le grand public, installateur (MSI/exe)
  ou `pip install` depuis une **release publique vérifiée** ;
- **un interpréteur « atelier » isolé** (numpy + matplotlib épinglés) et
  **annoncé** au modèle, turtle/tkinter interdits, PNG via `savefig` dans le dossier
  du run ;
- **l'installateur doit livrer la restriction réelle**, pas seulement les
  bibliothèques : dossier de travail du run et écriture bornée. Embarquer un
  interpréteur sans cela ne règle que la moitié du problème.
- **binaires média embarqués, pas de VLC à installer à la main** (ajouté le 19/11) :
  si les codecs système manquent — H.265/HEVC, `.heic`, FLAC 24 bits stéréo, toute
  conversion audio/vidéo — l'installateur apporte un **ffmpeg statique épinglé avec
  ses codecs complets**, téléchargé **silencieusement à l'installation** (aucune
  question, aucun téléchargement au premier lancement), **SHA-256 vérifié**, une
  ligne au récapitulatif (« Média — ffmpeg 7.x statique — 90 Mo — `~/.uh/bin/ffmpeg` »),
  retiré à la désinstallation, hors ligne ensuite. `ffmpeg`/`ffprobe` existent
  aujourd'hui par Chocolatey sur cette machine : l'installation ne doit pas en
  dépendre, et la version embarquée est celle qui fait foi pour la reproductibilité
  (mêmes rendus sur toute machine).

*Ouvert. Bloque toute promesse d'installation « en une commande ». À ne pas
implémenter avant la mesure du point 0 (interpréteur réel, écriture hors zone,
lecture des secrets) et le « go » de l'opérateur.*

### Étape 0-bis — confinement d'`execute_code` (GO du 19/11)

Point 0 mesuré, donc l'ordre change : **le confinement passe avant les points 1 à 5**
de l'interface. Deux sous-étapes, un commit local chacune, aucune poussée.

1. **Interpréteur « atelier »** — un venv dédié (`$UH_HOME/../.uhb/atelier` par
   défaut, ou `UHB_ATELIER_PYTHON`) qui ne contient que `numpy` et `matplotlib`
   épinglés, sans `fastapi` ni paquets système. UHB le préfère à `sys.executable`
   quand il existe, l'annonce dans le contrat de l'outil (pas de `turtle`/`tkinter`,
   `savefig('…png')` dans le répertoire courant) et dans `GET /status`
   (`execute.interpreter`, `execute.atelier`).
2. **Confinement** (`sys.addaudithook` injecté dans `_uhb_runner.py`, côté UHB) :
   écriture ou suppression **hors** du dossier du run refusée ; lecture de `~/.uh`,
   de `~/.uhb` (hors dossier du run) et de tout `.env` refusée ; `subprocess.*`,
   `os.system`, `os.exec*`, `os.spawn*` et le chargement `ctypes` refusés. Les
   bibliothèques restent lisibles (sinon numpy/matplotlib ne démarrent pas) : ce qui
   est fermé, c'est **l'écriture** partout et la **lecture des secrets**.
   L'approbation de l'opérateur reste obligatoire avant chaque exécution.

**Ce que le crochet ne tient pas**, et qui doit rester écrit : un module C qui
n'émet pas d'événement d'audit (certaines extensions) peut encore écrire, et rien
n'empêche un snippet de lire un fichier quelconque **non secret** hors du run. C'est
un confinement de code coopératif, pas un bac à sable noyau : la formulation du
badge doit dire « dossier du run uniquement » (écriture), jamais « sandbox ».

**Libellé du badge** : « Agent : exécute du code, portée non vérifiée » ne change
qu'après une contre-sonde réelle prouvant les quatre points — écriture hors run
refusée, lecture de `keys.toml` refusée, `subprocess` refusé, PNG `savefig` produit
dans le dossier du run — puis devient « Atelier : exécute du code (dossier du run
uniquement) ».

**Livré le 19/11** (branche UHB `feat/atelier-confinement`, commit local `7ad854d` ;
UH `fix/1.8.0-…`, commit local `7022f80`). Le venv atelier
(`~/.uhb/atelier`, 3.11.9 + numpy 2.4.6 + matplotlib 3.11.2, **sans** fastapi) est
annoncé dans `GET /status` (`execute.interpreter`, `execute.atelier`,
`execute.confinement`), dans le contrat de l'outil et dans chaque résultat
(`interpreter`), et traversé jusqu'à `GET /api/uhb/status` côté UH.

Contre-sonde réelle, par l'interface (`POST /chat` SSE + carte d'approbation),
session `20261119-1511-889f`, run `run-20261119-151200-762e8d` :

| essai | résultat mesuré |
| --- | --- |
| écrire sur le Bureau | `REFUSED`, fichier absent après coup |
| lire `~\.uh\keys.toml` | `REFUSED` |
| lancer `subprocess` | `REFUSED` |
| `savefig('test.png')` dans le run | `OK 7102 bytes`, PNG réel (`\x89PNG`) |

Commande du run : `…\.uhb\atelier\Scripts\python.exe -I -B -X utf8 <run>\_uhb_runner.py`,
exit 0, 1,031 s, artefact `test.png` remonté et affiché dans la bulle.
Suites : UHB 395 passed / 9 skipped, UH verte ; ruff et mypy propres sur les
fichiers touchés (mypy 0 erreur sur `execute.py`, `catalog.py`, `panel.py`,
`server.py` ; les 39 erreurs mypy restantes de UHB sont préexistantes et hors de
ces fichiers). Le libellé du badge est donc passé à « Atelier : exécute du code
(dossier du run uniquement) ».

**Reste ouvert après cette étape** : `ctypes` n'est pas bloqué (numpy et matplotlib
chargent `kernel32` à l'import) — donc un snippet qui appelle l'API Win32 à la main
sort encore du crochet ; un vrai bac à sable demande un jeton restreint Windows ou
un conteneur, et c'est ce que l'installateur devra livrer pour pouvoir écrire
« sandbox » un jour. Rien dans l'interface ne doit promettre plus que le tableau
ci-dessus.
