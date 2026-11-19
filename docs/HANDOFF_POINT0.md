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

1. Le venv d'UHB est un **Python 3.11.9 avec matplotlib 3.11.2** — pas 3.10.9. Le
   « matplotlib 3.10.9 » lu dans le message 436 ne correspond à **aucun** interpréteur
   mesuré.
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
