# Handoff — après le lot 2 (voix, GPU) et le premier volet de D

Écrit avant un `/new`, pour qu'une session neuve reprenne sans rien deviner.
Ce fichier ne raconte pas l'histoire : il donne l'état, les preuves et l'ordre des
chantiers restants.

## 1. État des branches (tout est local, rien n'est poussé)

```text
* 143200a feat(web): say that UH stopped answering, and stop crying wolf on a checkout   <- HEAD
* b9ba3cb feat(voice): dictation follows the measured device, DLL paths first
* eae71aa docs(roadmap): the GPU dictation step, and the measure that gates it
* 39cbb7b feat(voice): offer the GPU dictation step, and verify it by loading CUDA
* 57e9e5c fix(voice): the install command names the interpreter that serves the UI
* 087f29a fix(voice): only promise the GPU when CUDA actually loads
* 84b4d08 docs(roadmap): voice engine plan delivered, with its proof
* 3a7205e feat(voice): say which engine serves the voice, why, and what is missing
* 1e07684 docs(roadmap): the six field returns of 23/11, each with its proof criterion
* 4a80a63 docs(handoff): the full registry table, the three readings, and the ffmpeg lead
* 3ab99ac docs(roadmap): the two unstable e2e tests, with the one measurement we have
* d0b01cb docs(roadmap): lot 2 points 1 and 2, with their measurements
* 69cf3c9 feat(web): the microphone never survives its context
* 8040230 docs(handoff): the registry cannot witness a browser capture, and the python has no trace
* 068e160 feat(web): one feedback bar per answer, and "Ecouter" only on real text
* 4746893 docs(handoff): who held the microphone, measured, before any dictation code
* 65b07ee docs(dossier): architecture, roadmap and modules as a paginated PDF
* 4746bf1 docs(readme): 2030 tests, measured on 1d6be9d
* a354c60 docs(roadmap): P5-UI lot 2 (eight points), and the state after PR #44
* 50ba243 docs(changelog): fix the 1.9.2 lease entry, and record PR #44
* a0e2e23 Fix/p5ui autofill main (#45)        <- main / origin/main (squash de l'autofill)
```

Branche courante : `feat/p5ui-d-offline-copy` (volet 1 de D), posée sur `feat/voice-gpu`.
La pile est linéaire sur `main` (`a0e2e23`), rebasée le 23/11 avec
`--onto main 1d6be9d --update-refs`, les trois commits d'autofill supprimés (contenu déjà
dans main). Tags de sauvegarde : `backup/<branche>-avant-rebase` (5 tags).

### Ordre de poussée (et de fusion) — chaque PR vise la branche du dessous

| Branche | PR vers | Contenu |
|---|---|---|
| `docs/dossier-architecture` | `main` | dossier d'architecture (sources ; le PDF n'est pas suivi) |
| `feat/p5ui-lot2-point1` | `docs/dossier-architecture` | une barre de retour par réponse, « Écouter » conditionné |
| `feat/p5ui-lot2-point2` | `feat/p5ui-lot2-point1` | micro : arrêt garanti dans toutes les sorties |
| `feat/p5ui-lot2-point7` | `feat/p5ui-lot2-point2` | moteur vocal décidé, panneau Voix, commandes nominatives |
| `feat/voice-gpu` | `feat/p5ui-lot2-point7` | étape GPU + vérification réelle + banc |
| `feat/p5ui-d-offline-copy` | `feat/voice-gpu` | « UH ne répond plus » + `Recharger`, bandeau désarmé en dev |

Règles de fusion tenues : `UH_PUSH_OK=1` est posé par l'opérateur, jamais par l'agent ;
pas de `gh` ; pas de squash ; on ne supprime pas une branche de base avant que la
suivante soit fusionnée.

## 2. Bilan mesuré

### Ce qui est vert

- **Unitaire : 2073 passed, 2 skipped, 99 deselected** (exit 0) — `uh-suite-d2.log`.
- **e2e : 99 passed en 13 min 39** (exit 0) — `uh-e2e-d2.log`.
- `ruff check`, `ruff format --check` et `mypy` verts (87 fichiers source).
- Commit `143200a` : texte hors ligne + bandeau de version désarmé sur un checkout de
  développement, avec contre-épreuve (garde `editable` retirée → test rouge).

### La voix et le GPU, tels que mesurés

- `cublas64_12`, `cublasLt64_12`, `cudnn64_9` : **introuvables avant**
  `os.add_dll_directory`, **les trois se chargent après** sur les dossiers
  `nvidia/{cublas,cudnn,cuda_nvrtc}/bin` du venv. `ctranslate2.get_cuda_device_count()`
  rend **1**. Ce défaut-là était réel : une machine correctement équipée aurait répondu
  « CUDA absent » indéfiniment.
- `voice_gpu.verify()` réel : 4 étapes OK, transcription d'un échantillon de 1 s
  **3,5 s sur GPU** (chargement du modèle inclus).
- Banc 10 s tonalité, chat **non chargé** : CPU `int8` **4,21 s**, GPU `int8_float16`
  **0,96 s**, VRAM après passage GPU **2 008 Mo**, delta **278 Mo**.
- Panneau Voix : moteur + raison, ligne machine, « Vérifier mon installation » (état,
  version, commande), « Tester la voix » par moteur, installation après clic seulement,
  et toute commande `pip` nominative — `"<sys.executable>" -m pip install …`, prouvé par
  un test qui lance le plan depuis **un second venv**.

### Ce qui n'est PAS mesuré (à garder tel quel)

- **Le couple dictée GPU / chat de 6,6 Go sur 8 Go.** Le banc 4 combinaisons avec
  `qwen3.5:9b` préchargé (`6,4 Go`, **12 %/88 % CPU/GPU**) n'a jamais rendu : cache
  Hugging Face stable sur 45 min, donc il attendait le GPU que le chat occupait. Processus
  arrêté par l'agent (`taskkill /PID 12796 /T /F`), chat déchargé (`keep_alive: 0`).
  Conséquence : **rien ne prouve qu'Whisper sur GPU dégrade ou non le chat**, ni que le
  GPU gagne sur de la vraie parole.
- La fidélité de transcription (mots corrects) n'a pas été mesurée sur de la parole
  réelle. Une phrase SAPI française a été générée
  (`…/scratch/sapi-fr.wav`, voix Microsoft Hortense), mais **le banc sur cette phrase n'a
  pas été exécuté**.
- Le relevé 3 du registre micro reste **non mesuré** (Edge jamais quitté complètement) ;
  « qui a ouvert le micro le 17/11 » reste non mesuré, et `docs/HANDOFF_POINT0.md` le dit.

## 3. Chantiers restants, dans l'ordre

### 3.1 Garde dynamique VRAM (décidée par l'opérateur, à écrire)

Règle validée : **au moment de dicter**, si la VRAM libre est **< 1,5 Go** — ou si la
mesure est absente/en erreur — la dictée bascule sur **CPU `int8`** ; sinon elle utilise
**GPU `int8_float16`** (et seulement si le banc dit que le GPU gagne, règle déjà en place).

À faire :

1. `uh/voice_gpu.py` : `free_vram_mb()` (déjà `vram_used_mb` ; ajouter la lecture
   `nvidia-smi --query-gpu=memory.free`), et une constante `MIN_FREE_VRAM_MB = 1500`.
2. `uh/audio/local.py::whisper_device()` : appeler la garde **à chaque dictée** (pas au
   démarrage), avant de rendre `cuda`. Un `None` (nvidia-smi absent, erreur, timeout) vaut
   refus → CPU.
3. Le panneau doit dire **pourquoi** : quand le GPU est refusé pour VRAM, la raison
   affichée nomme les Go libres mesurés (jamais « insuffisant » sans chiffre).
4. Tests : VRAM 900 Mo → CPU ; 2 000 Mo → GPU ; `None` → CPU ; et un test qui prouve que
   la garde est **relue à chaque appel** (une baisse entre deux dictées suffit à rebasculer).
5. Suites complètes + `ruff`/`mypy`, une branche dédiée (`feat/voice-vram-guard`).

### 3.2 Recherche web multi-moteurs (volet 2 de D)

- `uh/toolsets/web_search.py` interroge la page HTML de DuckDuckGo : `no results parsed`
  quand le débit est limité, et **aucune recherche d'images** (`/web image` ne peut pas
  marcher).
- À faire : interface de moteurs configurables — **Brave Search API**, **Tavily**,
  **SearXNG** (instance locale) — avec **DuckDuckGo en dernier recours** ; clé absente →
  moteur non candidat, jamais d'appel réseau sans clé (même règle que le vocal).
- Échec honnête : quand tous les moteurs échouent, le message dit lequel a échoué et
  pourquoi, au lieu de « retry later ».
- `/web image` : soit un vrai moteur d'images (Brave/Tavily), soit un message clair
  « pas de moteur d'images configuré » — pas de promesse muette.
- Preuves attendues : tests purs (ordre des moteurs, absence de clé = aucun appel, échec
  agrégé), un test qui prouve qu'aucune requête ne part sans clé, captures clair/sombre
  si l'interface change, suites complètes.

### 3.3 Lot E

- Bloc CSS `uh-style-polish` (l'opérateur le redonne, avec les noms de classes vérifiés
  sur le dernier `main`).
- Restes d'anglais visibles : panneau Fournisseurs (« Ask UH », « Allow remote (free)
  providers », « Test connection ») et champ de saisie.
- Défaut de contraste ouvert : bordure de contrôle **4,37:1** en thème clair (sous 4,5:1).

### 3.4 Lot F

- Étiquetage honnête du rendu : un dessin matplotlib doit être annoncé comme
  « illustration vectorielle, pas une photo » (constat du 23/11 : le modèle a présenté un
  tracé comme « la photo de moutons générée »).

## 4. Règles qui n'ont pas bougé

- Aucun push, aucun `--no-verify`, aucune touche à l'authentification, `gh` non relancé,
  aucun processus tué qui n'ait été lancé par l'agent.
- Une branche par point, un commit par sujet, suites + `ruff` + `mypy` avant de conclure.
- L'interface ne promet que ce qui est mesuré ; une capacité non vérifiée s'écrit
  « non vérifiée ».
- Aucune écriture dans les compétences, la mémoire ou le profil de l'opérateur sans son
  GO explicite.

## 5. Repères utiles

- Dépôt : `C:/Users/searc/UH-Universal-Harness` (venv `.venv`, Python 3.10.11).
- `uv run` est interdit dans ce dépôt (il a déjà vidé une partie du venv).
- Suites : `.venv/Scripts/python.exe -m pytest -q` et
  `.venv/Scripts/python.exe -m pytest -o addopts="" -m e2e tests/e2e -q`.
- Toujours un `UH_HOME` jetable pour les tests ; le vrai `~/.uh` ne se touche pas.
- Captures de ce lot : `…/hermes/cache/scratch/{point1-shots,point2-shots,autofill-shots,voice-shots}`.
- Journaux des suites : `…/hermes/cache/scratch/uh-{suite,e2e}-{rb,gpu,dev,d2}.log`.
