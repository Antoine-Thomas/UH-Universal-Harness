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

The backend is done enough to be looked at: the question is now what the operator
sees while it runs. Items are listed with their state, not their ambition.

- **Report on the Desktop** - *delivered* (`--export-desktop`, copy to
  `~/Desktop/UH_Rapport_Nuit.txt`). A pass nobody opens is a pass nobody reads.
- **Splash screen** - the first paint waits on nothing: the UH mark pulses while
  the shell and the first API calls land, then fades. No fake depth on the mark,
  no click that pretends to do something. *To do.*
- **Independent window** - rounded frame, shadow, centred, so the UI reads as a
  desktop app rather than a page. No Electron: the browser is the runtime.
  *To do.*
- **Dockable consoles** - one UI, several panes (chat, logs, terminal, decision),
  detachable, resizable, with magnetic edges. Reference studied:
  `github.com/makushingu/harness-maxxing` (already named in section 5).
  *To do - the largest item, and the one most likely to break the layout.*
- **Telemetry** - tokens per pass and per day, cost, wall time, RAM/VRAM. Measured
  from `dreams/schedule.log` and `/api/system/*`, drawn as sparklines. No new
  dependency until a graph cannot be drawn without one. *To do.*
- **Second brain, drawn** - Mermaid, animated, showing what the memory holds
  (SiYuan blocks, RAG chunks, wiki notes) and which way a decision travelled.
  Read-only: the diagram never writes. *To do.*
- **Maintenance** - temp files, caches, orphaned uploads, with the reclaimed space
  reported before and after. Deletion is explicit, never silent. *To do.*
- **Voice and text helpers** - TTS and dictation through the Web Speech API (no
  key, no cloud, degrades to absent where the browser lacks it), automatic session
  titles, prompt improvement, and an error explainer that reads a traceback and
  says which cause is likely. *To do, in that order.*

Constraint for every item above: `pytest -q` stays green, the nightly task keeps
working, and nothing here may require Ollama, a key, or a network call.
