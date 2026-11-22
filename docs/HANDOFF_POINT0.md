# Relais — Point 0 : mesurer avant de coder

À coller tel quel dans une **session Hermes neuve**. Rien n'est poussé, rien ne doit l'être.

## Consignes

Aucun `git push`, aucun `--no-verify`, ne touche pas à l'authentification de
l'opérateur (ni `~/.gitconfig`, ni le keyring, ni `gh`).

Contexte sur disque : `docs/ROADMAP_UH_V2.md`, `git log --oneline -8`.
Branche : `fix/1.8.0-tool-serialization-and-uhb-mode`.
Commits acceptés : `f8e1a97` (point 1 : mode chat par défaut, badge, carte
« Autoriser », test d'étanchéité des outils), `47fc9c5`, `9ce05d5`, `056640a`
(documentation : retours d'expérience, installation unique).

Un arrêt après chaque numéro, un commit local par numéro, suite verte, ruff et mypy
propres, preuve par l'interface réelle.

## Corrections à appliquer d'emblée

1. **Mesuré le 19/11 (session `20261119-1455-d057`)** : `execute_code` tourne sous
   l'interpréteur **système** `…\Programs\Python\Python311\python.exe` (3.11.9,
   `-I -B -X utf8`, `sys.prefix` = le dossier d'installation, donc pas un venv),
   **sans numpy ni matplotlib**. Le `matplotlib 3.10.9` du message 436 est celui du
   **Python 3.10.11 système**, lancé par `subprocess` depuis le snippet (messages
   425 et 435 nomment ce chemin absolu) : il correspond donc à un interpréteur
   réel, mais ce n'est pas celui du runner. Le venv du checkout UHB
   (`UHB-Universal-Harness-Backend\.venv`) est bien 3.11.9 + matplotlib 3.11.2,
   mais `import fastapi` y échoue : il ne peut pas faire tourner UHB, et
   `_supports_uhb()` l'écarte. **Aucun** de ces trois messages n'atteint un venv.
   Voir « Résultats du Point 0 » en fin de document.
2. Toute mesure d'environnement se fait **`PYTHONPATH=` vide** : sinon `numpy`
   provient d'un venv Hermes (mesuré : `ImportError … cp314` sur un 3.11).
3. Les sondes s'écrivent dans un **fichier** (pas de heredoc : deux échecs
   d'échappement ont déjà invalidé une mesure).
4. La sonde passe par un **vrai `execute_code` via l'interface, avec approbation**.
   Un appel direct à UHB contourne la porte d'approbation et fausse la mesure.

## 0. Interpréteur d'`execute_code` — mesure seule, pas de code

1. `sys.executable` et `sys.prefix` depuis un `execute_code` réel.
2. Messages 424/430 et 436 de la session `20261119-1210-5c05` : commande exacte,
   interpréteur, et **comment** le venv d'UHB a été atteint (subprocess, chemin
   absolu, autre). Séparer mesuré / supposé.
3. La restriction d'écriture du mode `dev` s'applique-t-elle au code lancé par
   `execute_code`, ou seulement à `write_file` ? Écrire un fichier témoin **hors** de
   `~/.uhb/runs` et hors `uhb/**`, `tests/**`, `docs/**`, le supprimer, donner le
   résultat exact.
4. `.env`, `keys.toml`, `mcp-secrets.toml`, keyring : **lisibles ou non** depuis
   `execute_code` ? Booléens uniquement, jamais les valeurs.
5. `subprocess` peut-il lancer un autre interpréteur ? Le prouver ou l'infirmer.

Rendre les résultats **bruts avant toute interprétation**. Ce sont eux qui décident
de ce que le badge « Agent » peut promettre, et si un profil « atelier » (écriture
limitée au dossier du run) passe avant tout le reste.

## 1 à 5 — ensuite, dans cet ordre

1. Règle : « un tour ne se termine jamais sans réponse ni notice visible », en
   couvrant les **trois** sorties de `run_turn` (deux retours propres +
   `BudgetExhausted`), avec réconciliation explicite de
   `test_a_model_stuck_on_a_forbidden_tool_stops_the_turn`. Le message utilisateur
   est toujours persisté avec une réponse ou une notice.
   **Livré le 19/11** : `ChatSession._end_without_answer` pose une phrase unique
   par tour silencieux (limite d'itérations, limite de refus, réponse vide, et
   fournisseur muet), l'émet en `notice` et la **stocke** en message `system` —
   direct et rechargement disent la même chose. Un modèle bloqué sur un appel
   interdit n'est plus relancé pour du texte (`_stopped_on_refusal`), ce qui
   supprime trois appels payés dans le vide. `test_turn_never_silent.py` (9 cas +
   invariant paramétré « aucun tour stocké ne finit sur le message de
   l'utilisateur ») ; preuve par l'interface : session `20261119-1526-d9e4`,
   `max_iterations = 1` → frame `notice` « le tour s'est arrêté après 1 appels… »
   + ligne `system` en base, puis un tour normal dans la même session → réponse
   « bonjour », **aucune** notice.
2. En chat, un outil interdit n'est pas proposé — ou son refus produit
   `mode_refused` avec la carte. Aucune approbation pour un outil que le mode interdit.
   **Livré le 19/11** : `uh/mode_gate.py` applique la vue lecture seule
   (`read_only_view`) au seul point où les toolsets atteignent `run_turn`
   (`_intent_toolsets`). En `chat`, l'offre mesurée est
   `describe_tools, memory_read, read_file, search_files, uhb_modes, uhb_route,
   uhb_status, web_search` ; `patch`, `write_file`, `memory_write`, `desktop_*` et
   tous les `mcp__*` sont retirés. Un appel qui passe malgré tout (rejeu, invention)
   revient typé `mode_forbidden` avec le mode, et `mode_refused_frame` en fait le
   frame que la carte « Autoriser pour cette conversation » consomme. La porte
   d'approbation n'est jamais atteinte : `Uhbtoolset.call` refuse avant, et
   `explain_missing` n'est qu'une explication. Preuve par l'interface : session
   `20261119-1534-0917`, `turn_offer` dans `~/.uh/uh-web.log` (mode=chat, 8 outils,
   withheld non vide ; après passage en `dev`, offered complet, withheld=[]), et le
   modèle lui-même énumère l'offre réduite sans qu'aucune approbation ne soit
   demandée.
3. Code brut au lieu d'un appel d'outil : relance automatique **prouvée jusqu'à
   l'exécution réelle**, avec limite de tentatives.
   **Livré le 19/11** (`c007a5f`) : `_NUDGE_LIMIT = 2`, la relance nomme
   `execute_code` et interdit de recoller le code ; elle ne part que si un outil
   *exécutable* a été offert (donc jamais en `chat`, où la porte du point 2 l'a
   retiré) et jamais quand l'opérateur demande explicitement le code
   (« montre-moi le code », « sans l'exécuter », « pseudo-code »). L'instruction est
   retirée de l'historique avant stockage (`_without_nudges`) et comptée :
   `turn_end … nudges=N` dans `~/.uh/uh-web.log`. Preuve interface : tour
   `8ca446ec` avec `nudges=1`, deux appels `execute_code` exécutés, transcript
   sans ligne `system` ; tour `2ae45c46` `nudges=0` (appel direct), PNG 18 555 o
   rendu par `~/.uhb/atelier`.
4. `POST /api/uhb/mode` : garde Origin / Host / CSRF, test qu'aucun outil du modèle
   ne peut l'appeler.
   **Livré le 19/11** : le middleware répond désormais **403** (et non 400) pour
   `host not allowed`, `origin not allowed`, `cross-site request refused`, et la
   route refuse en plus de servir un UH non-loopback (`403 … loopback`). Mesuré sur
   le serveur réel, mode relu après chaque refus :

       sans jeton                         → 401  mode inchangé
       jeton + Origin: https://evil…      → 403  « origin not allowed »
       jeton + Sec-Fetch-Site: cross-site → 403  « cross-site request refused »
       jeton + Host: evil.example         → 403  « host not allowed »
       jeton seul (CLI)                   → 200  mode = dev
       jeton + Origin du cockpit          → 200  mode = dev

   Et le troisième verrou, mesuré depuis un `execute_code` réel (session
   `20261119-1612-fa7b`, approbation `a5e1e63ea6ba`, run
   `run-20261119-161216-5d36f4`) : lire le jeton par chemin absolu →
   **PermissionError** « confinement atelier : lecture d'un fichier protege » ;
   appeler la route sans jeton → **401** ; avec un jeton deviné → **401** ; avec une
   origine étrangère → **403** ; mode après ces tentatives : **dev, inchangé**.
   *Limite assumée, mesurée le même jour* : le hook bloque les **fichiers protégés
   par nom**, pas toute lecture hors du run — la sonde peut **lister** `~/.uh`
   (`SYSTEM_MAP.md`, `config.toml`, …) mais pas ouvrir `web-token`, `keys.toml` ni
   `mcp-secrets.toml`. C'est ce qui suffit ici : sans jeton, la route reste fermée.
5. Interface : espaces entre les notes de statut ; Corbeille qui **retire l'image du
   fil** avec « Annuler » quelques secondes ; « Utile » / « Pas utile » exclusifs et
   réversibles ; libellé « noté, pas encore utilisé pour s'améliorer » ; en-tête et
   onglet « IA locale » d'accord sur le modèle qui a **réellement** répondu.
   **Livré le 19/11** : une seule ligne de statut par image (`.tool-artifact-status`,
   en bloc — les deux `<span>` collés faisaient « notée utilegardée ») ; « Corbeille »
   retire l'image du fil immédiatement et propose **« Annuler » pendant 8 s**, l'annulation
   passant par une action serveur `restore` qui retrouve le fichier **par provenance**
   (sidecar de la corbeille) et refuse d'écraser ; votes exclusifs et réversibles
   (`vote: "none"` journalisé, `aria-pressed`) ; libellé « noté (pas encore utilisé pour
   s'améliorer) » ; `answer_meta` porte le modèle **servi** et le modèle **demandé**, et
   la même valeur s'affiche dans le pied de la réponse, l'infobulle d'en-tête et l'onglet
   « IA locale » (ligne marquée « · a répondu »). Mesuré en réel (session
   `20261119-1646-40d3`) : `ANSWER_META … "substituted": false`, journal de votes
   `["up","down","none"]`, corbeille → fichier absent, restauration → mêmes 15 240 octets
   au même chemin.

Le redémarrage d'UHB pendant un tour (ancien point 3) attend un « go » explicite.

## Installation unique — inscrit, ne pas implémenter

Voir `docs/ROADMAP_UH_V2.md`, section « Installation unique » : silencieux mais
visible (récapitulatif + désinstallation), composants épinglés et vérifiés
(SHA-256), hors ligne après installation, aucun identifiant GitHub en écriture,
`gh repo clone` réservé aux développeurs, interpréteur « atelier » isolé et annoncé,
et la restriction d'écriture réelle livrée avec. À ne pas implémenter avant les
mesures du point 0 et un « go ».

## Pièges déjà payés (ne pas les repayer)

- `PYTHONPATH` hérité : mesure fausse et `ImportError` trompeur.
- Heredoc : échappements cassés, sonde jamais exécutée.
- `-m uhb.entrypoints.server --mode X` : les drapeaux sont **ignorés** ; le bon
  point d'entrée est `-m uhb.entrypoints.cli run --port … --mode …`.
- Le mode est un attribut du **processus** : en changer exige un redémarrage de UHB.

## Résultats du Point 0 — mesurés le 19/11 (session `20261119-1455-d057`)

Conditions : sonde écrite dans un fichier, exécutée par un vrai `execute_code` passé
par l'interface de UH (`POST /api/sessions/{id}/chat`, SSE) avec la carte
d'approbation (ticket `d1f9f81888aa`, `timeout 120 s`, réponse `allow`), `PYTHONPATH`
vide dans le processus UH, UHB en mode `dev`.

1. **Interpréteur** — `sys.executable` = `…\Programs\Python\Python311\python.exe`,
   `sys.prefix` = `…\Python311` (= `base_prefix`, donc pas un venv), 3.11.9,
   `flags.isolated` vrai, `cwd` = le dossier du run, `PYTHONPATH` et `VIRTUAL_ENV`
   nuls. Commande enregistrée par UHB :
   `python.exe -I -B -X utf8 <run_dir>\_uhb_runner.py`.
2. **Écriture** — un témoin de 16 o écrit puis supprimé **hors** de `~/.uhb/runs` :
   `…\hermes\cache\scratch\point0_witness.txt` et `…\Desktop\point0_witness.txt`,
   succès aux deux endroits. La restriction d'écriture du mode `dev`
   (`uhb/**`, `tests/**`, `docs/**`) ne s'applique **pas** au code de
   `execute_code` : elle ne gouverne que les outils fichier.
3. **Secrets** — `~\.uh\keys.toml` (384 o), `~\.uh\mcp-secrets.toml` (164 o) et
   `UHB-Universal-Harness-Backend\.env` (65 o) : lisibles, premier octet lu, flag
   d'écriture vrai. Le module `keyring` est absent, mais `cmdkey /list` répond
   (rc 0) et énumère **78** cibles : le magasin d'identifiants Windows est
   énumérable depuis `execute_code`.
4. **Subprocess** — un interpréteur quelconque est lançable : `same_as_runner`,
   `Python310` (matplotlib 3.10.9), `Python311` (sans matplotlib), et le venv du
   checkout UHB (matplotlib 3.11.2) répondent tous. Le modèle s'en est servi en
   425/435/436 pour contourner l'absence de matplotlib.
5. **Conséquence** — le libellé « Agent : exécute du code, portée non vérifiée »
   (`a992592`) était juste. Le profil « atelier » (confinement réel + interpréteur
   dédié) passe **avant** les points 1 à 5 de l'interface.


---

## Mesure 2.0 — microphone, 22/11 (lecture seule, avant tout code de dictée)

Objectif du lot 2, point 2 : savoir **qui** utilise le micro avant d'écrire quoi que ce
soit. Aucun réglage modifié, aucun enregistrement lancé, aucune sonde Python.

### Méthode

`Get-ItemProperty` (lecture) sur deux clés, sans écriture :

```
HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone
HKCU\…\ConsentStore\microphone\NonPackaged
```

Pour chaque sous-clé : chemin de l'exécutable (`#` = `\`), `Value`, `LastUsedTimeStart`,
`LastUsedTimeStop` (FILETIME → heure locale). `LastUsedTimeStop = 0` = accès encore
ouvert (consigne : s'arrêter).

### Résultat mesuré — NonPackaged

| Exécutable | Début | Fin | Encore ouvert |
| --- | --- | --- | --- |
| `…\Programs\Python\Python311\python.exe` | 2026-11-14 18:08:11 | 2026-11-17 22:11:47 | **non** |
| `…\NVIDIA Corporation\NvContainer\nvcontainer.exe` | 2026-11-17 19:11:36 | 2026-11-17 19:12:48 | non |
| `…\chocolatey\lib\ffmpeg\tools\ffmpeg\bin\ffmpeg.exe` | 2026-11-17 19:10:03 | 2026-11-17 19:10:20 | non |
| `…\Microsoft\Edge\Application\msedge.exe` | 2026-05-28 14:22:33 | 2026-05-28 14:22:41 | non |
| `…\Google\Chrome\Application\chrome.exe` | 2026-09-28 21:43:52 | 2026-09-28 21:44:13 | non |
| `…\obs-studio\bin\64bit\obs64.exe` | 2026-10-03 14:25:25 | 2026-10-03 14:25:29 | non |
| `…\Adobe Premiere Pro 2026\Adobe Premiere Pro.exe` | 2026-09-07 15:48:50 | 2026-09-07 15:48:56 | non |
| `…\Adobe Premiere Pro (Beta)\Adobe Premiere Pro (Beta).exe` | 2026-11-13 18:52:57 | 2026-11-13 18:53:28 | non |
| `…\Steam\bin\cef\cef.win64\steamwebhelper.exe` | 2026-04-15 23:10:18 | 2026-04-16 01:18:06 | non |
| `…\Vegas Pro 17.0\vegas170.exe` | 2026-04-19 13:56:10 | 2026-04-19 13:56:16 | non |
| `…\NVIDIA Broadcast\NVIDIA Broadcast.exe` | 2026-06-14 17:00:50 | 2026-06-14 17:00:54 | non |
| `H:\SteamLibrary\…\Skate.exe` | 2026-04-18 21:57:52 | 2026-04-18 22:29:55 | non |
| `I:\SteamLibrary\…\GTA5_Enhanced.exe` | 2026-08-21 21:18:39 | 2026-08-21 21:18:41 | non |

Aucune entrée `LastUsedTimeStop = 0` : **rien ne tient le micro** à l'instant de la
mesure. **Aucune entrée datée du 22/11.**

Empaquetées (Store) : `Prompt` par défaut ; `ScreenSketch`, `Photos`, `Camera` et
`SoundRecorder` en `Allow`, sans horodatage. Aucune ne peut être le « python » observé.

### Classification demandée

| Question | Réponse |
| --- | --- |
| (a) le python de UH ? | **Non** — UH tourne sous `.venv\Scripts\python.exe` (3.10.11) : aucune entrée à ce chemin. |
| (b) l'atelier `~/.uhb/atelier` ? | **Non** — ce venv a son propre `Scripts\python.exe` sous `%USERPROFILE%\.uhb` : aucune entrée. |
| (c) un autre python ? | **Oui** — le Python système `…\Programs\Python\Python311\python.exe`, dernière utilisation 14/11 18:08 → 17/11 22:11. C'est **le même interpréteur que celui d'`execute_code`/UHB** (voir la section « Résultats du Point 0 » du 19/11) : c'est l'explication la plus probable du libellé « python » dans les réglages. |
| Une entrée Edge/msedge ? | **Oui**, mais du 28/05 (8 s) : elle prouve qu'un accès micro **par le navigateur** est bien journalisé ici. Rien pour le 22/11. |

### Corrélation avec les essais de dictée — **non mesurée**

L'heure des essais n'est pas connue de cette session. Deux lectures, à trancher avec
elle : (1) si les essais datent du 22/11, **aucun processus n'a réellement ouvert le
micro** ce jour-là — l'échec est en amont (permission navigateur, `getUserMedia`
refusé, dictée jamais déclenchée) ; (2) si l'entrée « python » vue dans les réglages
date du 14–17/11, elle décrit un usage antérieur (UHB / `execute_code`), sans lien avec
la dictée.

**À fournir pour conclure** : l'heure approximative des essais du 22/11. La corrélation
consiste à vérifier si `2026-11-14 18:08 — 2026-11-17 22:11` la contient.

### Ce que la mesure ne dit pas

- Le registre horodate l'**accès à la capacité**, pas le handle : il ne nomme ni script,
  ni module, ni flux.
- Elle ne prouve pas l'absence d'un handle ouvert par un tiers (outil de handles non
  installé, hors périmètre).
- Rien n'a été modifié, aucun processus touché, aucune dictée provoquée.

### Décision

Pas d'arrêt requis (aucun `Stop = 0`). Deux exigences pour le point 2 : l'indicateur
« Micro actif » se pilote sur le **flux réel** (`track.readyState`, pistes live), et le
test CDP doit prouver 0 piste live après arrêt, après F5 et à 120 s — le registre ne
prouve pas, à lui seul, qu'un flux est fermé.

## Mesure 2.0 (suite) — le 22/11, et le python du 14 au 17/11

### Quel navigateur `uh web --app` lance réellement

Mesuré par exécution, pas par lecture : `find_chromium()` rend
`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`, et
`centered_position()` rend `--window-position=640,280` sur cet écran. L'ordre des
candidats est Edge d'abord (`uh/web/browser.py`, `WINDOWS_CANDIDATES`), puis Chrome.

### Relecture du registre (lecture seule, `reg query ... /s`)

Trois entrées seulement portent un horodatage dans
`HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone` :

| exécutable | dernier début | dernière fin |
| --- | --- | --- |
| `…\Microsoft\Edge\Application\msedge.exe` | 2026-05-28 14:22:33 | 2026-05-28 14:22:41 |
| `…\Google\Chrome\Application\chrome.exe` | 2026-09-28 21:43:52 | 2026-09-28 21:44:13 |
| `…\Programs\Python\Python311\python.exe` | 2026-11-14 18:08:11 | 2026-11-17 22:11:47 |

- **Aucune entrée du 22/11**, ni pour Edge, ni pour Chrome, ni pour la sous-clé
  `Packaged` (qui n'existe pas sur ce poste).
- **Aucune valeur `LastUsedTimeStop = 0`** : rien ne tient le micro à l'instant de la
  lecture.

### Ce que le code prouve, et ce que le registre ne peut pas dire

`app.js` (l'ancien comme le nouveau) n'affiche `dictée impossible : …` qu'après un
`getUserMedia` réussi, un `MediaRecorder` et un POST sur `/api/audio/transcribe` ; un
refus du micro affiche `Micro refusé : …`. Le message vu à 17:32 prouve donc **une
capture réelle**, alors que le registre ne l'attribue à aucun exécutable.

Conclusion mesurée : sur ce poste, le ConsentStore **n'est pas un témoin fiable** d'une
capture Chromium. Mécanisme : **non mesuré**. Hypothèses, non vérifiées :

- Windows n'écrirait la paire qu'à la fermeture complète du processus, et Edge garde des
  processus en arrière-plan après la fermeture de la fenêtre ;
- la capture passerait par le service audio de Chromium, journalisée sous un autre
  exécutable que l'hôte.

Test décisif (proposé, **non exécuté** — il ouvre le vrai micro) : enregistrer 6 s dans
la fenêtre `--app`, relire le registre immédiatement ; puis quitter complètement Edge et
relire. Si la valeur ne bouge qu'après la sortie complète, l'hypothèse 1 est établie et
l'absence du 22/11 s'explique sans nier la capture.

### Le python du 14 au 17/11 : aucune trace dans les dépôts cités

Recherche en lecture seule, sur la fenêtre exacte 14/11 18:08 → 17/11 22:11 :

- `~/.uhb/runs` : **aucun run** — le plus ancien est `run-20261118-170921-c431ee`.
- `~/.uhb` : un seul fichier modifié dans la fenêtre (`shutdown-token`).
- `~/.uhb/atelier` : créé le **19/11 15:59** — il n'existait pas entre le 14 et le 17,
  donc **aucun snippet confiné ne pouvait ouvrir le micro** à ces dates.
- Sessions UH (`~/.uh/uh.db`) : **0 message** entre le 14 et le 18/11.
- Mes propres commandes (Hermes, `state.db`) : **11 465 messages** relus sur la période ;
  aucun ne contient `sounddevice`, `pyaudio`, `speech_recognition`, `waveIn`, `winmm`,
  `dshow`, `wasapi`, `getUserMedia` ni `microphone`.
- `…\Python311\Lib\site-packages` : **aucune** bibliothèque audio (`sounddevice`,
  `pyaudio`, `soundcard`, `speech_recognition`, `torchaudio`) — rien ne pouvait importer
  une capture sans avoir laissé un paquet.

Mesuré : aucune trace dans les dépôts nommés. Non mesuré : **quel processus** a ouvert le
micro. Le trou de confinement (point 3) **n'est pas démontré** : l'atelier n'existait pas
à ces dates.
