# UH / UHB — validation matrix before the fusion

A fix announced is not a validated feature. This file lists the proofs required before
P4-2 (the fusion) and the real state of each one, as of 2026-11-17.

**States**: `DONE` (replayable proof: an automated test or a dated capture) · `TO VERIFY`
(the code exists, the proof is missing) · `NOT STARTED` · `BLOCKED` (waits for a decision).

Reading rule: a row only reaches `DONE` with the proof **in the repository** (a test) or
**a dated capture** — never "it was fixed".

**Published milestones**: `uh-harness` **1.7.5** is on PyPI (tag `v1.7.5` → `40f6f34`,
`Publish to PyPI` green). `uhb-harness` **0.2.0** is tagged and merged (`v0.2.0` → `17bd062`)
but **not yet on PyPI**: its `pypi` environment still needs the manual approval. Every row
marked `DONE` below is in the UH artefact published today; the UHB side is proven by the
tests in `17bd062` and ships as soon as the approval is clicked.

---

## P0 — Open problems (audit)

| ID | Criterion | Side | State | Proof / what is missing |
|---|---|---|---|---|
| P0-A | Two consoles / two processes: know who starts UHB, which processes stay, why they are visible | UH+UHB | `TO VERIFY` | Measured here: UHB runs as **2 processes** (`uhb.entrypoints.cli` + a `python311` child), detached; UH web is a third. Started by `uhb_spawn.ensure()`. **Missing**: a unified start/stop, and a "single window" proof |
| P0-B | Decision tab: useful, readable, in French | UH | `TO VERIFY` | **Works** (capture `1-decision.png` on 1.7.4: PROMPT/MODEL/UHB CHAIN/CONTEXT/DURATION, survives a reload). **Missing**: the content is English and internal jargon ("UHB was not consulted… no uhb_route call", "no chain"), so unusable by a non-developer |
| P0-C | On/off switch visible and operable | UH | `DONE` (browser proof) | Box measured **310×34 px**, `role="switch"`, `aria-checked` false→true on click, label "… activés", works from the keyboard (captures `2-switch-off.png`, `3-switch-on.png`) |
| P0-D | "Recherche web": same control, same requirement | UH | `NOT STARTED` | Still a **13 px checkbox** in the same panel (visible on `3-switch-on.png`) — the same visibility defect as the old on/off |
| P0-E | Version tested = version published | Process | `DONE` | `uh --version` and the machine's `/healthz`: **1.7.2**; the #10/#11 fixes are in **1.7.4**. The last visual test therefore ran the old version |
| P0-F | `execute_code`: transport budget, approval, and the sandbox's real boundary — one path | UH+UHB | `DONE` (UH #13 + #15, UHB #16 + #17, all merged, in 1.7.5 / 0.2.0) | Four fixes that only make sense together: the client waits `requested + margin` (bounded by the catalogue schema) instead of a flat 5 s; the sandbox's boundary is measured rather than claimed (environment scrubbed, files readable by absolute path); `execute_code` asks the operator before running; UHB refuses without the nonce it minted. Witnesses fail on the previous code (`2 failed` → `5 passed`); the merged tree is **1513 passed, 2 skipped** on `40f6f34` |
| P0-G | The approval gate itself: who decides, who enforces, and what it is not | UH+UHB | `DONE` (UH #15, UHB #17) | UH asks on the turn's stream while the tool call waits; UHB mints a **single-use** nonce (120 s, in memory, bound to `execute_code`), consumes it even when the run fails, and ignores any body claiming `approved: true`. No approver, a refusal, a raising notifier or a UHB that will not mint all give a typed `forbidden` with **zero** requests to `/tools/execute`. "Always allow" is scoped to `uhb:execute_code` and lives in memory only |

## P1 — Connections and performance (the heart of the fusion)

| ID | Criterion | Side | State | Proof / what is missing |
|---|---|---|---|---|
| P1-1 | Add a provider, store a key, resolve it, list models, test the connection | UH | `TO VERIFY` | Routes and codes exist; key resolution fixed for OmniRoute. **Missing**: one complete UI path (add, test, model, turn) |
| P1-2 | Errors 401/403/429/500, timeout, cut, recovery | UH | `TO VERIFY` | P1-9 covers transport/5xx/429 and stops on 401/402/403; tested at unit and provider level. **Missing**: the same cases seen from the UI against a real remote provider |
| P1-3 | OmniRoute: configuration, key, models visible, selection, **real conversation**, errors, and the same path through UHB | UH+UHB | `TO VERIFY` | **Proven**: key resolved (UH store), `eco` returned 200 `OK` from plain Python, 1 619 models listed (PR #11). **Missing**: a real turn **in the UI** and a turn **through UHB** |
| P1-4 | Chat and streams: cut, resume, bounded retry, no duplicates, transcript kept | UH | `TO VERIFY` | Measurement over 20 turns: 0 lost, 0 duplicate, 40 calls (P1-9 branch). **Missing**: the same measurement with a real remote provider and a server-side cut |
| P1-5 | Images: from tools and from APIs, safe download, display, persistence after a reload | UH | `TO VERIFY` | Whole chain proven in a test (`test_image_artifacts_e2e.py`), guards tested as refusals. **Missing**: a browser capture of an API image displayed and found again after a reload |
| P1-6 | MCP: discovery, connection, tools listed, calls, arguments, results, errors, refusals, disconnect/reconnect | UH (+UHB for authority) | `TO VERIFY` | stdio and HTTP/SSE clients tested, approvals, typed refusals. **Missing**: one complete UI path (server added, tool called from the chat, refusal visible) |
| P1-7 | Cloud: real inventory of connectors, auth, read, write, expiry, reconnect | UH | `NOT STARTED` | Declared templates: **`s3`** and **`gdrive`** (`uh/mcp_cloud.py`), routes `/api/mcp/cloud`, `/api/mcp/custom`, `/api/mcp/probe`. No end-to-end test at all |
| P1-8 | UHB tools executed from the UH chat (permissions, refusals) | UH+UHB | `TO VERIFY` | `Uhbtoolset` publishes the catalogue, 403 relayed, tested against a real UHB. **Missing**: the complete UI path |
| P1-9 | Long conversations (budget, compaction, context ceiling) | UH | `TO VERIFY` | Budget resolves to 24 000, `max_tokens` 2 048, compaction exists. **Missing**: one measured long session |
| P1-10 | Service start / stop, in one command | UH+UHB | `NOT STARTED` | `uh web` starts UHB as a child; **two processes stay visible** (P0-A). A unified stop exists (`/shutdown` + `stop_running`), not a unified "one window" start |
| P1-11 | Persistence: conversations, results, artifacts, reload | UH | `DONE` | SQLite store + `messages.artifacts` (in-place migration, tested), transcript reconciled append-only |
| P1-12 | Transcript notice: no false positive after a turn or a local command; any real gap shown outside the thread as a plain-French status control, never a red bubble between messages | UH | `NOT STARTED` | `reconcileTranscript` (`uh/web/static/app.js:1095`, called on `done` at :1379) still appends the `failed` bubble *« Le transcript enregistré contient moins de messages que l'écran »* + **Recharger la session** when the store returns fewer rows than the DOM — the writer lag the function's own comment documents (`system`/`failed` bubbles are already excluded from the count). **Missing**: a retry-once read before deciding, the out-of-thread control (*« Actualiser la vue »*), and a browser capture proving a normal turn and a `/web` command raise nothing |

## P2 — User experience and CLI

| ID | Criterion | Side | State | Proof / what is missing |
|---|---|---|---|---|
| P2-1 | The interface says what is connected / disabled / why something failed / what to do | UH | `TO VERIFY` | Progress: named errors (`source: command` vs model), action buttons, token warnings. **Missing**: a screen-by-screen inventory and the remaining English text (Decision) |
| P2-2 | CLI: help, actionable errors, exit codes, diagnostics, docs matching the interface | UH | `TO VERIFY` | `uh doctor`, non-zero exit codes, `--version` consistent with `/healthz`. **Missing**: a message audit (some are English inside a French UI) |

## P4 — Final product

| ID | Criterion | State | Condition |
|---|---|---|---|
| P4-1 | Complete functional validation of both projects together | `NOT STARTED` | Every P0/P1/P2 row above in `DONE` |
| P4-2 | Fusion of UH and UHB (one architecture, one start, no duplicate responsibility) | `BLOCKED` | P4-1 entirely validated |
| P4-3 | Repository `UHD-Universal-Harness-Desktop` | `BLOCKED` | P4-2 validated; do not move the current repositories before |
| P4-4 | Packaging Windows/macOS/Linux (installer, uninstaller, updates, signing, data preserved) | `NOT STARTED` | P4-2; licences and the weights of the media features settled before publishing anything that embeds them |

---

## Limits of the approval gate (read before trusting it)

The gate moves a decision, it does not build a wall. Written down because a report, a demo or
a comment claims more very easily:

- **It is not isolation.** The snippet still runs as the operator's user: `$UH_HOME/keys.toml`,
  `$UH_HOME/web-token`, `$UH_HOME/router-token` and `~/.uhb/shutdown-token` stay readable by
  absolute path — measured by the UHB #16 characterisation test.
- **It stops the client that skips the interface, not a local hostile process.**
  `POST /tools/approvals` is protected by loopback and `Origin` only: any process on the
  machine can mint its own nonce and then execute.
- **A third-party MCP server's own `execute_code` is not covered.** This gate lives on UHB's
  route and on UH's `uhb` toolset; a tool with that name arriving through `mcp:<server>` goes
  through the MCP approval path, another mechanism with its own scope.
- **`EXECUTE_MARGIN = 15 s` is a design constant, not a measurement.** It covers the kill of
  the process tree, the artifact scan and the response, and is only used after being bounded
  by the `maximum` the catalogue publishes.
- **The nonce proves authorisation, not identity.** UHB cannot tell which human clicked: it
  knows the nonce it issued was consumed exactly once, and that the server's mode allowed it.

## Dependencies between the lots

1. **P0-E comes first**: while the tested version is not the published one, every UI
   observation is invalid. Reinstall, then redo the captures.
2. **P0-B and P0-D** share one defect (English jargon, low-visibility controls): a single
   "panel readability" lot covers both.
3. **P1-3 gates P1-1/P1-2** for the remote half: without a real OmniRoute turn in the UI,
   the other providers are not validated differently.
4. **P1-6 (MCP) and P1-8 (UHB tools)** go through the same approval gate: validating them
   together avoids testing the same refusal twice.
5. **P1-10 (unified start)** is the first concrete step of P4-1: it fuses nothing, it
   removes the visible double console.
6. **P4-1 cannot be declared** while P1-7 (cloud) is `NOT STARTED`.
7. **What comes next, in this order**: **P1-8 (UHB monitoring** — absent / error / ok shown in
   the System panel), then **P4-1 (cross validation of the two components)**. P0-4 closed with
   1.7.5 / 0.2.0; nothing else in P0 blocks the pair.
8. **The `execute_code` lot merged in one order** — UHB #16 then #17 (no conflict), then UH #13
   then #15, which needed three adaptations: keep both the transport budget and the body
   carrying the nonce, give #13's integration test an approver, serve `/tools/approvals` in
   #13's fake server. Measured on the merged tree: **1513 passed, 2 skipped**.

## What is missing to be able to say "proven"

- one dated capture per screen concerned, on the published version;
- one automated test per path, in the repository (not a mock);
- the same proof **seen from UHB** for everything that crosses both projects.
