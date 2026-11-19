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
3. Code brut au lieu d'un appel d'outil : relance automatique **prouvée jusqu'à
   l'exécution réelle**, avec limite de tentatives.
4. `POST /api/uhb/mode` : garde Origin / Host / CSRF, test qu'aucun outil du modèle
   ne peut l'appeler.
5. Interface : espaces entre les notes de statut ; Corbeille qui **retire l'image du
   fil** avec « Annuler » quelques secondes ; « Utile » / « Pas utile » exclusifs et
   réversibles ; libellé « noté, pas encore utilisé pour s'améliorer » ; en-tête et
   onglet « IA locale » d'accord sur le modèle qui a **réellement** répondu.

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
