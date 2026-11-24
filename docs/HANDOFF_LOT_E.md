# Handoff — après le lot E, avant le lot 3

Écrit le 24/11 sur `main` `90a5763`, pour un `/new`. Ce fichier donne l'état, les
preuves et l'ordre des chantiers ; il ne raconte pas l'histoire. Il est la
référence de la **partie B** du prompt « lot 3 ».

Son contenu précédent (l'état d'avant le lot E) reste dans l'historique git :
tout ce qu'il listait comme à faire est livré, et la section 2 le nomme.

## 1. Le dépôt, tel qu'il est

```text
branche   main, à jour sur origin/main
HEAD      90a5763  fix(ui): three distinct labels for the provider badge  (24/11 01:40)
arbre     propre (git status --short ne renvoie rien)
poussé    tout ; aucune branche locale en avance sur origin/main
restes    backup/pre-amend-c006690, docs/update-roadmap-post-pr44 : anciennes,
          non fusionnées, non poussées, laissées telles quelles
venv      .venv, Python 3.10.11, `uh` importé en éditable depuis le dépôt
```

Les retours du 24/11 viennent de la **capture** de l'opérateur (pied de page
« paquet installé · 1.9.2 »), pas de ce dépôt : voir G6 et la section 5.

## 2. Livré depuis v1.9.2 — vérifié dans le code, pas dans les messages

| Commit | Ce que le code fait maintenant | Où |
|---|---|---|
| `71c35d0` | Chaîne de moteurs web : Brave → Tavily → SearXNG → DuckDuckGo HTML. Un moteur sans clé n'est **pas candidat** (aucun appel réseau) ; un échec n'arrête pas la chaîne et son nom reste dans `failed`. `/web image` n'a qu'un moteur : Brave Images. | `uh/toolsets/web_search.py` (`engine_chain` :213, `search` :452, `image_engines` :494, `search_images` :502) |
| `c5b8f8d` | Textes des panneaux en français, bordure de contrôle remontée à 4,5:1. | `uh/web/static/index.html`, `style.css` |
| `9da68d3` | Un dessin produit par `execute_code` est étiqueté : `ILLUSTRATION_NOTE` voyage avec l'artefact (sous le cadre) **et** avec le message d'outil que le modèle relit. | `uh/images.py:45`, `uh/console.py:900-940` |
| `6824770` | Garde VRAM **relue à chaque dictée** (`MIN_FREE_VRAM_MB` = 1 536) ; sous le seuil, ou si `nvidia-smi` ne répond pas, la dictée passe en CPU `int8` et le panneau nomme les Go mesurés. | `uh/voice_gpu.py`, `uh/audio/local.py` |
| `7e16856` | Le micro est libéré à l'arrêt de l'enregistrement, pas à la réponse du réseau. | `uh/web/static/app.js` |
| `39e2a0d` | Une bulle en échec ne garde ni vote ni « Écouter ». | `uh/web/static/app.js` |
| `90a5763` | Trois libellés distincts pour la pastille fournisseur (au lieu d'un seul). | `uh/web/static/app.js` |
| `0601fe6` | L'e2e attend la disparition du splash avant de cliquer dans la suite micro. | `tests/e2e/` |

## 3. Mesuré

- **Suite unitaire** : `2114 passed, 2 skipped, 104 deselected` en 415,95 s
  (`…/hermes/cache/scratch/uh-suite-codex.log`, 24/11 01:06). Le log précède le
  commit `90a5763` de 34 min ; celui-ci ne touche qu'un libellé de pastille et
  n'a pas été rejoué.
- **e2e en CI, 23/11 19:52** : `1 failed, 96 passed, 3 skipped`. L'échec est
  `tests/e2e/test_p3_14_role.py::test_a_preset_chosen_in_the_header_survives_a_reload`
  (`Page.expect_response: Timeout 30000ms exceeded`), `scratch/e2e49.log`.
  **La CI était rouge à cette date** ; une attente explicite existe depuis
  `1d6be9d` (PR #44), la mesure n'a pas été refaite.
- **e2e local, 23/11 15:06** : `99 passed` en 13 min 39 (`uh-e2e-d2.log`), avant
  les derniers commits de la pile.
- `ruff check`, `ruff format --check`, `mypy` : verts à la dernière passe du lot E,
  aucune mesure refaite le 24/11.

## 4. Non mesuré — à ne pas présenter autrement

- **Les 4 combinaisons CPU/GPU × base/small** sur une vraie phrase, chat chargé :
  le script existe (`scratch/bench4.py` : `cpu_base`, `cpu_small`, `gpu_base`,
  `gpu_small`) et n'a **jamais rendu** (cache Hugging Face stable 45 min, GPU
  occupé par le chat ; processus arrêté par l'agent).
- Seules **deux** mesures existent, sur une **tonalité de 10 s**, donc en débit :
  CPU `int8` 4,21 s, GPU `int8_float16` 0,96 s, VRAM 2 008 Mo (delta 278 Mo),
  `scratch/bench-out.json`. Ce n'est pas une mesure de qualité.
- La fidélité de transcription sur de la parole réelle : une phrase SAPI française
  a été générée (`scratch/sapi-fr.wav`), le banc n'a pas été lancé.
- Openverse, Wikimedia Commons, oEmbed YouTube : **aucun code, aucune mesure**
  (voir G2, G3).
- Le relevé 3 du registre micro reste non mesuré ; `docs/HANDOFF_POINT0.md` le dit.

## 5. Pièges payés une fois chacun

- `basetemp` de pytest **hors dépôt** ; jamais `uv run` dans ce dépôt (il a déjà
  vidé une partie du venv) ; `PYTHONPATH` vide.
- e2e instables connus : rechargement de rôle (`test_p3_14_role.py`) et splash.
  Toujours un `UH_HOME` jetable pour les tests ; le vrai `~/.uh` ne se touche pas.
- **Deux UH sur cette machine** : une copie réelle dans
  `%LOCALAPPDATA%/Programs/Python/Python310/Lib/site-packages/uh` (1.9.2,
  installée le 22/11 17:10) et le dépôt importé en éditable depuis `.venv`. Le
  `uh` du `PATH` est `Python310/Scripts/uh.exe` (`uh --version` → `1.9.2`) :
  c'est la copie du 22/11 que lance le raccourci (G6).
- **Piège de diagnostic** : `python -c "import uh"` lancé **depuis** le dépôt
  importe le dépôt, le répertoire courant étant sur `sys.path`. Mesurer hors du
  dépôt ou avec `-P` — c'est ce qui a failli faire conclure « le raccourci sert
  déjà le dépôt ».
- **8 processus `uh web` résiduels** (18/11 → 22/11) sont encore vivants, lancés
  en `-m uh web`. Le filtre d'auto-nettoyage du lanceur cherche `-m uh.cli web`
  (`uh/shortcut.py:184`) : il ne les voit pas. Personne ne les a tués ; ne pas les
  tuer sans GO.
- `tests/test_shortcut.py:273-279` asserte `-m uh.cli web` — chaîne qui
  n'apparaît **que** dans ce filtre CIM, jamais dans la ligne de lancement
  (`& $uh.Source web --app`, `uh/shortcut.py:195`). Aucun test ne lie donc le
  raccourci à un interpréteur : c'est le trou que G6(c) doit fermer.

## 6. Lot 3 — ce que la lecture du code confirme

- **G1** confirmé. `linkTabs` (`markdown.js:31-49`) remplace les URL nues par des
  jetons avant de traiter les noms d'onglet, puis les restaure **en texte**
  (ligne 46) : seuls `[texte](url)` (:61-67) et les noms d'onglet (:18-29)
  deviennent des liens.
- **G4** confirmé, et plus large que la capture. `markdown.js` n'a aucune règle
  image. Côté serveur, `MARKDOWN_IMAGE` (`images.py:39`) n'accepte que `http(s)`
  et `_from_markdown` (:267) récupère les octets **sans retirer la référence** :
  pour `![x](https://…)`, le texte brut reste affiché **en plus** de l'image.
  Une cible relative (`cochon.png`) n'est reconnue par personne : texte brut seul.
- **G2** confirmé. `search()` renvoie `{title, url, snippet}` et la bulle
  l'affiche en texte ; `image_engines()` ne rend que `brave`. Aucune route de
  vignettes dans `uh/web/app.py` (zéro occurrence de `thumb`). La brique SSRF
  existe : `uh/images.py::check_url` / `_host_is_local` (:109-145) — redirections
  refusées, 2 Mo, MIME par octets magiques, SVG exclu — mais **l'hôte littéral
  seulement**, avec un commentaire assumé (« a hostname we do not resolve »).
  Le proxy du lot 3 doit résoudre le DNS puis épingler l'IP : cela contredit ce
  commentaire, et le changement doit être écrit, pas glissé.
- **G3** confirmé par absence : zéro occurrence de `youtube`, `oembed`, `ytimg`
  dans `uh/`.
- **G5** confirmé : le rendu passe par `execute_code` + matplotlib
  (`uh/intent.py` route les dessins), le planificateur (roadmap, lot 2 point 5,
  lignes 2166-2195) est toujours non coché, seul `ILLUSTRATION_NOTE` existe.
- **G6** confirmé et mesuré (section 5). La roadmap le savait déjà
  (lignes 126-128 : « the shortcut currently runs whatever `uh` is on PATH »,
  `--from-checkout` proposé — absent du code, `grep` vide).
- **G7** vérifié : « masquage de la console du raccourci Windows » est **livré**
  (`uh/shortcut.py:404-406`, `-WindowStyle Hidden` ; `tests/test_shortcut.py:157` ;
  roadmap lignes 70 et 91). « Parité de fallback des sessions web » est
  **introuvable** : zéro occurrence de « parité » dans `docs/ROADMAP_UH_V2.md` ;
  les seules entrées sont `docs/JALON7.md:127` (commandes console/CLI) et
  `docs/SLASH_COMMANDS.md:63` (console/CLI/web sur `providers_admin`) — rien sur
  un fallback de sessions web.

## 7. Ordre et règles du lot 3

`G0` (CI e2e verte : 1 échec le 23/11, `test_p3_14_role.py`, cause à trouver,
pas de `sleep` ni de relance) → `G1 + G4` (petits, sûrs) → `G6` (note +
procédure) → `G2 + G3` (surface réseau neuve, relecture sécurité **avant** le
code) → `G5`.

Cette branche (`docs/handoff-lot3`) est **locale** : le `pre-push` du dépôt
refuse tout push sans `UH_PUSH_OK=1` posé par l'opérateur dans son propre
terminal, et le laisser poser par l'agent annulerait la barrière installée après
l'incident du 19/11. C'est l'opérateur qui pousse, ouvre la PR et fusionne
depuis l'interface web de GitHub.

Une branche par point, un commit par sujet, contre-épreuve pour chaque test
(le test échoue avant le correctif), suites complètes + `ruff` + `mypy`, captures
clair et sombre, tout texte visible en français.

Inchangé : aucun push, aucun `--no-verify`, rien toucher à l'authentification ni
à `~/.gitconfig`, `gh` non relancé, aucun processus tué qui n'ait été lancé par
l'agent, `basetemp` hors dépôt, jamais `uv run`, `PYTHONPATH` vide, ne promettre
que ce qui est mesuré, aucune écriture dans les compétences / la mémoire / le
profil sans GO explicite.

## 8. Repères

- Dépôt `C:/Users/searc/UH-Universal-Harness` ; `.venv/Scripts/python.exe -V` → 3.10.11.
- Suites : `.venv/Scripts/python.exe -m pytest -q` et
  `.venv/Scripts/python.exe -m pytest -o addopts="" -m e2e tests/e2e -q`.
- Journaux : `…/hermes/cache/scratch/uh-suite-*.log`, `e2e49.log`, `bench-out.json`.
