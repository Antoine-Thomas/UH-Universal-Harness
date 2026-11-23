# `uh web` — HTTP API contract

Read from the running code, not from memory: `tests/test_web_api_docs.py` fails
when a route exists in the app and is missing here, and when an element id used
by `uh/web/static/index.html` is missing from the id list below. Update this file
in the same commit as any route change.

## Transport and trust

| Rule | Value |
| --- | --- |
| Bind | `127.0.0.1:8765` by default; another `--host` is refused unless `--allow-remote` is given |
| Auth | token, generated per process; `/?token=…` sets a `HttpOnly; SameSite=Strict` cookie, `Authorization: Bearer <token>` is also accepted |
| Missing or wrong token | `401 {"detail": "…"}` |
| `Host` header | must be the bind host or loopback, else `400 {"detail": "host not allowed"}` |
| `Origin` header | when present, must match `http://<host>:<port>`, else `400` |
| `Sec-Fetch-Site` | `cross-site` is always `400`; a cookie-authenticated `POST`/`PUT`/`PATCH`/`DELETE` needs `same-origin` (or a matching `Origin`), else `403` |
| Headers on every response | `Content-Security-Policy: default-src 'none'; …` (no `unsafe-inline`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` |
| OpenAPI | disabled (`openapi_url=None`, `docs_url=None`) |
| Bodies | never logged; keys never appear in any response |

Error shape is always `{"detail": "<message>"}`. Messages never contain a key,
even when the failing input was one.

### Turn journal

Every chat turn appends one line per event to `$UH_HOME/uh-web.log` (rotated at
1 MB, 3 backups), keyed by a turn id:

```text
2026-11-15 16:04:11 turn turn_start turn=ab12cd34 session=1458-af7d provider=deepseek model=deepseek-flash
2026-11-15 16:04:11 turn tool_start turn=ab12cd34 tool=execute_code
2026-11-15 16:04:15 turn tool_end   turn=ab12cd34 tool=execute_code ms=4210 status=ok
2026-11-15 16:04:16 turn turn_end   turn=ab12cd34 reason=done ms=5120 tools=0
```

`reason` is `done`, `error`, `deadline` (the 10-minute cap) or `stopped`.
The journal never contains a prompt, an answer, a tool argument or a key: only
names, counts, durations and verdicts. It exists to answer "why did my turn never
come back", which nothing else in the product could.

## Routes

| Method | Path | Body / query | Response |
| --- | --- | --- | --- |
| GET | `/` | `?token=` | HTML page (sets the cookie when the token matches) |
| GET | `/static/*` | — | the three interface files (or `--static-dir`) |
| GET | `/healthz` | — | `{app: "uh", version}` — **unauthenticated on purpose**: it is how a second `uh web` recognises the instance already holding the port instead of silently serving another copy on the next one. Carries no secret |
| GET | `/favicon.ico` | — | `204`, empty. The page declares its icon as a data URI; this route answers the request a browser makes anyway (bookmark bar, "add to home screen") without a 404 in the console |
| GET | `/apple-touch-icon.png` | — | `204`, empty — same rule as `/favicon.ico` |
| GET | `/apple-touch-icon-precomposed.png` | — | `204`, empty — same rule as `/favicon.ico` |
| GET | `/api/status?session_id=` | — | `{version, provider, model, provider_error, sessions, store, schema_version, terminal, workspace}` — le couple `provider`/`model` est celui de la **session** quand `session_id` est fourni, sinon le défaut global (Porte B : l'en-tête annonçait le défaut pendant qu'une session tournait sur un autre modèle) |
| GET | `/api/commands` | — | `{commands: [{name, aliases, help, args, group}]}` |
| GET | `/api/sessions` | `?limit=` | `{sessions: [{…, short_id}]}` |
| POST | `/api/sessions` | `{title?}` | `{session: {…, short_id}}` |
| DELETE | `/api/sessions/{session_id}` | — | `{deleted, id, files_removed}` — messages cascade, uploads are removed from disk · `404` |
| PATCH | `/api/sessions/{session_id}` | `{title}` and/or `{folder}` and/or `{settings}` and/or `{role}` | `{id, title, folder, role, session}` · `400` empty body · `404`. `role` is a preset id (the posture is resolved server-side) or `{id: "custom", label, posture}`, bounded to 60 / 800 characters; `null` clears it |
| GET | `/api/roles` | — | `{presets: [{id, label}], none}` — labels only: the posture text never reaches the browser |
| GET | `/api/folders` | — | `{folders: [{name, count}]}` — folders are metadata values, so a folder with no session does not exist |
| PATCH | `/api/folders/{name}` | `{name}` | `{name, previous, moved}` · `400` empty/unchanged/contains `/` · `404` unknown folder |
| DELETE | `/api/folders/{name}` | — | `{deleted, unfiled}` — the sessions are kept, unfiled · `404` |
| POST | `/api/sessions/{session_id}/upload?name=<file>` | raw bytes as the body (no multipart) | `{attachment: {name, path, size, kind, status, note}}` · `400` empty/refused · `413` over 20 MB |
| DELETE | `/api/sessions/{session_id}/messages/{message_id}` | — | `{deleted, session_id, remaining}` · `404` (unknown session or message) |
| GET | `/api/sessions/{session_id}/messages` | `?limit=` | `{session, messages, provider, model}` · `404` |
| POST | `/api/artifacts/action` | `{source: {path \| run_dir+relative}, action: keep\|trash\|rate, vote?: up\|down, comment?, session_id?, message_id?, index?}` | `{action, kept\|trashed\|rated, …}` · `400` refusé : fichier hors du dossier `runs`, disparu, ou action inconnue. Le fichier est résolu côté serveur et doit vivre sous `~/.uhb/runs` (`uh/artifacts.py`) : le corps de la requête ne peut viser aucun autre fichier |
| GET | `/api/artifacts/trash` | — | `{files, root}` — ce que la corbeille contient avant de la vider |
| GET | `/api/media/capabilities?provider=&model=&kind=` | — | `{kinds: {image: [...], video: [...], audio: [...], document: [...]}, budget_bytes, max_side, ffmpeg, model?, vision_candidates?}` — P1-14 : les extensions acceptées par nature, le budget d'image envoyée, et ce que le modèle **actif** peut réellement lire (`capable`, `loss`, `source`, `verified`) |
| GET | `/api/models/vision` | — | `{candidates: [{provider, model, modalities, context, tools}], total}` — les modèles **configurés** qui lisent une image, capacité mesurée auprès du fournisseur (jamais déduite du nom) |
| GET | `/api/audio/capabilities` | — | `{engines: [{name, label, kind, clones, stt, tts, available, reason}], tts, stt, ffmpeg}` — chaque moteur dit pourquoi il ne peut pas tourner (P1-15) |
| POST | `/api/audio/transcribe?session_id=&name=&language=` | **octets audio bruts** dans le corps (pas de multipart : pas de `python-multipart`) | `{text, engine, clip}` · `503` aucun moteur : le détail nomme chaque tentative. Le texte rendu entre dans le fil comme un message `user` ordinaire — aucun champ audio n'est stocké |
| GET | `/api/voice/plan?profile=auto\|local\|cloud` | — | Plan vocal : `{profile, stt, tts, network_allowed, install[], machine{}, installation[], versions{}, python_executable}` — `python_executable` est l'interpréteur **du serveur** : toute commande `pip` du plan le nomme (`"<exe>" -m pip install …`). Décision pure (`uh.voice_plan`) : aucun moteur cloud sans clé, aucun appel réseau, aucune installation |
| POST | `/api/voice/install` | `{target: "faster-whisper", confirm: true}` | `{ok, output, command, version}` · `400` sans `confirm`, cible inconnue. Exécute **uniquement** l'installation de la dictée locale, après le clic ; rien ne part d'un rechargement |
| POST | `/api/audio/speak` | `{text, session_id?, voice?, reference?}` | `{kind, source: {path, root: "uploads"}, url, name, mime, bytes, seconds, sha256, engine}` · `400` texte vide ou référence hors des envois · `503` aucun moteur (détail par tentative). Les octets vont dans `$UH_HOME/uploads/<session>/audio/` : le fil ne garde qu'une référence |
| GET | `/api/audio/file?path=` | — | le clip (`audio/wav`), chemin résolu **sous `$UH_HOME/uploads`** uniquement · `404` hors racine ou disparu · `400` extension non audio |
| POST | `/api/artifacts/trash/empty` | — | `{action: empty_trash, removed, memory_forgotten}` — ne touche jamais un dossier de run ; vider la corbeille **oublie** aussi les entrées mémoire des fichiers qu'elle contenait (P1-13) |
| GET | `/api/projects?session_id=` | — | `{ok, current, root, projects: [{path, name, label?, branch, porcelain, dirty, ahead, behind, last_commit, ok, reason, current}]}` — `porcelain` est la sortie **brute** de `git status --porcelain=v1 -b` : c'est elle que la Porte D (D-4) compare octet pour octet au terminal |
| POST | `/api/projects/add` | `{path, label?}` | `{ok, project}` — refusé en 400 si le dossier n'est pas un dépôt (`git rev-parse --show-toplevel`) ou sort de `projects.root` |
| POST | `/api/projects/switch` | `{path, session_id?}` | `{ok, workspace, session_id}` — le workspace est **par session** (`sessions.metadata["workspace"]`), donc aucune autre conversation ne bouge (D-5) ; 403 hors registre / `projects.root` |
| GET | `/api/fs/list?path=&limit=` | — | `{path, name, workspace, parent, chain: [{name, path}], entries: [{name, path, kind, size, mtime, ext}], total, truncated}` — `path` vide = le workspace. Lecture **seule**, bornée aux racines de `FileToolset` : `403` sur `~/.uh` (`refused: … inside the UH configuration folder`), sur un chemin hors workspace et sur un lien qui en sort ; `400` si ce n'est pas un dossier. `chain` vient du serveur : le fil d'Ariane ne devine aucun séparateur |
| GET | `/api/fs/stat?path=` | — | `{path, name, kind, size, mtime, ext, symlink}` · `403` refusé (mêmes règles) · `404` disparu |
| GET | `/api/fs/preview?path=&max_bytes=` | — | `{kind: text, text, bytes, truncated}` · `{kind: image, data_url, bytes}` (PNG/JPEG/GIF/WebP/BMP/SVG, `413` au-delà de 8 Mio) · `{kind: pdf, readable: true, text}` ou `readable: false, reason` quand `pypdf` manque — un PDF n'est jamais annoncé lu sans l'avoir été · `{kind: binary, reason}` · `403` refusé. Texte borné à 200 000 o par défaut, 1 Mio au maximum |
| GET | `/api/fs/raw?path=` | — | les octets, toujours `application/octet-stream` + `attachment` (un SVG servi affichable serait du script depuis notre origine) ; `413` au-delà de `attachments.MAX_BYTES` · `403` refusé. C'est la source du bouton « Attacher au tour », qui repart ensuite par l'upload normal |
| GET | `/api/git/log?path=&session_id=&limit=&offset=` | — | `{ok, commits: [{sha, short, author, date, subject}], limit, offset, has_more, path}` — paginé (une ligne de plus demandée à git pour savoir s'il en reste) ; dépôt sans commit ⇒ `commits: []` avec `reason`, pas une erreur. Lecture seule, `403` si `path` n'est pas le workspace de la session |
| GET | `/api/git/diff?path=&session_id=&file=&staged=` | — | `{ok, file, staged, diff, bytes, truncated, reason}` — sortie de `git diff --no-color --ignore-space-at-eol` (l'espaces de fin ne font pas un diff), tronquée à 200 000 caractères · `400` fichier hors dépôt |
| POST | `/api/git/stage` | `{path?, session_id?, files: [...]}` | `{ok, staged: [...], project}` — `git add` sur des chemins RELATIFS au dépôt ; `400` chemin absolu, `..` hors dépôt, fichier vide · `403` si le dépôt n'est pas le dépôt actif, ou s'il n'est ni enregistré ni sous `projects.root` |
| POST | `/api/git/unstage` | `{path?, session_id?, files: [...]}` | `{ok, unstaged: [...], project}` — `git reset HEAD --` (repli `git rm --cached` sur un dépôt sans commit) ; mêmes bornes que `stage` |
| POST | `/api/git/commit` | `{path?, session_id?, message}` | `{ok, sha, short, message, project}` — message **obligatoire** (`400` s'il est vide), les hooks du dépôt tournent (aucun `--no-verify`), et **aucune route ne pousse** |
| GET | `/api/infra?log_lines=` | — | **Lecture seule.** `{ok, at, version, install, services, disk, logs, vram, alerts, read_only, actions_available: []}`. `services` : UH (uptime, port), UHB (état, mode, version, raison avec port et erreur système), Ollama (idem), puis les complémentaires de la roadmap **RAG (`UH_RAG_URL`, défaut `:8200`)**, **SiYuan (`SIYUAN_URL`, défaut `:6806`)**, **MARI (`UH_MARI_URL`, défaut `:55553`)** — sondés par TCP seul, jamais par leur API — et **Docker** (moteur via `docker version`, lecture seule, `reason` = première ligne de l'erreur, `binaire introuvable` si absent). `disk` : taille RÉELLE de `~/.uh` et `~/.uhb/runs` (parcours borné à 2 s, `complete: false` + raison si dépassé), espace libre. `logs` : dernières lignes de `uh-web.log` et `uhb.log`, bornées à 64 kio lues et 200 lignes, **jetons et en-têtes masqués**. `alerts` : `ok`/`warn`/`error` (UHB arrêté, Ollama muet, disque, VRAM, paquet installé au lieu du dépôt, code modifié). Aucune action : rien à arrêter, rien à relancer |
| GET | `/api/schedules` | — | `{ok, tasks: [{id, name, command, every_minutes, enabled, mechanism, trigger_path, created, last_run, last_status, last_detail, next_run, next_reason}], catalog: [{key, label, description, mutating}], mechanism, mechanism_label, platform, system_disabled, **now**, limits}` — `now` est l'**horloge du serveur** (`AAAA-MM-JJTHH:MM:SS`, heure locale, même format que `last_run`/`next_run`) : l'interface s'en sert pour dire « Aujourd'hui » et « Hier », afin qu'un navigateur dans un autre fuseau ne lise pas un passage du jour comme « hier ». Le catalogue est la **liste blanche** : `doctor`, `dream`, `runs-prune` (simulation) |
| POST | `/api/schedules` | `{name, command, every_minutes, enabled?}` | `{ok, task, trigger: {mechanism, path, detail}}` — `command` doit être une clé du catalogue, sinon `400` (`non autorisée`) ; cadence bornée à 5–10080 min, sinon `400` ; même nom = remplacement. Le déclencheur essaie le Planificateur Windows puis replie sur un `.vbs` Startup (`registre` si `UH_SCHEDULE_DISABLE_SYSTEM=1`), sans aucune élévation |
| POST | `/api/schedules/{task_id}/toggle` | — | `{ok, task}` — activer installe le déclencheur, désactiver le retire ; `404` si l'id est inconnu |
| POST | `/api/schedules/{task_id}/run` | — | `{ok, task, detail}` — exécute la tâche MAINTENANT par la même liste blanche, puis enregistre `last_run`/`last_status` ; `404` si inconnue |
| DELETE | `/api/schedules/{task_id}` | — | `{ok, removed, triggers}` — retire le registre et **seulement** les déclencheurs `uh-schedule-<id>.vbs` que nous avons écrits ; `404` si inconnue |
| GET | `/api/extensions` | — | **Gestionnaire d'extensions (D-8).** `{ok, groups: {mcp: [...], skills: [...], uhb: [...]}, counts: {kind: {total, enabled, broken}}, kinds}`. Chaque entrée : `{kind, name, state, enabled, detail, reason, tools}`. `state` : `connected`/`idle`/`failed`/`broken` (MCP), `ready` (skills), `ready`/`unreachable` (UHB). Une extension éteinte reste listée, une extension cassée porte sa raison |
| PATCH | `/api/extensions/{kind}/{name}/enabled` | `{enabled: bool}` | `{ok, kind, name, enabled, hot: true}` — `kind` ∈ `mcp`/`skills`/`uhb` (`400` sinon), nom absent de l'inventaire → `404`, valeur non booléenne → `400`. **Effet à chaud** : la liste d'outils est reconstruite à chaque tour, un serveur MCP éteint n'est même pas démarré ; les skills passent par `skill_prefs.json` |
| POST | `/api/extensions/{kind}/{name}/test` | — | `{ok, kind, name, ok: bool, detail}` — épreuve RÉELLE : handshake MCP (ou sonde HTTP/SSE), lecture du skill, `status()` de UHB. Tester une extension éteinte est permis. Un refus est expliqué (`binaire introuvable`, `handshake échoué`, `aucune instruction`) |
| GET | `/api/runs` | — | proxy de UHB `GET /runs` : `{ok, root, count, bytes, kept, recent, policy}` ; UHB arrêté ⇒ `{ok: false, reason, runs: []}` |
| POST | `/api/runs` | `{action: close\|prune\|restore, run?, keep?, apply?, revision?}` | proxy de UHB `POST /runs` ; `prune` reste une **simulation** sans `apply: true` ; UH ne supprime jamais lui-même |
| GET | `/api/memory` | — | `{entries: [{sha256, kind, path, bytes, source, session, message_id, format, width?, height?, bits?, channels?}], weight: {root, bytes, by_kind, entries}}` — ce que l'opérateur a gardé, en PNG lossless ou FLAC 24 bits (P1-13) |
| GET | `/api/maintenance?session_id=` | — | `{ok, swept: {derived, stale, freed_bytes}, summary: {files, unmentioned, unmentioned_bytes, missing, stale_days}}` — purge des copies préparées et **rapport** des orphelins dans les deux sens (P1-12). Aucune suppression de fichier mentionné |
| POST | `/api/uhb/mode` | `{mode: chat\|dev\|review\|observe}` | `{mode, changed, owned?, connected?}` · `400` mode inconnu ou UHB désactivé · `409` UHB démarré hors de UH (UH ne tue pas un processus qu'il ne possède pas) · `503` redémarrage impossible. Le mode est une propriété du PROCESSUS : le changer redémarre UHB, en `--mode <mode>` |
| POST | `/api/sessions/{session_id}/chat` | `{message, attachments?: [path], skill?: name}` | **SSE stream** (below) · `400` empty message, refused attachment, or unknown skill · `404` · `503` provider unreachable. `skill` names a markdown file from `$UH_HOME/skills` or the shipped `uh/skills`; its instructions ride on the system message for that turn only |
| POST | `/api/sessions/{session_id}/stop` | — | `{stopped: bool, reason?}` · `404` |
| GET | `/api/sessions/{session_id}/decision` | — | `{available, turn: {duration_ms, average_ms, turns}, prompt: {complexity, mode, reason, source, note}, model: {provider, model, served, substituted, chosen}, chain, usage, context: {budget, summarised_exchanges, kept_exchanges, summary}, uhb_consulted, tool_calls, turns}` · `404` unknown session |
| GET | `/api/memory/sources` | — | `{wiki: {available, label, path, notes, reason}, memory, rag, siyuan, sessions}` |
| GET | `/api/memory/wiki` | — | `{available, label, root, notes: [{path, name, size, modified, source}]}` |
| GET | `/api/memory/wiki/{note:path}` | — | `{path, source, size, text}` · `404` unknown or outside the wiki |
| GET | `/api/memory/rag` | — | `{available, queryable, reason, path?, chunks?, documents?, index_mb?, modified?}` — never queryable from UH: UHB has no RAG route |
| GET | `/api/memory/siyuan` | — | `{available: false, configured, reason, url}` — a UHB adapter, no route |
| POST | `/api/memory/rag/reload` | — | `{reloaded, service: {fragments?}, at}` · `409 {error, hint: "start it with: python uhb/services/rag/serveur_rag.py", kind: "service_offline"}` · `502 {error: "UHB unreachable", kind: "uhb_offline"}` |
| GET | `/api/memory/siyuan/blocks` | `?path=&limit=` | `{count, path, blocks: [{id, type, hpath, content}]}` · `400 {kind: "config_missing"}` · `401 {kind: "auth"}` · `502 {kind: "transport"}` |
| GET | `/api/memory/search` | `?q=<text>&limit=` | `{query, hits: [{kind: memory\|wiki\|session, title, path, snippet}]}` · `400` empty q |
| GET | `/api/memory/graph` | — | `{nodes: [{id, label, column, count, unit, state: on\|empty\|off, detail}], edges: [{from, to, label, wired}], wired}` — `state` is about the source (`off` = unreadable), `wired` is about the edge (UH really consults it for a turn) |
| GET | `/api/llm/settings` | `?session_id=` | `{provider, model, support: {provider, model, applied, ignored: [{name, reason}], supports_reasoning}, spec: {key: {kind, min?, max?, step?, choices?, default, label, hint}}, defaults: {enabled, values}, provider_keys}` — the panel draws itself from this; `support` says what the provider will drop |
| GET | `/api/uhb/status` | — | `{connected, version, mode, harness, providers, modes}` · `{connected: false, reason, log?, checkout?}` when UHB is down or disabled |
| GET | `/api/uhb/overview` | — | `{connected, owned, status, modes, tools, errors}`: UHB's `/status`, `/modes` and `/tools` in one answer (a failing part is `null` with its error under `errors`) · `{connected: false, reason}` when UHB is down or disabled |
| POST | `/api/uhb/route` | `{prompt, mode?, complexity?}` | `{mode, complexity, chosen, chain: [{ref, tier, model_id}], reason, allow_paid}` · `400` no prompt · `503` UHB unreachable |
| GET | `/api/dreams` | — | `{dreams: [{stamp, status, tokens, summary, ideas}], daemon: {...}}` |
| GET | `/api/dreams/{stamp}` | — | one dream, including `report`; `404` if unknown |
| DELETE | `/api/dreams/{stamp}` | — | `{deleted}`; `404` if unknown |
| POST | `/api/dreams/{stamp}/archive` | — | `{archived, path}` under `~/.uh/dreams/archived/` |
| POST | `/api/dreams/{stamp}/fulfill?upload=<server>` | — | `{stamp, plan, applied: false, upload}` — writes a plan in `~/.uh/dreams/actions/` **and** in `outputs_dir`; with `upload`, also stores it through that cloud server's upload tool (`{ok, bucket, key}` or `{ok: false, error}`). Applying stays a human decision |
| POST | `/api/dreams/daemon/{action}` | `start` \| `stop` | starts the idle daemon (only on an explicit click) or ends it after the current pass |
| GET | `/api/system/ollama` | — | `{installed, running, models, boost: active\|available\|unavailable, reason, optional: true, url, savings: {local_passes, passes_logged, tokens_saved_estimate, basis}}` — always `200` |
| GET | `/api/system/capabilities` | — | `{tier, ram_gb, cpu_cores, gpu, os, arch, recommended_model, recommended_budget, local_possible, sentence}` |
| GET | `/api/telemetry` | — | `{generated_at, window_days, usage, system, dreams}` — `usage`: `{days: [{date, local, cloud, turns, estimated}], totals (same keys), outside_window, undated, turns_without_usage, cost: {value: null, reason}}`; `estimated` counts turns recorded before decisions carried an `at`, placed on their session's date; `system`: `{cpu_percent, cores, ram, vram, gpu, platform}` where each probe may be `null`; `dreams`: `{passes, lines, diagnostics}` read from `dreams/schedule.log` |
| GET | `/api/backup` | — | `{backups: [{name, bytes, modified}], dir}` — newest first |
| POST | `/api/backup/export` | — | writes `$UH_HOME/backups/uh_backup_YYYYMMDD_HHMMSS.zip`, returns `{path, name, bytes, files, includes, excludes, wal_checkpoint, created_at}`; `keys.json` is never included |
| GET | `/api/backup/download/{name}` | — | the archive as `application/zip`; `404` for any name outside `$UH_HOME/backups/` |
| POST | `/api/backup/import` | raw body: the zip | restores after snapshotting the current home, returns `{restored, skipped, safety, files, was, restart_required}`; `400` when the archive holds no UH data or a member escapes the home, `413` above 256 MB |
| GET | `/api/skills` | — | `{skills: [{name, title, description, origin, native, path, bytes, modified}], user, native, user_dir, native_dir, native_available}` |
| POST | `/api/skills` | `{title, description, instructions, name?}` | writes `$UH_HOME/skills/<name>.md`, returns `{name, path, bytes, title, description}`; `400` for a missing title/instructions, a name that could escape the folder, or an existing file |
| GET | `/api/tools/catalog` | `?mode=` | `{available: true, tools: [{name, description, parameters, category, execution_type, version, enabled, required_permissions, tags, compatible_modes, available, reason}], count, mode, catalog_version, request_id}` — the catalogue read from UHB, passed through untouched. When UHB is disabled or unreachable: `{available: false, reason: {code, hint}, tools: []}`, never a `500` and never an empty list without a reason |
| GET | `/api/tools/catalog/{name}` | `?mode=` | `{available: true, tool: {…}, count: 1, mode, request_id}` — one tool. `404` from UHB is relayed as `{available: false, reason}` like the listing |
| GET | `/api/tools/status` | — | `{toolsets: {name: [tools]}, ollama, uhb: {connected, reason}, mcp: {supported: true, configured, servers: [{name, command, connected, state, protocol, server, version, tools, reason?}], http: [{name, url, connected, state, label, tools, reason, advice}], http_reason?, problems?}, terminal, workspace}` · `state` is `connected`, `idle` (configured, not started) or `failed`; an HTTP server is never reported connected without a live client |
| GET | `/api/setup` | — | `{needed, provider, model, ollama: {running, installed, models}, cloud: [names], huggingface_models: [ids]}` — `needed` is true only when no local model answers **and** no provider key is stored |
| POST | `/api/setup` | `{choice: ollama\|huggingface\|cloud, provider?, key?, model?}` | `{saved, provider, model}` — writes `default_provider` / `default_model` through the config writer and the key through the provider panel · `400` unknown choice or provider, a cloud choice without a key, or a local model that is not installed · `409` `config.toml` not writable (the message names the cause) |
| POST | `/api/fix` | `{instruction}` | `{stamp, applies, error, files, path, notes, diff}` — asks a provider for a unified diff and **proves** it with `git apply --check`; nothing is written to the tree · `400` empty instruction · `502` no provider / model refusal |
| POST | `/api/fix/{stamp}/apply` | — | `{applied, branch: "fix/auto-<stamp>", reverted, changed?, failed?}` — re-reads and re-checks the stored diff, applies it on a new branch, runs `ruff` on `uh` and `tests`, and reverts the patch when that fails. Never commits, never pushes · `400` not a stamp · `404` unknown proposal · `409` the patch no longer applies |
| GET | `/api/mcp/cloud` | — | `{templates: [{id, label, what, can_upload, upload_note, docs, fields: [{key, label, secret, required, placeholder, help}]}], upload_targets: [{name, tool, label}], http_servers: [{name, url, transport, state, label, connected, tools, token, has_token, insecure, reason, advice}], http_note}` — never a stored value; `token` is the mask (`••••` + 4 characters) |
| POST | `/api/mcp/http` | `{name, url, token?, label?, allow_insecure?}` | `{name, transport: "http", url, reason, warning, insecure, token, secrets, connection: {state, label, tools, duration_ms, reason, advice}, state}` — records an HTTP/SSE MCP server (Positron, an IDE, a hosted tool server) as a `[[mcp.servers]]` block with `transport = "http"` **then asks it a handshake in the same gesture**: `state` is the result (`connected`, `auth`, `unreachable`, `incompatible`, `timeout`). The URL is checked by `uh.urlguard`: http(s) only, no credentials in the address, no fragment, and **plain HTTP to a non-loopback host is refused (`400`) unless `allow_insecure: true`** (the token would travel in clear). Token → `mcp-secrets.toml` (owner-only ACL); the answer carries its masked form only · `400` bad name / refused URL · `403` not a loopback bind |
| POST | `/api/mcp/custom` | `{name, command, args: [..], env: {KEY: value}}` | `{name, custom: true, config, secrets, parsed, problems, state}` — adds any stdio MCP server: one `[[mcp.servers]]` block, env entries whose **name** looks like a credential (`TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `CREDENTIAL`) go to `mcp-secrets.toml`, the rest stay visible in `config.toml` · `400` bad name / empty command / `env` not an object · `403` not a loopback bind |
| POST | `/api/mcp/cloud` | `{template, name, fields: {KEY: value}}` | `{name, template, label, can_upload, config, secrets, parsed, problems, state}` — appends (or replaces by name) one `[[mcp.servers]]`; secrets go to `mcp-secrets.toml` (0600) and are merged into the child's environment, never written to `config.toml` · `400` unknown template / missing or unexpected field / bad name · `403` not a loopback bind |
| POST | `/api/mcp/probe` | `{name, transport, url?, token?, command?, args?, env?, allow_insecure?}` | `{state, label, connected, tools, duration_ms, reason, advice}` — handshake against a candidate **without saving it** (5 s cap). The popup keeps "Enregistrer" disabled until this answers `connected` · `400` refused URL / missing command · `403` off loopback |
| POST | `/api/mcp/import` | `{text, filename?}` | `{servers: [{name, transport, command?, args?, env?, url?, token?}]}` — reads a JSON config (`{"mcpServers": {…}}`, a single server, or a list) and the documented YAML subset (maps, scalar lists, quoted strings); normalises names, never writes · `400` unreadable file / unknown shape / empty |
| POST | `/api/mcp/{name}/test` | — | an HTTP/SSE entry: `{name, transport, url, state, label, connected, tools, protocol, server, version, duration_ms, reason, advice}` (P14.1); a stdio entry: `{name, command, connected: true, state: "connected", protocol, server, version, tools}`. Nothing is served from the cache: the answer is what the server said this second · `404` unknown name · `502` a stdio server failed (timeout, crash, JSON-RPC error) |
| GET | `/api/mcp/approvals` | — | `{pending: [{id, server, tool, arguments, asked}]}` — remote tool calls waiting for a click. The SSE stream carries the same ticket as an `approval` frame; this route covers a page that missed it |
| POST | `/api/mcp/approvals/{ticket}` | `{allow: bool, always?: bool}` | `{resolved: true, ticket, allow, always, trusted}` — answers one pending call; anything but `allow: true` refuses it and nothing runs. `always: true` trusts that **server** for the life of the process (in memory only, never written); `trusted` lists the servers trusted so far · `404` expired or unknown ticket · `403` off loopback |
| POST | `/api/mcp/{name}/call` | `{tool, arguments?}` | `{ok: true, server, tool, result, latency_ms}` — runs **one read-only tool** for the panel's "Tester un appel" button (`list_*`, `get_*`, `search_*`, `read*` only: a write is unreachable this way and still needs a chat turn and its approval card); a failing connection answers `{ok: false, error, latency_ms}` · `400` a tool that is not read-only · `404` unknown server · `403` off loopback |
| POST | `/api/dreams/run` | `{budget?}` | one pass now, in its own process: `{started: true, pid, budget, log}` · `400` budget not an integer · `409` a pass is already running |
| POST | `/api/system/quit` | `{confirm: true}` | `{quitting: true, uhb_stopped}` — stops the UHB this process started, answers, then ends `uh web`. Closing the browser window never stops a server, which is how `uhb.exe` outlived UH · `400` without `confirm` · `403` off loopback · `409` a turn is still running |
| POST | `/api/system/alive` | `{visible?: bool}` | `{alive: true, window, ttl_s}` — renews the lease of the window this server opened: the page sends it every 10 s, and only with `uh web --app` does the server stop UHB and exit after the lease goes quiet (45 s visible, 300 s hidden so a minimised window is not killed) · `401` without the token · `403` a cross-site caller |
| POST | `/api/uhb/start` | — | starts UHB from the panel (off the event loop, 25 s budget): `{connected: true, …status}` · `{connected: false, reason, log, checkout}` when it still does not answer · `400` `UH_UHB_URL` disabled · `503` the spawn itself failed |
| POST | `/api/uhb/stop` | — | stops the UHB this process started: `{connected: false, stopped}` |
| POST | `/api/models/pull` | `{name}` | `{started, name}` · `400` no name · `429` two pulls already running |
| GET | `/api/search` | `?q=&limit=` | `{query, hits: [{…, short_id}]}` |
| GET | `/api/models` | — | `{provider, model, current, choices: [{id, provider, local, note}], ollama, providers, pulling}` — an API provider appears only when it holds a key |
| POST | `/api/model` | `{model, provider?, session_id?}` | `{model, provider, note, persisted}` — the pair is **written to `config.toml`** (`default_provider`, `default_model`), so the next `uh web` starts on it; `persisted: false` means the file refused the write (a hand-edited config with a table) and the switch only holds for this process · `400` when the pair does not fit (a local id with an API provider, an uninstalled Ollama model, an unknown provider) |
| GET | `/api/providers` | — | `{providers: [{name, base_url, model, keys, verified, custom, in_chain}], key_entry_allowed, keys: {cloudflare: {configured, source, account_id, key_hints}, deepseek: {configured, key_hints}, omniroute: {detected, base_url, has_key, config_path}}}` |
| GET | `/api/providers/presets` | — | `{presets: [{name, label, base_url, model}], gateway_only: [{name, label, note}]}` · form defaults only, nothing is stored |
| POST | `/api/providers/{name}/keys` | `{key}` | `{stored: true, keys: n}` · `400` · `403` off loopback · `404` unknown |
| DELETE | `/api/providers/{name}/keys` | `{index}` | `{deleted: true, keys: n}` · `400` · `403` · `404` |
| POST | `/api/providers/cloudflare/config` | `{account_id, token}` | `{stored: true, account_id, key_hint, source}` · `400` (account id must be 32 hex) · `403` · no full key in the answer |
| POST | `/api/providers/deepseek/key` | `{key}` | `{stored: true, keys: n, key_hints: [...]}` · `400` · `403` |
| POST | `/api/providers/omniroute/import` | — | `{imported: true, name, base_url, key_hints}` · `403` · `404` no OmniRoute config |
| POST | `/api/providers/custom` | `{name, base_url, model?, key?}` | `{created: true, name, base_url, model}` · `400` · `403` |
| POST | `/api/providers/{name}/clone` | `{name}` | `{cloned: true, name, base_url, model, keys: n, cloned_from}` · `400` bad or taken name · `404` unknown · `403` off loopback · the copied key is never in the answer |
| POST | `/api/providers/{name}/test` | — | `{ok, status, error_kind}` · `404` |
| GET | `/api/router/settings` | — | `{allow_remote, consent, consent_date, strategy, extra_providers, warning}` |
| PUT | `/api/router/settings` | `{allow_remote, consent?, strategy?}` | the same object · `400` consent or strategy · `403` off loopback |

`403` on a write route comes from one of two rules. First, the server refuses to
listen beyond loopback unless it was started with `uh web --allow-remote`: the
cockpit writes configuration and runs tools, and the token in the URL is its only
credential. Second, **a state-changing request authenticated by the cookie alone
is refused (`403`) unless it proves its own origin** (`Origin` matching the bound
address, or `Sec-Fetch-Site: same-origin`). A cookie is ambient authority: a page
in another tab would otherwise be able to `POST /api/mcp/http` and register a
server of its choosing (CSRF / DNS rebinding). A request carrying
`Authorization: Bearer` has no such problem and needs no origin.

**No response ever carries a full key or MCP token.** The `keys` object of
`GET /api/providers` is the API-key panel's state: `configured`, the `source`
of the credential (`environment`, `keys.toml` or `none`), and at most a
**masked hint** per stored key (3 leading + 4 trailing characters, e.g.
`sk-…abcd`). A key that comes from the environment is reported as
`source: environment` with no hint at all: UH never wrote it and does not
echo it. Delete-by-index exists for the same reason: a key value must not
travel in a URL, which ends up in an access log. An MCP token is reported the
same way (`••••` plus its last four characters), read from
`mcp-secrets.toml` at answer time and never from `config.toml`.

`error_kind` is one of `auth`, `rate_limit`, `payment`, `server`, `timeout`,
`transport`, `redirect_refused`, `no_base_url` — the same classification the
router uses.

## SSE stream of `POST /api/sessions/{id}/chat`

Frames follow the usual `event: <name>\ndata: <json>\n\n` shape.

| Event | Data | Meaning |
| --- | --- | --- |
| `open` | `{session, model, skill}` | the turn started; `skill` is empty unless one was attached |
| `token` | `{text}` | one streamed fragment |
| `tool` | `{name, …}` | one tool call, before and after |
| `approval` | `{id, server, tool, arguments, timeout}` | a remote MCP tool is waiting for a click; answer with `POST /api/mcp/approvals/{id}`. Nothing runs before the answer, and no answer by `timeout` is a refusal |
| `message` | `{…}` | a whole assistant message when streaming is off |
| `error` | `{message}` | the turn failed; the message is already classified |
| `done` | `{session}` | terminal event, always sent, even after an error or a stop |

`POST …/stop` makes the stream end with `done`, not with an error.

### Dictée : l'état du micro est un élément d'interface

`#mic` (bouton Dicter) ouvre un enregistrement ; `#mic-bar` reste visible tant que des
pistes sont vivantes, avec `#mic-clock` (temps écoulé, puis « arrêt dans … » sur les
15 dernières secondes) et `#mic-stop` (« Arrêter »). Le client ferme les pistes dans un
`finally` après un arrêt, un plafond de deux minutes, un onglet masqué, `pagehide`,
`beforeunload`, une erreur d'enregistrement ou un changement de session. « Dicter »
(enregistrer) et « Écouter » (synthèse de la réponse) sont deux commandes distinctes.

## Element ids used by the current interface

`uh/web/static/index.html` + `app.js` rely on these ids; a replacement interface
served by `--static-dir` is free to use its own, but the ones below are the
contract of the bundled one:

```
app-banner
attach  attachments  command-chips  composer  files  folder-hint  mic  mic-status
mic-bar  mic-clock  mic-stop
tab-voice  voice-panel  voice-view
project-add  project-path  projects-hint  projects-list  projects-refresh  projects-view
runs-clean  runs-hint  runs-list  tab-projects
plus-backdrop  plus-close  plus-empty  plus-filters  plus-import  plus-title
plus-menu  plus-dictate  media-warning
files-view  files-crumbs  files-refresh  files-hint  files-list  tab-files
files-preview  files-preview-name  files-preview-meta  files-preview-body  files-attach
git-log  git-hint  git-more  git-status  git-changes  git-diff  git-message  git-commit
git-commit-hint
infra-view  infra-hint  infra-refresh  infra-alerts  infra-services  infra-disk
infra-install  infra-logs  tab-infra
tasks-view  tasks-mode  tasks-refresh  tasks-hint  tasks-form  task-name  task-command
task-every  task-create  tasks-catalog  tasks-list  tab-tasks
extensions-view  extensions-mode  extensions-refresh  extensions-hint
extensions-groups  tab-extensions
gauge-fill  gauge-label
decision-panel  decision-view  hardware-card  help-open  input  keys-priority  keys-view
role-custom  role-custom-save  role-label  role-panel  role-posture  role-select  role-view
tab-role
local-panel  local-view  tab-local  setup-banner
data-open  data-panel  data-view  tab-data
tools-hint  tools-list  tools-view  tab-tools
conn-badge  conn-view  tab-conn
memory-graph  skill-badge  skill-open
llm-badge  llm-open  llm-panel
pane-detach  pane-reset  pane-&lt;name&gt;  pane-body  pane-head
panel-dock  panel-float  panel-head
panel-groups  panel-pool  panel-subtabs  group-projects  group-decision  group-tools
group-brain  group-models
memory-panel  memory-view  models-view
tab-telemetry  telemetry-panel  telemetry-view
layout  menu-toggle  messages  mode-badge  mode-badge-label  model-advanced  model-note  model-select  new-folder  new-session  palette
palette-input  palette-list  panel-toggle  providers-close  providers-hint  providers-list
install-badge  providers-open  providers-panel  router-settings  search  quit-uh
remote-banner  search-results  send  session-list  session-title  sidebar
sidebar-toggle
splash  splitter-left  splitter-right  status-provider  stop  tab-keys  tab-models
tab-conn  tab-decision  tab-memory  tab-uhb  terminal-warning  theme-toggle  uhb-panel
uhb-view  version-warning  view-stale
```

`splash` is the only element that exists before the script runs: the UH mark,
the wordmark and an indeterminate bar are static markup in `index.html`, so the
first paint is not an empty page. `app.js` adds `is-done` (a CSS fade) and then
removes the node; a 4 s timer does the same if `boot()` has not resolved, so a
hung API never leaves the operator looking at a pulsing mark.

`dreams.js` mounts its own elements on activation (`dreams-open`, `idea-badge`,
`dreams-panel`), so a failed dreams route cannot stop the chat from loading.

The layout is three CSS grid columns plus two drag handles
(`splitter-left`, `splitter-right`). The handles write `--sidebar-w` / `--right-w`
on `#layout`; the widths live in `localStorage` (`uh.layout.v1`) so a reload keeps
them, and `#sidebar-toggle` collapses the sessions column (`sidebar-collapsed`).

## Serving a replacement interface

```
uh web --static-dir ./my-ui
```

The folder is mounted at `/static` and its `index.html` is served at `/`, with
the **same** token, `Host`/`Origin` checks and CSP as the bundled interface (no
`unsafe-inline`, so the page must load its script from `/static/…`). A missing
`index.html` is a startup error, not a silent fallback.

### Skills - cycle de vie (P3-15)

| Methode | Route | Role |
| --- | --- | --- |
| GET | `/api/skills/{name}` | cycle de vie d un Skill |
| PUT | `/api/skills/{name}` | cycle de vie d un Skill |
| DELETE | `/api/skills/{name}` | cycle de vie d un Skill |
| POST | `/api/skills/{name}/duplicate` | cycle de vie d un Skill |
| POST | `/api/skills/import` | cycle de vie d un Skill |
| PATCH | `/api/skills/{name}/enabled` | cycle de vie d un Skill |
| GET | `/api/skills` | cycle de vie d un Skill |
| POST | `/api/skills` | cycle de vie d un Skill |

Elements d interface : `skills-manager`, `skills-hint`, `skills-refresh`, `skills-enable-all`, `skills-disable-all`, `skill-new`, `skill-file`, `skill-editor`, `skill-editor-title`, `skill-name`, `skill-title`, `skill-description`, `skill-instructions`, `skill-save`, `skill-cancel`, `skill-error`

## Router API (`uh router serve`)

A **second, separate** application: another port (8780), another token
(`$UH_HOME/router-token`), no browser interface. Checked against the code by
`tests/test_web_api_docs.py` like the routes above.

| Method | Path | Body / query | Response |
| --- | --- | --- | --- |
| GET | `/v1/models` | — | `{object: "list", data: [{id, object, owned_by, tools}]}` |
| POST | `/v1/chat/completions` | `{model?, messages, tools?, stream?}` | `chat.completion` object, or an SSE stream |

Rules that apply to both routes: `Authorization: Bearer <router token>` only
(no cookie), `Host` must be loopback plus the bound port, no CORS header, body
≤ 1 MiB (`413`), 4 concurrent turns (`429`), and the client's `Authorization`
never reaches a provider. Errors keep the OpenAI shape:
`{"error": {"message", "type", "code", "param"}}` with `400`, `401`, `413`, `429`,
`502` or `503`.

Streaming frames:

```
data: {"id":"chatcmpl-…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"…"},"finish_reason":null}]}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

A failure after tokens were emitted sends `{"error": {"code": "stream_error"}}`
then `[DONE]`: the stream is never replayed to another provider.
