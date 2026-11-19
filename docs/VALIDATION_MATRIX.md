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
| P1-12 | Transcript notice: no false positive after a turn or a long conversation (>200 messages); any real gap shown outside the thread as a plain-French status control, never a red bubble between messages | UH | `DONE` | The read window now follows the thread (`RECONCILE_MIN_WINDOW` / `_WINDOW_MARGIN` / `_MAX_WINDOW`, one re-read after `RECONCILE_RETRY_MS` before believing a gap), and the notice left the conversation for a header control (*« Actualiser la vue »*, `#view-stale`). Proof: `tests/e2e/test_p1_12_transcript_notice.py` on a real Chromium — 250-message session, a routed turn appends bubbles past the old 200-row window (no notice, no `.msg.failed`), then a forced store gap shows the control, whose click refreshes the view and changes nothing in SQLite; plus 7 static tests in `tests/test_transcript_notice.py`. Regression check: the same e2e test run against `d1015a0` fails at *aucun encart rouge dans le fil* (1 `.msg.failed`) |
| P1-13 | Graphs: a chart/diagram/map request produces an image in the bubble (`execute_code` → `.png` published in `artifacts`), not a text tutorial | UH+UHB | `TO VERIFY` | **Proven (2026-11-18)**: `matplotlib` 3.11.2 + `numpy` 2.4.6 installed in the UHB venv render a real PNG (14 374 B, 440×275, Agg); the system prompt now asks for a drawn chart (`uh/console.py`); a tool result carrying an inlined image becomes `message["artifacts"]` plus **one** `artifacts` event (`tests/test_tool_images.py`, 7 cases); the browser draws it and redraws it after a reload (`tests/e2e/test_p1_13_charts.py`, 2 cases, real Chromium). **Missing**: one dated capture of a *real model* turn (a provider deciding to call `execute_code` for the chart) — that decision is not deterministic and a canned turn cannot prove it |

## P2 — User experience and CLI

| ID | Criterion | Side | State | Proof / what is missing |
|---|---|---|---|---|
| P2-1 | The interface says what is connected / disabled / why something failed / what to do | UH | `TO VERIFY` | Progress: named errors (`source: command` vs model), action buttons, token warnings. **Missing**: a screen-by-screen inventory and the remaining English text (Decision) |
| P2-2 | CLI: help, actionable errors, exit codes, diagnostics, docs matching the interface | UH | `TO VERIFY` | `uh doctor`, non-zero exit codes, `--version` consistent with `/healthz`. **Missing**: a message audit (some are English inside a French UI) |

## P3 — Functional evolutions

| ID | Criterion | Side | State | Proof / what is missing |
|---|---|---|---|---|
| P3-14 | Character & role per project: a **« Caractère / Rôle »** tab sets the assistant's posture, tone and business speciality (presets or free text), **independent from the skills**, persisted per session in SQLite | UH | `DONE` (2026-11-18) | **Delivered**: 7 presets + free text (`uh/roles.py`), the **Caractère** tab in the configuration panel and a compact selector in the session header, stored in `sessions.metadata["role"]` (no migration) and applied in `system_message()` — after the base prompt, before the skill block. **Proofs**: `tests/test_roles.py` (9 cases) — presets from the module never from the client, bounded free text, prompt appended byte-for-byte, and the **independence proof**: skills files, `config.toml` (MCP servers), active toolsets and tool schemas identical before/after a role change; `tests/e2e/test_p3_14_role.py` (3 cases, real Chromium) — preset chosen in the header and kept after a reload, mid-session switch, panel listing all 9 entries, free-text role kept. **Not claimed**: that a given model *sounds* like the role — that is a model behaviour, non-deterministic; what is proven is that the posture reaches the system prompt and nothing else moves |
| P3-15 | **Intelligent orchestration**: a natural-language request reaches the model with the *relevant* toolsets and at most one skill — no `/command` needed, no phantom tool, no tool offered "just in case" | UH (+UHB unchanged) | `IN PROGRESS` (2026-11-18) | **Router prepared and wired, flag off by default** (`UH_INTENT_ROUTING=1`): `uh/intent.py` (families + real catalog vocabulary + capped selection + `ask` on ambiguity + a name outside the catalog can never be returned) and `ChatSession._intent_toolsets()` in `uh/console.py` (flag off → catalogue unchanged byte for byte; router exception → same fallback; small talk → no tool; journal entry without arguments or content). **Proofs**: `tests/test_intent.py` (12) + `tests/test_intent_wiring.py` (12, incl. T6/T7/T12/T13/T14/T15), `tests/test_decision.py` and `test_console.py` (58 tests together), full suite **1574 passed / 2 skipped** with the flag off, ruff + mypy clean (70 files). **Missing**: a real multi-provider turn (T10 end to end) and an observed UHB refusal on a selected-but-unauthorised tool (T7 real) — so the lot is not DONE |

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

## Porte A — audit de câblage UI/UX (19/11, avant tout push)

Cette porte ne juge pas des fonctionnalités : elle vérifie que ce qui est livré est
**relié**. Chaque ligne est un test du dépôt, rejouable.

| ID | Critère | État | Preuve |
|---|---|---|---|
| GA-1 | Aucune frame SSE orpheline, dans un sens ou dans l'autre | `DONE` | `tests/test_wiring_audit.py::test_no_frame_is_emitted_without_a_consumer` lit `console._emit`, `app.emit`/`frame` et les handlers `app.js` ; les frames construites par un helper (`answer_meta`, `approval`, `mode_refused`) sont listées avec leur producteur, qui est vérifié présent. Trouvaille réelle : `answer` était **émise et jamais consommée** → handler ajouté (remplit la bulle seulement si aucun token n'est arrivé) |
| GA-2 | Toute route est documentée, et les familles `/api/audio/*`, `/api/media/*`, `/api/memory`, `/api/maintenance` existent | `DONE` | `test_every_route_is_documented` (90 routes, 0 absente) + `test_the_audio_and_media_families_are_isolated_under_their_own_prefix` |
| GA-3 | Les 5 routes publiques sont les seules sans jeton | `DONE` | audit : `GET /`, `/healthz`, `/favicon.ico`, 2 × `apple-touch-icon` ; **85 routes `/api/*` portent `require_token`** |
| GA-4 | Aucun id du DOM utilisé par `app.js` n'est absent de la page ou créé par elle | `DONE` | `test_the_ids_the_page_uses_exist_or_are_created_by_it` (115 ids ; `execution-mode`, `llm-panel`, `skill-menu`, `local-models` sont construits par le panneau, et le test accepte les trois formes de construction) |
| GA-5 | Rien de base64 en base, un lecteur audio ne naît que d'un clip réel | `DONE` | `test_the_thread_never_stores_base64`, `test_an_audio_player_only_ever_comes_from_a_real_clip`, `test_the_media_warning_is_hidden_until_a_file_needs_it` |
| GA-6 | Les quatre verrous sont toujours câblés | `DONE` | `test_the_locks_the_relay_paid_for_are_still_wired` (porte de mode, garde Host/Origin, audithook atelier, isolation audio/média) + suites dédiées `test_mode_gate`, `test_uhb_mode_guard`, `test_audio_chain`, `test_turn_never_silent` |
| GA-7 | Architecture UI/UX réécrite avec les lots livrés | `DONE` | `docs/ARCHITECTURE.md` : contrat des frames (13 types avec producteur et consommateur), les quatre verrous, la place de l'audio et du média « à côté » du tour, ce qui est stocké vs référencé, mémoire et nettoyage |

## Porte B — banc d'essai dev complet (19/11, `DONE`)

Un passage, 24 lignes mesurées sur le serveur lancé (`PYTHONPATH= UH_UHB_EXECUTE=1`).
Chaque ligne est un chiffre lu dans une réponse HTTP, un fichier, le journal du serveur
ou `ffprobe` — pas une impression. Tableau complet : `porte_b_table.md` (scratch du
relais), résumé ici.

**Trois défauts réels trouvés par le banc, corrigés avec leurs tests** :

1. **Contexte Ollama** — trois images de 1536 px débordent le défaut de 8 192 jetons
   (`request (9641 tokens) exceeds the available context size`). Le provider demande
   maintenant la fenêtre **mesurée** (`/api/show`), bornée à 32 768 :
   `tests/test_gate_b_media_fixes.py`.
2. **Bande son d'une vidéo envoyée comme image** — Ollama répondait `Failed to load
   image or audio file`. Les images clés partent seules ; la piste audio a sa propre
   clé (`soundtrack`), pour la dictée : même fichier de test.
3. **Modèle affiché d'une session** — l'en-tête annonçait le défaut global
   (`auto/best-free`) pendant que la session tournait sur `qwen2.5:7b`. `/api/status`
   accepte `session_id` et rend le couple de la session ; la page le demande :
   `tests/test_status_scope.py`.

Et deux lignes de banc qui accusaient le code à tort, corrigées côté banc (dit ici pour
que personne ne « re-corrige » du code sain) : la purge lue **avant** que le thread worker
ne l'ait faite (attendre 3 s), et un « sans jeton » qui portait quand même le jeton par
l'en-tête par défaut du client HTTP.

| Groupe | Lignes | Résultat mesuré |
|---|---|---|
| 1. Démarrage & mode chat | 4 | page + libellés servis ; tour streamé **sans aucune approbation** ; `offered` en chat sans `execute_code`/`write_file`/`patch` |
| 2. Atelier & sécurité | 3 | bascule 200 (`mode=dev`) ; garde **401/403/403/403** + légitime 200 ; dessin PNG exécuté après **1 approbation** |
| 3. Tour texte | 2 | frames `open, token, answer, answer_meta, done` ; `answer_meta` = statut de session = modèle demandé |
| 4. Capacité & bascule | 3 | `capable=false` + perte annoncée ; premier candidat `ollama/qwen3.5:9b` ; bascule 200 → `capable=true` |
| 5. Lot 4K sans prompt | 3 | analyse déclenchée sans texte ; mémoire **3000×1688 PNG** (12,9 Mo) sha OK ; copies `-sent` purgées sauf la gardée |
| 6. Vidéo H.264 + H.265 | 2 | `turn_media images=10 kinds=['video','video']` ; images clés + bande son extraites puis purgées |
| 7. Audio & dictée | 3 | synthèse `engine=xtts` (Voxtral 429 en amont) 2,04 s ; **FLAC 48 kHz / 2 canaux / 24 bits** ; dictée `engine=whisper` |
| 8. Corbeille & votes | 2 | `up, down, none` journalisés dans l'ordre ; corbeille 200 (fichier absent) puis `restore` 200 (présent) |
| 9. Auto-nettoyage | 2 | `derived: 0`, `unmentioned ≤ 2`, `missing: 0` ; poids identique avant/après (delta 0) |

**Ménage de fin de banc** : sessions de test supprimées (17), dossiers d'uploads de
sessions disparues retirés, entrées mémoire orphelines oubliées, `~/.uh/uploads` ramené
de 216 Mo à 155 ko, `~/.uh/memory` à 0 entrée, `config.toml` restauré à l'octet.

**Porte C** : à ta main. Aucun push n'a été fait.

## What is missing to be able to say "proven"

- one dated capture per screen concerned, on the published version;
- one automated test per path, in the repository (not a mock);
- the same proof **seen from UHB** for everything that crosses both projects.
