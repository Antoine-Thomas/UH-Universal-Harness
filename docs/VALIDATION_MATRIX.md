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
| P1-7a | Cloud: S3 connector, offline end to end | UH | `DONE` | **Prouvé hors ligne** : `tests/test_p1_7a_s3_connector.py` (9 tests, 2 s, serveur factice `tests/fake_s3_mcp.py`, aucun réseau hors boucle) via le **vrai gabarit** `s3` — secrets absents de `config.toml` et reçus par le processus enfant ; les deux refus distincts (`InvalidAccessKeyId` sans clé, `SignatureDoesNotMatch` avec un secret trop court) ; la liste `allow` du gabarit **retire** un huitième outil que le faux publie, avec témoin à huit outils sans liste ; écriture/lecture/listage ; lien présigné 200 puis 403 sans toucher à l'objet ; refus d'écriture distante qui laisse le fichier local intact ; reconnexion qui retrouve l'objet. Contre-épreuves : filtre `allow` désactivé → le test du filtre tombe ; refus sans clé renommé → le test correspondant tombe |
| P1-7b | Cloud: Google Drive connector, offline | UH | `BLOCKED` | **Décision de l'opérateur (24/11) : garder le gabarit, le verrouiller avec `allow = ("search",)` et documenter la dépréciation du paquet** ; `@botrun/mcp-google-drive@0.2.1` reste l'alternative si un paquet maintenu est préféré. À appliquer **uniquement après G8, G5 et les points 3/4 du lot 2**. Mesuré le 24/11 : `@modelcontextprotocol/server-gdrive@2025.1.14` est **déprécié** (« Package no longer supported ») et ne publie qu'**un** outil, `search`, plus des ressources `gdrive:///<file_id>` ; le gabarit n'a **aucune** liste `allow`, donc « lecture seule » ne repose aujourd'hui que sur la parole du serveur — c'est ce que la liste fermera. Autres candidats mesurés : `@hachiai/mcp-google-drive@1.2.0` (lecture **et** écriture, liste obligatoire), `mcp-google-drive@1.6.2` (CRUD, un seul mainteneur). Ni test ni faux serveur avant cette échéance |
| P1-7c | Cloud: real accounts (AWS, Google) | UH | `TO VERIFY` | Exige une preuve **datée par l'opérateur** avec de vrais identifiants : un bucket réel, un dossier Drive réel. Aucun serveur factice ne peut en tenir lieu |
| P1-8m | UHB monitoring: three distinct states — absent / error / ok — visible in the cockpit, not a silent failure | UH | `DONE` | `/api/uhb/status` publishes `state` (`ok` / `absent` / `error`) and a French `sentence`; `uhb_state_of()` separates *nothing listening* (`kind: transport` → absent) from *answers but broken* (HTTP error, invalid JSON, `connected: false` → error). The badge shows the three labels with their own class, its tooltip carries the server's sentence, and a click on absent/error opens the UHB panel instead of switching a mode that has no object. Proof: `tests/test_uhb_state.py` (6 tests through the real route, with a fake UHB for ok / 500 / invalid JSON / closed port / no URL) and `tests/e2e/test_uhb_state_badge.py` (5 browser tests: labels, `data-uhb`, class, tooltip, panel sentence). Counter-proof: `error` remapped to `absent` → 2 tests fail. |
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
| P2-9 | Versioned UH <-> UHB wire contract: major mismatch refused by name, minor accepted, absence treated as v1 | UH+UHB | `DONE` | UHB publishes `contract: {name: uh-uhb, version: 1, minor: 0}` in `GET /status` (`uhb/core/contract.py::WIRE`, tested through the real server in `uhb/tests/test_wire_contract.py`). UH reads it in `uh/contract.py::check()`: same major -> ok, newer minor -> ok, different major or another contract name -> `incompatible` with a French sentence naming both versions, no version published -> `unknown` (presumed v1: an old UHB that works is not refused). The toolset probes the contract once per instance and refuses remote calls with `reason: contract_mismatch` (409) **before** any POST; `/api/uhb/status` turns an incompatible UHB into `state: error`, so the badge shows it. Proof: `tests/test_contract_wire.py` (11 tests: 6 pure, 3 through the route with a fake UHB, 2 through `Uhbtoolset`, the last one counting that no POST leaves). Counter-proof: major check disabled -> 3 failures. |
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

## Porte D - cockpit, runs and extensions (`DONE`, 2026-11-21)

Ouvre avec le lot **P3-16** (`docs/ROADMAP_UH_V2.md`), ferme avant **P4-1**. Une ligne ne
passe a `DONE` que sur une preuve rejouable : un test du depot, un chiffre mesure, ou une
capture datee. Statut global au 2026-11-21 : **DONE** - releve ci-dessous, chaque ligne
rejouee ce jour-la sur le code de `main` (`bfd37fa`).

| ID | Critere | Sous-lot | Preuve exigee | Etat |
|---|---|---|---|---|
| D-1 | Un run non garde ne laisse **aucun** dossier dans `~/.uhb/runs` | P3-16a | comptage avant/apres sur 20 runs : `ls -1 ~/.uhb/runs \| wc -l` identique ; plus aucun dossier de plus de 7 jours | `DONE` |
| D-2 | Un run garde est compresse et archive dans un **depot Git dedie** | P3-16a | `git -C ~/.uhb/brain-runs.git log --oneline` montre un commit par run garde, `--stat` egal aux fichiers produits ; le run est restaurable a l'identique (SHA-256 du manifeste) | `DONE` |
| D-3 | Le filet de securite TTL/quota fonctionne sans intervention | P3-16a | quota depasse volontairement (N dossiers) : les plus anciens sont purges, les gardes sont epargnes | `DONE` |
| D-4 | Le panneau Projets affiche l'etat Git **exact** | P3-16b | `git status --porcelain` du terminal et la ligne du panneau identiques octet pour octet, sur 3 depots (propre / modifie / en avance) | `DONE` |
| D-5 | La bascule de workspace se fait sans redemarrage | P3-16b | changement de projet dans l'UI, tour envoye dans le nouveau workspace, chemin du fichier produit verifie sur disque | `DONE` |
| D-6 | Un cron est declenchable a la main depuis l'UI, avec sa sortie | P3-16c | declenchement, sortie affichee, prochaine execution recalculee, journal du planificateur coherent | `DONE` |
| D-7 | L'etat Infra dit **injoignable** sans casser le panneau | P3-16c | un service arrete volontairement (RAG, MARI ou Ollama) : sa ligne passe a `injoignable`, les autres restent vertes, aucun panneau vide | `DONE` |
| D-8 | Une extension s'active et se diagnostique a chaud | P3-16d | activation d'un serveur MCP et d'un skill sans redemarrage, appel de test reussi, refus explique quand la config est fausse | `DONE` |
| D-9 | La campagne complete est verte | P3-16d | suite UH + suite UHB vertes, ruff et mypy propres, `node --check` sur les fichiers de l'interface, et les lignes D-1 a D-8 rejouees d'affilee | `DONE` |

**Ce que la Porte D ne remplace pas.** Les Portes A et B restent valables telles quelles :
D verifie ce que P3-16 ajoute, pas ce qui a deja ete prouve. Une regression sur une ligne de A
ou de B pendant P3-16 est une regression, pas une « nouvelle preuve a refaire ».

**Separation des pouvoirs, rappelee pour la porte elle-meme.** D-1 a D-3 sont mesures cote
UHB (il possede la politique) ; D-4 a D-7 sont mesures dans l'interface UH (elle affiche et
pilote) ; D-8 et D-9 croisent les deux. Une ligne D-1 prouvee par un test UH serait un
contresens.

### Releve du 2026-11-21 (`main` = `bfd37fa`)

Une seule session, les neuf lignes rejouees d'affilee. Les chiffres ci-dessous sont les
sorties brutes des scripts de mesure (`scratch/porteD_uhb_d1_d3.py`,
`scratch/porteD_cockpit_4678.py`, `scratch/porteD_d5.py`), pas des resumes.

| ID | Mesure | Sortie exacte |
|---|---|---|
| D-1 | 20 runs crees puis clos dans une racine isolee | `avant = 0 / apres = 0`, actions `['purged']`, **0 dossier survivant** sur 20 |
| D-1 (reel) | `~/.uhb/runs` | **381** dossiers, **0** de plus de 7 jours (`find -mtime +7 \\| wc -l`) |
| D-2 | run garde archive | `action=archived`, bundle `sha256=17f4aa4ea507529c…`, commit `44c97c3`, dossier supprime, journal a **1** entree |
| D-2 (reel) | depot reel `~/.uhb/brain-runs.git` | **1** commit : `keep run-20261120-232455-908f9e (mode=dev, 0 files, 2289 B)` |
| D-2 | restauration | 3 fichiers compares, **3 identiques**, ecart `[]` - `manifest.json` compris (meme SHA-256). Voir le complement ci-dessous : la premiere mesure donnait 2 sur 3 et a conduit a un correctif UHB |
| D-3 | TTL puis quota | `avant = 7 / apres = 2`, **5 expires**, **2 gardes epargnes**, 1155 octets liberes |
| D-3 (reel) | `uhb runs prune` (a blanc) | 381 runs, 0 protege, **181 candidats**, 3 185 221 B recuperables, tri du plus ancien au plus recent |
| D-4 | trois depots, porcelain du panneau vs terminal | propre / modifie / en avance : **identique = True** les trois fois ; `branche/ahead/dirty` = `('main',0,0)`, `('main',0,2)`, `('main',1,0)` |
| D-5 | bascule puis tour reel | bascule `{"ok": true}` ; tour `qwen3:8b` **HTTP 200 en 87 s** ; `preuve_d5.txt` = `porte-d` dans B, **absent de A** ; `git status B` = `['?? preuve_d5.txt']` |
| D-6 | tache interne lancee depuis l'API | jamais lancee : `next_run = 14:55:54` ; apres lancement `last_run = 14:40:54`, `status = ok` ; **second passage** `14:40:58` -> `next_run = 14:55:58` (recalcul sur le dernier passage) ; journal : `mecanisme = registre`, 1 tache |
| D-7 | RAG volontairement injoignable (`UH_RAG_URL` sur un port ferme) | **7 services** listes, RAG `state=down` raison **`port 1 : TimeoutError: timed out`**, encore `up` : `UH, Ollama, SiYuan, MARI` ; `actions_available = []` |
| D-8 | serveur MCP minimal `mini` | inventaire `[('mini','idle',True)]` ; bascule off `ok=True, hot=True, enabled=False` ; epreuve pendant l'extinction **`1 outil(s) liste(s)`** ; reactivation `ok=True` |
| D-9 | suite UH | `1981 passed, 2 skipped, 74 deselected` en 376,57 s, `ruff : All checks passed!`, `mypy : no issues found in 85 source files` |
| D-9 | suite UHB (venv du depot) | **`429 passed, 9 skipped`** en 22,08 s (426 avant le correctif du manifeste) |
| D-9 | `node --check` sur les fichiers de l'interface | aucun message : tous passent |

**Ce que la campagne a trouve, et ce qu'il en est advenu.**

1. `uhb.runs.write_manifest()` reecrivait `created` a chaque appel et **remplacait** le
   manifeste au lieu de le fusionner ; `archive()` puis `restore()` le reecrivaient tous les
   deux. Un run garde « naissait » donc au moment de son archivage, et le manifeste restaure
   n'etait plus celui qui avait ete archive. **Corrige** cote UHB (PR #21, `3ea361d`) : fusion
   des cles, `created` preserve, manifeste d'origine conserve a cote du bundle dans le depot
   (`run-manifest.json`), et `* -text` dans l'arbre d'archive pour que git ne convertisse
   plus les fins de ligne. D-2 se mesure desormais **3 sur 3**, SHA-256 du manifeste compris.
2. L'epreuve `POST /api/extensions/.../test` **demarre** le serveur MCP meme lorsque
   l'extension est eteinte - c'est voulu (diagnostiquer une extension ne suppose pas de
   l'allumer pour les tours), mais cela signifie que « eteint » se lit dans `build_toolsets`
   et dans l'inventaire, pas dans le resultat de l'epreuve.

### Complement du 2026-11-21 (soir) : les preuves d'interface de D-6 et D-8

Les deux criteres parlent du cockpit, pas de la route : ils se prouvent en cliquant.
Mesures prises sur `main` cote UH (`c00be4d`) et cote UHB (`3ea361d`, apres la fusion de la
PR #21 qui corrige le manifeste).

| ID | Mesure | Sortie exacte |
|---|---|---|
| D-2 | restauration champ par champ (apres correctif UHB) | SHA-256 avant/restaure **identiques** (`654101a01a71819d…`), octets identiques, et `created`, `session`, `keep`, `mode`, `summary`, `run` egaux deux a deux |
| D-6 | bouton « Lancer maintenant » clique dans le panneau Taches | etat de depart `jamais lancee`, puis `#tasks-hint` = `<id> : ok`, ligne `dernier passage : … (ok)`, `title` de la ligne contenant la sortie de `doctor`, `prochaine : …` ; `tests/e2e/test_p3_16_tasks_extensions_ui.py`, **3 executions consecutives, 3 fois vert** |
| D-8 | entree `[[mcp.servers]]` sans `command`, ecrite avant le demarrage | ligne rendue `data-state="broken"`, « cassée », detail `configuration refusee avant tout demarrage`, raison **`mcp.servers[casse]: 'command' is required`** ; compte `{total: 1, enabled: 0, broken: 1}` |
| D-8 | entree bien formee mais **binaire inexistant**, bouton « Tester » | `#extensions-hint` = **`fantome : refuse · binaire introuvable : … 'C:/…/nexistepas.exe' ([WinError 2] …)`** ; la ligne reste listee (une epreuve qui echoue n'efface rien) |
| D-8 | meme chemin, teste unitairement | `tests/test_extensions.py::test_a_malformed_mcp_entry_is_listed_broken_with_its_reason` ; **controle negatif** : avec l'ancien code, `AttributeError: 'str' object has no attribute 'get'` (extensions.py:192) - l'inventaire entier tombait |

Ce que D-8 a revele au passage : `mcp.status()` publie ses refus sous forme de **phrases**,
et `uh.extensions._mcp_entries()` les lisait comme des dictionnaires. Une seule entree MCP
mal ecrite suffisait donc a faire tomber tout le panneau Extensions, au lieu d'afficher une
ligne cassée avec sa raison - exactement le chemin que le critere demande. Corrige dans le
meme lot : les deux formes sont acceptees, et une entree refusee est nommee d'apres
`mcp.servers[<cle>]` quand la phrase la porte.

## What is missing to be able to say "proven"

- one dated capture per screen concerned, on the published version;
- one automated test per path, in the repository (not a mock);
- the same proof **seen from UHB** for everything that crosses both projects.
