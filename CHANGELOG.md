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

## [Unreleased]

### Fixed

- **422 `missing field 'type'` après un appel d'outil refusé ou retiré.** Un tour qui n'offre aucun
  outil (`tools=[]`, mode chat après dev) rejouait les appels des tours précédents : une
  passerelle stricte refusait toute la requête. Les appels sont retirés et leur résultat reste
  lisible en note texte. Test de non-régression : refus, puis second tour contre une passerelle stricte.
- **`uh web` lancé depuis `~/.uh` ou `System32`** adopte `~/uh-workspace` (créé au besoin) au lieu
  du cwd : `write_file` n'échoue plus au premier lancement.

### Added

- `GET /api/uhb/overview` : `/status`, `/modes` et `/tools` d'UHB en une réponse.
- `uh web` suit la même chaîne de repli que `uh chat` (Ollama, Cloudflare, DeepSeek, OmniRouter,
  Router), hors 401/402/403, avec la bascule dite dans le fil. Un modèle choisi pour une
  conversation reste seul.

### Changed

- **Interface : lisibilité et accessibilité (WCAG AA).** Panneau Décision en français clair (métriques techniques dans un détail repliable) ; réglages booléens en vrais interrupteurs (`role="switch"`, 34 px) ; erreurs lisibles avec bouton d'action (Nouvelle session, Recharger, Autoriser l'outil), plus aucun code HTTP brut ; cartes d'outils à quatre états (en cours, terminé, refusé, erreur) avec `aria-busy` ; images bornées (320 px, chargement, bouton Ouvrir) ; état du second cerveau en quatre états (icône + texte + couleur) ; contrastes des bordures de contrôles, focus visible partout, piège de focus dans les modales, zones `aria-live` pour les notices système. Aucune logique backend modifiée ; CSP inchangée.

## [1.9.0] — 2026-11-19 *(non publiée : le tag suit la fusion)*

**Quatre lots et cinq correctifs de stabilisation, tous mesurés dans l'interface.**
Le fil ne ment plus, il ne meurt plus en silence, et il sait lire une capture 4K, écouter
une voix et se souvenir de ce qu'on lui a dit de garder.

### Added

- **P1-15 — audio, micro, voix.** Routes dédiées `/api/audio/*` : transcription (Voxtral 3,
  repli local `faster-whisper` CPU/int8), synthèse (Voxtral 3, repli **XTTS-v2**), lecteur
  avec **forme d'onde réelle** calculée depuis les échantillons décodés, affiché seulement
  quand un son existe. `uh/agent.py` et `uh/providers/*` n'importent jamais le paquet audio
  (test architectural). Preuve : repli réel sur un HTTP 429 Voxtral.
- **P1-14 — bouton « + » unifié, vision 4K, vidéo.** Un seul bouton range par nature (son,
  images, vidéo, documents `.txt/.md/.json/.pdf/.csv/.html/.zip`). Réduction à la volée sous
  1 Mo par image (`-sent.jpg`, l'original intact), images clés + bande son extraites d'un
  `.mp4` H.264/H.265 par le ffmpeg résolu, analyse **automatique sans prompt** quand on
  envoie un média seul. Capacité **mesurée** (`/api/show`, `input_modalities`), jamais déduite
  d'un nom ; un modèle inconnu est déclaré incapable et l'avertissement dit ce qui sera perdu,
  avec une bascule en un clic. Les octets ne partent plus vers un modèle mesuré aveugle
  (Ollama répondait 400).
- **P1-13 — mémoire des réussites.** « Garder » copie le fichier en format canonique :
  **PNG lossless à la résolution d'origine** (une capture 3000×1688 revient en 3000×1688) et
  **FLAC 24 bits / 48 kHz / 2 canaux** pour un son, indexés par SHA-256 (+ `sha256_stored`)
  et liés à leur message. Idempotent par contenu ; vider la corbeille oublie les entrées
  qu'elle contenait, « Corbeille » seule non (la fenêtre d'Annuler les détruirait).
- **P1-12 — auto-nettoyage et auto-réparation.** Purge des copies préparées en fin de tour
  (ce qui est gardé survit, via le sidecar de chaque copie), fichiers de plus de 7 jours non
  gardés, dossiers vides, et `GET /api/maintenance` qui rapporte les orphelins **dans les deux
  sens** sans jamais supprimer un fichier qu'un message mentionne.

### Fixed

- **Un tour ne finit jamais muet** : toute sortie de `run_turn`, y compris `ProviderError`,
  produit une réponse ou une notice visible, persistée en `system`.
- **Mode `chat`** : les outils interdits sont retirés de l'offre (mesuré : 8 outils, ni
  `execute_code`, ni `write_file`, ni `patch`) et un appel forcé revient refusé et typé
  `mode_forbidden`, avec une carte « Autoriser pour cette conversation ».
- **Relance bornée** (2 essais) sur du code collé en texte au lieu d'un appel d'outil, coupée
  en mode chat et dès que l'opérateur demande explicitement le code.
- **Bascule de mode protégée** : `POST /api/uhb/mode` exige le jeton, refuse le cross-site en
  **403** (Host, Origin, Sec-Fetch-Site, CSRF), et le modèle ne peut pas changer de mode.
- **Cinq mensonges d'interface du point 5** : un seul statut par artefact, « Corbeille » avec
  « Annuler » 8 s, votes `Utile`/`Pas utile` exclusifs et réversibles (journalisés, `none`
  compris), libellé honnête « noté (pas encore utilisé pour s'améliorer) », et **une seule
  source pour le modèle affiché** (`answer_meta`), y compris quand la session tourne sur un
  autre modèle que le défaut global.
- **Frame `answer` sans consommateur** trouvée par l'audit de la Porte A, et **trois défauts**
  trouvés par le banc de la Porte B : contexte Ollama sous-dimensionné pour trois images
  1536 px, bande son d'une vidéo envoyée comme image, en-tête annonçant le mauvais modèle.

### Documentation

- `docs/ARCHITECTURE.md` réécrit (contrat des 13 frames, les quatre verrous, place de
  l'audio/média, ce qui est stocké vs référencé vs mémorisé) ; `docs/VALIDATION_MATRIX.md`
  gagne la table des Portes A (GA-1 → GA-7) et B, avec les chiffres bruts.

## [1.8.0] — 2026-11-18

### Added

- **P3-14 — caractère & rôle par projet, indépendant des skills.** Sept presets (Architecte
  logiciel senior, Développeur full-stack, Analyste de données, Rédacteur produit concis,
  Auditeur sécurité, Domotique / Home Assistant, Vision / vidéo IA) plus un **texte libre**,
  dans un onglet **Caractère** du panneau de configuration et un sélecteur compact dans
  l'en-tête de session. Le rôle est stocké avec la session (`sessions.metadata["role"]`), donc
  il revient à la réouverture, et il est injecté dans le prompt système **avant** le bloc de
  skill. Preuves : `tests/test_roles.py` (9 cas) et `tests/e2e/test_p3_14_role.py` (3 cas
  Chromium). L'indépendance est mesurée, pas affirmée : fichiers de skills, `config.toml` (dont
  les serveurs MCP), toolsets actifs et schémas d'outils sont **identiques octet pour octet**
  avant et après un changement de rôle.

## [1.7.7] — 2026-11-18 *(PyPI)*

**Les graphiques se dessinent dans le chat, et l'alerte de transcription a quitté le fil.**
Deux lots, chacun avec sa preuve dans le dépôt.

### Added

- **P1-13 — une demande de graphique finit en image, pas en tutoriel** (PR #25). Le prompt oriente
  une demande de graphique, de diagramme ou de carte vers `execute_code` : le snippet écrit un
  `.png` (matplotlib, backend Agg — pas d'affichage) et l'image rejoint `message["artifacts"]`, donc
  la bulle, par le chemin navigateur déjà éprouvé. Preuve : `tests/e2e/test_p1_13_charts.py`
  (Chromium réel, image redessinée après rechargement).
- **`_tool_images()`** : UH récolte enfin les images produites par un outil (`execute_code` inline
  ce que le snippet a écrit). Seul un `data_url` inline est lu — jamais un chemin disque — et le
  MIME vient des octets (`sniff_mime`), comme pour les images du provider.

### Changed

- **P1-12 — l'alerte de transcription sort du fil de discussion** (PR #23). La fenêtre de lecture
  suit la longueur du fil (`max(200, bulles + 25)`, plafond 2 000) au lieu d'un `limit=200` fixe qui
  signalait un écart à chaque tour au-delà de 200 messages ; une relecture de 400 ms précède tout
  verdict (le worker commit la ligne juste après `done`) ; et un écart réel s'affiche en contrôle
  discret dans l'en-tête (« Actualiser la vue ») au lieu d'une carte rouge entre deux messages.
  Aucun message, artefact ni dossier `+ Folder` n'est touché.

### Fixed

- **Le base64 ne repart plus au modèle.** Le message `role: tool` gardait `images[].data_url` : un
  graphique de 14 Ko représentait des milliers de tokens d'entrée au tour suivant, et s'affichait en
  clair dans la carte d'outil. Les octets vivent désormais dans les artefacts de la réponse, le
  message outil ne garde qu'un marqueur.
- **Plus de graphique en double après une réponse vide.** Le nudge qui relance un modèle muet
  normalisait deux fois la même fenêtre : les empreintes d'images du tour empêchent la seconde
  émission (deux images *différentes* restent deux images).

## [1.7.6] — 2026-11-17

**`--provider X` choisit maintenant son propre modèle, et un 401 dit lequel des deux a échoué.**
`uh chat --provider omnirouter "PING"` héritait du `default_model` global (« deepseek-flash »),
un id DeepSeek qu'OmniRoute ne connaît que comme nom de fournisseur sans identifiants : il
répondait `401 provider_not_configured` et UH affichait « clé OmniRoute refusée ». La clé était
valide — mesuré : `/v1/models` → 200 avec la clé du store et celle de l'environnement.

### Fixed

- **`--provider <kind>` sans `--model` prend le modèle du provider demandé.**
  Le `default_model` de la configuration décrit la chaîne par défaut, pas un provider choisi
  explicitement. Résolution : un `--model` explicite gagne toujours ; sinon `omnirouter_model`
  pour la passerelle, le premier tag local pour Ollama, et le `DEFAULT_MODEL` du provider
  lui-même pour DeepSeek, Cloudflare et HuggingFace (`_provider_default_model`, `uh/cli.py`).
- **Un 401/403 de la passerelle nomme la vraie cause.** Le corps est lu (`error.code`) :
  `provider_not_configured` donne « OmniRoute n'a pas d'identifiants pour le fournisseur amont
  `<nom>` » et le détail du corps ; les autres codes gardent le message sur les fichiers de clé.
  Même traitement dans `list_remote_models` (`uh/providers/omnirouter.py`).

### Tests

- `tests/test_requested_provider_model.py` (8 cas) : modèle propre au provider, `--model`
  explicite prioritaire, Ollama via le premier tag local, et le cas mesuré de bout en bout.
- `tests/test_auth_failure_message.py` (4 cas) : `provider_not_configured` n'est plus imputé à
  la clé, `invalid_api_key` continue de pointer les fichiers de clé, corps illisible compris.

## [1.7.5] — 2026-11-17

**Un snippet n'est plus coupé, et il demande la permission.** Cette version ferme P0-4 :
le budget transport d'`execute_code`, une approbation explicite de l'opérateur, la frontière
réelle du bac à sable mesurée, et une page d'installation qui nomme enfin les paquets qui
contraignent Pydantic.

### Changed

- **`docs/INSTALL.md` cesse d'accuser les mauvais paquets.**
  ([#16](https://github.com/Antoine-Thomas/UH-Universal-Harness/pull/16)) Le plafond conseillé
  `pydantic<2.12` contredisait les paquets qu'il était censé satisfaire : mesuré sur
  l'interpréteur partagé qui a déclenché le conflit, les contraintes **actives** viennent de
  `mcp` et `mcp-types` (`>=2.12.0`) et de `google-genai` (`>=2.12.5,<3`). La plage documentée
  devient `pydantic>=2.12.5,<3`, présentée comme le **minimum mesuré là-bas** et non comme une
  exigence d'UH : un venv propre résout `2.14.3` seul, et UH fonctionne de `1.10` à `2.14`
  parce qu'il n'importe jamais Pydantic. `mixpanel` est disculpé (aucune dépendance à
  Pydantic), `gradio` ne contraint que par son extra `mcp` (absent d'une installation de
  base), `litellm` accepte `>=2.11.0,<3`. Le conflit `google-antigravity` ⇄ `mcp<2.0`,
  indépendant de Pydantic, est documenté séparément — une montée de Pydantic n'y changerait rien.

### Added

- **Une approbation explicite avant toute exécution de code.**
  ([#15](https://github.com/Antoine-Thomas/UH-Universal-Harness/pull/15), avec
  [UHB #17](https://github.com/Antoine-Thomas/UH-Universal-Harness-Backend/pull/17)) Le
  snippet tourne en tant qu'utilisateur de l'opérateur : l'environnement est nettoyé, le
  système de fichiers ne l'est pas. UH demande donc l'autorisation sur le flux du tour,
  affiche le code, le mode et le délai, et n'envoie **rien** à `/tools/execute` si l'opérateur
  refuse. UHB détient la permission : nonce à usage unique (120 s, en mémoire, lié à
  `execute_code`), consommé même quand l'exécution échoue, et un corps qui prétend
  `approved: true` est ignoré. « Toujours autoriser » ne vaut que pour la session et pour
  `execute_code`, jamais pour l'ensemble des outils UHB.
- **`scripts/diagnose_pydantic.py`, un diagnostic reproductible.**
  ([#16](https://github.com/Antoine-Thomas/UH-Universal-Harness/pull/16)) Lecture seule, aucune
  installation dans l'environnement courant : interpréteur, contraintes sur `pydantic` avec
  leur marqueur évalué (active, ou derrière un extra non installé), intervalle qui les satisfait
  toutes, versions publiées compatibles, `pip check`, et `--clean` pour éprouver deux venvs
  jetables. Code de sortie `1` en cas de conflit. Il vit dans le dépôt, pas dans le wheel :
  les notes d'installation disent comment le récupérer.

### Fixed

- **Un snippet autorisé à courir 10 s n'est plus coupé à 5.**
  ([#13](https://github.com/Antoine-Thomas/UH-Universal-Harness/pull/13)) UHB ne répond
  qu'une fois l'enfant terminé — tué au timeout, artefacts scannés. Un budget client égal au
  délai demandé coupait donc un travail correct et affichait « uhb unreachable ». Le budget
  devient `timeout demandé` (borné par le `default` et le `maximum` que le catalogue publie
  dans le schéma de l'outil) plus une marge, **pour `execute_code` uniquement** : les autres
  outils gardent leurs 5 s, et un timeout dépassé revient comme un résultat `timed_out`
  structuré, pas comme une exception.

### Notes

- Zéro changement de dépendance : aucune borne Pydantic n'est ajoutée aux métadonnées d'UH ni
  de UHB. La plage documentée dans `docs/INSTALL.md` concerne un environnement partagé ; elle
  n'est pas imposée par le paquet.

## [1.7.4] — 2026-11-16

**Le réseau, le routeur, et deux contrôles qu'on ne voyait pas.** Cette version livre
P1-9 et P1-10 (déjà sur `main`, jamais publiés) plus OmniRoute et deux défauts d'interface
constatés sur une instance réelle.

### Fixed

- **Une coupure de transport coûte une tentative, plus un tour.** Chaque provider réessaie
  3 fois (0,5 s puis 1 s) une erreur de transport ou un timeout, **jamais après un token
  déjà émis** ; une réponse coupée est conservée, marquée `_[réponse interrompue]_`,
  persistée, et la bulle offre « Continuer ». La chaîne s'arrête net sur 401/402/403
  (une clé morte ne doit pas être masquée par une réponse venue d'un autre compte) et
  continue de basculer sur 400/404 (modèle sans support d'outils), avec une `notice` qui
  nomme la bascule. Mesure : 20 tours, une tentative sur deux forcée en coupure →
  **0 tour perdu, 40 appels, 20 réponses, aucun doublon**.
- **OmniRoute refonctionne depuis UH.** `resolve_api_key()` ne lisait jamais le store d'UH :
  une clé saisie dans le panneau Fournisseurs était ignorée, et UH retombait sur
  `~/.omniroute/.env`, dont la clé répond 401 sur `/v1/models`. L'ordre est désormais
  env → **store UH** → dotenv, et les 401/403 expliquent où la clé se règle. Mesuré sur le
  gateway réel : `eco` → `200`, `list_remote_models()` → 1 619 identifiants.
- **L'onglet Décision n'est plus vide.** Ouvert par deep link, il chargeait avant qu'une
  session existe et ne se rafraîchissait jamais : il suit maintenant la session ouverte,
  et distingue en français « aucune session » de « aucun tour enregistré ».
- **L'interrupteur on/off est visible.** La case de 13 px « Paramètres personnalisés »
  devient un bouton `role="switch"` à contraste AA, avec `aria-checked` et un libellé
  qui dit « activés » / « désactivés ».

### Added

- **Images renvoyées par une API.** Blocs `image_url` / `image` / `input_image` / base64 et
  images markdown deviennent des artefacts, dans le même canal que les images d'outils :
  téléchargement **côté serveur**, http(s) seulement, aucune adresse locale littérale,
  redirection refusée, plafond 2 Mio, MIME aux magic bytes, `svg` exclu. Un refus produit
  une ligne lisible. Les octets vivent avec la réponse (`messages.artifacts`, colonne
  tardive, migration en place) et ne partent jamais vers un provider.

### Notes

- La vision long terme du produit (fusion UH+UHB, dépôt `UHD-Universal-Harness-Desktop`,
  packaging multi-OS) est inscrite dans `docs/ROADMAP_UH_V2.md`, section **P4**.
- `1.7.2` était une montée de version de CI (Playwright) sans entrée de changelog.

## [1.7.3] — 2026-11-16

**Ce que la fenêtre disait, et ce qu'elle faisait.** Trois défauts constatés sur une
instance réelle, plus un quatrième trouvé en écrivant le test qui devait prouver le
contraire.

### Fixed

- **Un rouge qui accusait le modèle alors qu'une commande échouait.** `/web` interroge
  DuckDuckGo en HTML ; la connexion est coupée (`ConnectError: [WinError 10054]`) et le tour
  mourait en 174 ms, `tools=0`, sans qu'aucun provider soit appelé — mais la bulle disait
  « Erreur du modèle ». Le toolset réessaie désormais une erreur de transport, un 5xx ou un
  429 (3 tentatives, 0,4 s puis 0,8 s) et jamais un 4xx ; la trame `error` d'une commande
  porte `source: "command"`, que l'interface traduit par « Commande : … ».
- **Un avis qui demandait une action absente de l'écran.** « Recharge la session » était une
  phrase, pas un contrôle : la réconciliation du transcript porte maintenant un bouton
  « Recharger la session » (contraste AA, focus visible) qui recharge en place et annonce
  le résultat. La règle « une capacité visible s'active depuis l'interface » vaut aussi pour
  un message d'erreur.
- **Les octets d'une image payés par le modèle.** Le payload `artifacts` (data URL comprise)
  faisait partie du message `role: tool` envoyé au provider : ~10 000 tokens par image de
  40 Ko, à chaque tour suivant, pour une image qu'un modèle texte ne lit pas.
  `request_messages` retire `artifacts` du fil — la session garde les octets, le modèle garde
  le marqueur `[image: image/png, N base64 characters, artefact 1]`.
- **La suite de tests arrêtait le backend de l'opérateur.** `stop_running()` interroge un
  vrai UHB avant l'arbre de processus : lancer la suite là où `uh web` sert son UHB tuait ce
  UHB. `request_shutdown` est injecté dans les cinq tests qui l'atteignent, et un second test
  ne compare plus le catalogue d'outils à un littéral de quatre noms.

### Added

- **Images en artefacts (P0-5).** Un résultat d'outil MCP peut porter des images : octets
  conservés et bornés (512 Ko décodés par image, 4 par retour, `omitted: "too_large"` au-delà),
  rendues par `img.src` après contrôle du préfixe `data:<mime autorisé>;base64,`, MIME en
  liste blanche (svg exclu), aucun `innerHTML`. Le `data_url` vit dans le message, donc un
  rechargement de session redessine l'image sans redemander au serveur.
- **La chaîne complète est prouvée** : réponse de provider avec tool call → enfant MCP stdio
  réel → artefact sur le flux SSE → store → relecture de session (`tests/test_image_artifacts_e2e.py`).

### Notes

- `1.7.2` était une montée de version de CI (Playwright) sans entrée de changelog ; elle ne
  contenait aucun des correctifs ci-dessus.

## [1.7.1] — 2026-11-15

**The lifecycle, not the transcript.** Every symptom of the 1.7.0 round came back one
level lower: the token, the cookie, the port and the end of a stream. No tag until the
maintainer says so.

### Fixed

- **A 401 no longer erases the page.** The token of another instance (or a restart)
  used to replace the whole document with one sentence: transcript, composer and
  draft included. The message lands in a banner, the page stays.
- **The token survives a restart.** `$UH_HOME/web-token` (0600 on POSIX, profile ACL
  on Windows) replaces the per-process random token; `uh web --rotate-token` replaces
  it on purpose. The cookie is `uh_token_<port>`, so two instances no longer share
  one authority.
- **A message is never lost before the turn is accepted.** Enter and the button go
  through one `submitComposer()`; the field is emptied on the server's `open` frame;
  a message during a turn is refused visibly and keeps its text; a silent stream
  ends after 45 s with the draft restored (`UH_STALL_MS`).
- **Every stream ends.** A `ping` frame every 15 s, a 10-minute cap, a guaranteed
  `done`; `boot()` isolates each step so one failure no longer cancels the rest.
- **One instance per port.** `/healthz` lets a second `uh web` recognise the first
  (print its URL, exit 0) instead of silently serving another copy on the next port;
  the fallback is opt-in (`--port-fallback`), and `--quit-existing` stops the other.
- **A second brain that cannot be orphaned silently.** A launched UHB is recorded in
  `$UH_HOME/uhb.pid`; "Quitter UH" stops a recorded one whoever started it, after
  checking the command line.

### Added

- **Turn journal** in `$UH_HOME/uh-web.log` (rotated): one line per turn event with
  turn id, tool name, duration, `ok`/`error` and the ending reason
  (`done`/`error`/`deadline`/`stopped`). Never a prompt, an answer, an argument or a
  key.
- **Delete a stored API key** from the panel (the route existed, the button did not),
  with a confirmation naming the mask; the value never reaches the browser.
- **Which copy is running**: `/api/status` exposes `install`, the footer shows
  `dépôt` or `paquet installé`, and `uh doctor` warns when the command comes from
  site-packages while the folder is a checkout.
- **CI e2e job** (ubuntu + windows): the Playwright suite that catches "the product
  breaks while every unit test stays green". Advisory until ten green runs.

## [1.7.0] — 2026-11-15

**One door per subject, honest errors, and an agent that knows its own setup.**

### Changed

- **One place to connect anything.** `+ Connecter un MCP` left the `Données` tab:
  `Connexions` is the only door to a cloud storage, an MCP server or an IDE, and
  `Données` is the local maintenance room (backup, skills, installed tools, repair
  UH). An old `?panel=providers` link still lands on `Fournisseurs`.
- **No second button in the local-model table.** The row itself selects the model,
  the same move as the header menu; `Providers` merged into `Fournisseurs` (one
  subject, one tab), and the tab strip wraps instead of hiding titles.
- **The header switches a pair, not a name.** The menu lists local models *and*
  every API provider that holds a key, badged 💻 / ☁️, and sends
  `{provider, model}`: choosing `deepseek-v4-pro` while `qwen3:8b` was active used
  to leave the provider behind ("faster, not smarter") or send an Ollama id to an
  API and come back as a bare `HTTP 500`.
- **`SYSTEM_MAP.md` gained an `## Architecture` section** - UH is the cockpit, UHB
  the optional backend on port 8101, local models run through Ollama on 11434 - and
  one condensed line of it rides in the brief the model reads on every turn.
- **The prompt names the doors.** A UI question is answered with a tab and a deep
  link, never with a file edit; `Connexions` is the only place for a connection,
  and `Données` is never offered for one. When a tool exists, the agent calls it
  instead of printing code to copy.

### Fixed

- **Ollama failures are sentences.** `refusé : HTTP 500` on a model row became
  "non chargé : Ollama n'a pas pu le mettre en mémoire (VRAM ou délai)", with what
  to do next; a model that is not installed is a `400` that says so, and a
  read-only `config.toml` is a `409` naming the cause.
- **`POST /api/model` refuses a contradictory pair**: a local id with an API
  provider, an uninstalled Ollama model, or an unknown provider - each with the
  sentence that resolves it, instead of caching an impossible state.
- **The dev environment no longer shadows the repository.** A non-editable
  `uh-harness` in the venv won the import race over `./uh` when the gate ran
  `pytest.exe`, so the release gate could test code that was not being pushed.
  Fixed by an editable install, and `tests/test_dev_env.py` now fails loudly if it
  ever happens again.


### Added

- **`+ Connecter un MCP`** (in `Connexions`, its only home): one popup for
  `stdio`, `http` and `sse`, with a `.json` / YAML-subset import, a five-second
  probe (`POST /api/mcp/probe`, writes nothing) and an `Enregistrer` button that
  stays disabled until the probe answers `connected`. Editing a field after the
  test disables it again.
- `POST /api/mcp/import`: reads a Claude-Desktop-style JSON (`mcpServers`), a bare
  server, or a list, plus the documented YAML subset; refuses what it cannot read
  with the line number instead of half-parsing it.

- **MCP over HTTP.** `uh/mcp_http.py` speaks Streamable HTTP and the legacy
  HTTP+SSE stream Positron publishes: `initialize`, `tools/list`, `tools/call`,
  `ping`, sessions, per-request timeouts and the same failure kinds as the stdio
  client. A configured entry is tested from "Connexions" - and by the save
  itself: `connecté · 3 outil(s) · 187 ms`, `jeton refusé`, `injoignable` or
  `protocole incompatible`, each with the sentence that says what to change.
  A dropped stream is detected and retried with a 1-2-4…60 s backoff.
- **Per-call approval for remote tools.** No approver, no tools: every
  `tools/call` on an HTTP/SSE server asks first (an `approval` frame in the turn's
  stream, Autoriser / Refuser in the cockpit); an unanswered ticket is a refusal.
  A console turn exposes no remote tools for the same reason.
- `uh web --allow-remote` is the only way to bind beyond loopback; `--host` alone
  is refused.
- `UH_DEV_RELOAD=1` restarts the worker on a Python change, so a route added after
  the server started no longer answers 404 while working on the UI.
- **The cockpit opens before any model exists.** No modal on first launch: a
  discreet banner offers `Configurer une IA`, `J'ai une clé API` or `Plus tard`
  (remembered), and the chat, the files and the memory already work.
- **`IA locale` is its own tab** (yellow guide): Ollama status, the official
  installer link (UH installs nothing), one-click model choice, TGI detection, and
  a pointer to `Clé API`. `Clé API`, `IA locale` and `Connexions` are the first
  three tabs; `Connexions` now says "Stockage cloud (dossiers)" so a storage
  account is never mistaken for a model key.

### Security

- A user-typed MCP URL goes through `uh/urlguard.py`: http(s) only, no
  credentials in the address, no fragment, and plain HTTP to a non-loopback host
  is refused unless explicitly allowed. Redirects are never followed.
- `mcp-secrets.toml` is restricted with `icacls` on Windows (`chmod` is a no-op
  there); a widened file is narrowed again when it is read.
- The MCP token is masked in every answer (`••••` plus its last four characters)
  and redacted from error messages, including a server's own stderr.
- A cookie-authenticated `POST`/`PUT`/`PATCH`/`DELETE` needs a same-origin proof
  (CSRF / DNS rebinding), and `Sec-Fetch-Site: cross-site` is refused outright.

### Fixed

- A 401 while opening the SSE stream crashed the test with a `NoneType` instead of
  reporting "jeton refusé".
- `/api/tools/status` no longer says "nothing configured" next to a configured
  HTTP server.

## [1.6.1] — 2026-11-14

### Fixed

- **1.6.0 does not import on Python 3.10.** `uh/mcp_cloud.py` imported `tomllib`
  directly; it joined the standard library in 3.11. The module now goes through
  `uh._compat`, which falls back to `tomli` (already a conditional dependency), as
  every other TOML reader in the codebase does. The 3.10 jobs of the CI matrix
  caught it on the release commit - the wheel published as 1.6.0 is unusable on
  3.10, and this is the fix. `python3.10 -c "import uh.cli, uh.web.app"` passes.

## [1.6.0] — 2026-11-14

Three pillars, one release: a provider that needs no Ollama, a harness that can
propose a patch on its own code, and a cloud connection a non-developer can set up.

### Added

- **Hugging Face, first-class.** `uh/providers/huggingface.py` speaks
  `POST /v1/chat/completions` and resolves its base URL in this order:
  `UH_HUGGINGFACE_BASE_URL`, a **local TGI on `127.0.0.1:8080`** (probed with a
  socket, no token, no account), then the router `https://router.huggingface.co/v1`.
  The legacy `api-inference.huggingface.co` per-task API is never used - it has no
  chat roles, and a chat loop pointed at it fails on the first turn.
- **`uh fix`: a patch must apply before anyone believes it.** The answer must start
  with `diff --git`, it must pass `git apply --check`, `--apply` works on a
  `fix/auto-<stamp>` branch, and a failed `--check` command **reverts** the patch.
  Nothing is ever committed or pushed by the harness.
- **"Réparer UH" in the Data panel** with a diff modal: propose, read the diff,
  apply on a branch or reject. Proven by driving the real window
  ([screen](https://github.com/Antoine-Thomas/UH-Universal-Harness/blob/main/docs/assets/self-repair.png)).
- **Cloud connections from the UI** - AWS S3 (`put_object`, 7 tools) and Google
  Drive (read-only), with the `self-repair` read-only filesystem template. Secrets
  go to `mcp-secrets.toml` (0600), never to `config.toml`.
- **First run asks once**: `GET/POST /api/setup` offers Hugging Face, Ollama or a
  cloud key, and the screen appears only when nothing can answer a turn.

### Fixed

- The Data panel threw `data is not defined` whenever `/api/tools/status`
  succeeded: the MCP block read `data.mcp` outside the `try` that declared it, so
  the whole panel (MCP, Cloud, Réparer UH) was replaced by an error line.
- `uh fix` could not reach a configured provider (`build_provider("")`), and the
  file list came out as `/__init__.py` instead of `uh/__init__.py`.
- A diff written from Python was translated to CRLF on Windows, and a patch whose
  lines end in `\r` does not apply: `newline="\n"` on every patch file.

## [1.5.1] — 2026-11-14

### Added

- **`uh skills`** lists what is installed; **`uh skills --compress`** groups the
  near-duplicates (difflib over titles and descriptions, no model) and merges each
  group into its longest member. It is a dry run unless `--write`, and the absorbed
  files are *moved* to `skills/.merged` rather than deleted: a bad merge is one
  `mv` away from undone. Two skills with empty descriptions are never grouped -
  difflib calls them identical, and that would merge every undocumented skill.

### Fixed

- The 11 MB MCP test waited on a 15 s budget to read a line through a pipe, which
  takes ~20 s on a loaded machine: it measures "refused, not buffered", so the
  budget is 60 s and the reason is written next to it.

## [1.5.0] — 2026-11-14

The harness stops guessing about itself.

Every path in the map is generated from the function that owns it, so the answer
cannot drift from the machine.

### Added

- **`uh map`** - the system map: chats (`uh.db`), dreams (passes / archived /
  actions), outputs, skills (yours and built-in), workspace, models. `--write` drops
  `$UH_HOME/SYSTEM_MAP.md`. A compact version rides on the system prompt of every
  turn, before the skill's instructions, so "where are my dreams?" gets a path and
  not an invention.
- **`outputs_dir`** - a real delivery folder (`config.toml`, default
  `$UH_HOME/outputs`). "Exaucer" now writes its plan there as well as keeping the
  record in `dreams/actions`: a plan the user cannot find is a plan that never
  happened.
- **`sessions` dream source** - the night pass reads the last 20 session titles and
  their message counts. Titles, not bodies: the pass runs unattended at 03:00.

### Changed

- The system prompt is now: role, map, skill - in that order, so the task stays the
  closest thing to the user message.

## [1.4.4] — 2026-11-14

The patch for installing and updating on Windows, which was the hardest part of
using it.

### Fixed

- **`uh upgrade` finishes.** On Windows a running `uh web` holds `uh.exe`, so pip
  answered `WinError 32` - and the process doing the upgrade *is* `uh.exe`, which
  pip cannot replace while it runs. It now stops the other UH processes, purges
  pip's cache, installs with `--force-reinstall --no-cache-dir`, refreshes the
  Desktop shortcut, and - when it is running as `uh.exe` - hands the install to a
  detached console and leaves first. `--no-clean` keeps the old plain behaviour.
- **The Desktop launcher stops a previous `uh web`** before starting one: the port
  and the executable belong to it, and a second start used to die on bind.
- **DeepSeek is listed in Providers before it holds a key.** The routes existed
  while the catalogue did not name the provider, so the list that adds a key had
  no row to add it from - the provider appeared only after a key already existed.
- The isolation guard in the test suite ignores the files a live process writes
  (`dream-daemon.status`, the logs), so a daemon ticking during a run no longer
  looks like a test writing into `~/.uh`.

## [1.4.3] — 2026-11-14

The patch after the first hour of using the cockpit: fewer choices on screen, and
a second brain that says it once.

### Changed

- **One model in the header.** The selector listed every installed model; it now
  shows the current one, and a "⋯" button (persisted per browser) reveals the
  full list. `default_model` in `config.toml` decides which one is current.
- **The Second brain panel says "connected" once.** A dot, a version and a mode
  instead of a paragraph, with the harness/providers/modes behind "Détails" -
  the explanation only appears when something is wrong.

## [1.4.2] — 2026-11-14

The patch for the first hour of real testing: an old `uh web` no longer hides in
the port, and the window opens like a program.

### Fixed

- **A busy port is no longer a dead end.** A `uh web` from an earlier session kept
  `127.0.0.1:8765`, so every new launch died on bind - uvicorn's exit code 3, and
  a popup with no reason. Because the interface is read from disk on every
  request, that stale server served the *new* `app.js` with an *old* backend: the
  panel called `/api/uhb/start`, got a 404, and said "Not Found" on a button that
  exists. `uh web` now probes the port and, on the default one, moves to the next
  free port and says so; with an explicit `--port` it refuses with one clear line.
- **The page is stamped with its build** (`app.js?v=1.4.2`,
  `data-uh-version`), so a cached script cannot outlive a restart, and `/api/status`
  is compared at boot: a mismatch shows a banner naming both versions.
- **A 404 from an API route explains itself** ("ce serveur UH ne connaît pas cette
  route : une ancienne instance occupe encore le port") instead of "Not Found".
- **The Desktop launcher is refreshed by `uh web`** when it predates `--app`: an
  upgraded installation used to keep a shortcut that opened a browser tab forever.
  A hand-edited launcher is left alone.

### Added

- The app window opens **centred** (`--window-position` computed from the primary
  screen at launch), so it reads as a program rather than a popup in a corner.

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
