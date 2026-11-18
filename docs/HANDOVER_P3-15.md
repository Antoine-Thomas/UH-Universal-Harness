# P3-15 — relais : Lots 2, 3 et 4 (état au 2026-11-18)

Worktree : `C:\Users\searc\wt-intent`, branche `feat/intent-orchestration`.
**Ne rien réécrire des commits validés.** Rien n'est poussé, aucune PR.

```
5a96210  routeur déterministe (uh/intent.py) + P3-15 roadmap/matrice
1f8411b  câblage sous UH_INTENT_ROUTING=1 (off par défaut)
861849e  injection du Skill dans le tour + drapeau strict ("1" seulement)
1f2af51  Lot 1 : uh/skill_prefs.py + filtre du routeur + routes API
```

## Ce qui marche aujourd'hui (mesuré)

* `uh/intent.py` : `select()` / `turn_toolsets()` — familles + vocabulaire réel du
  catalogue, ≤ 3 toolsets, `ask` sur ambiguïté, aucun nom hors catalogue possible.
* `uh/console.py` : `_intent_toolsets()` (filtre), `_intent_skill()` (injection par
  tour, jamais dans l'historique), `intent_routing_enabled()` (**exactement `"1"`**),
  `_skill_listing()` filtré par les préférences.
* `uh/skill_prefs.py` : `~/.uh/skill_prefs.json`, écriture atomique, ids
  `origine:nom`, fichier corrompu → vide, natifs jamais modifiés.
* `uh/web/app.py` : `GET /api/skills` (marque `enabled`, garde les décochés),
  `PATCH /api/skills/{name}/enabled` (origine résolue serveur, 404 si inconnu),
  `POST /api/skills` (création, existait déjà).
* Tests : `tests/test_intent.py` (12), `tests/test_intent_wiring.py` (15),
  `tests/test_skill_prefs.py` (11), 138 passed sur les suites ciblées,
  `ruff` + `mypy uh` (71 fichiers) propres.

## Lot 2 — interface (onglet Outils)

Points d'intégration **mesurés**, ne pas en créer d'autres :

| Élément | Où |
|---|---|
| dispatcher des onglets | `uh/web/static/app.js` : liste `[["tools","tools-view"]]` + `if (name === "tools") loadToolCatalog()` |
| onglet + vue | `uh/web/static/index.html` : `id="tab-tools"`, `id="tools-view"`, `id="tools-list"`, `id="tools-hint"` |
| câblage des onglets | `wireProviders()` dans `app.js` |
| styles | `uh/web/static/style.css` |

À faire : remplacer la liste passive par une liste de Skills (nom, description,
origine, case à cocher), deux sections (natifs / utilisateur), boutons « Activer
tout » / « Désactiver tout » / « Actualiser », état `indisponible` affiché sans
bloquer le reste, erreurs en français. Chaque case appelle
`PATCH /api/skills/{name}/enabled`. Aucun redémarrage : les tours suivants lisent
`_skill_listing()`.

Contrainte connue : **CSP `script-src 'self'`** → dans les e2e, jamais
`page.wait_for_function`, seulement `expect(locator)`.

## Lot 3 — éditeur et import

* Éditeur : `POST /api/skills` existe (`name`, `title`, `description`, `instructions`)
  → ajouter un formulaire dans l'onglet Outils, validation avant envoi, puis
  rafraîchir la liste. Modification = créer avec un autre nom **ou** nouvelle route
  `PATCH /api/skills/{name}` à écrire (réutiliser `uh_skills.create()` après
  suppression explicite, ou ajouter `uh.skills.update()` — décider et tester).
* Import : accepter un **fichier `.md`** (le format UH est plat, pas de dossier) ;
  valider avant écriture (`uh.skills.parse` / `create` refusent déjà un titre vide,
  un nom invalide, un doublon) ; refuser tout chemin fourni par le client
  (n'accepter que le **contenu**), donc pas de traversée possible.
* Duplication / suppression : routes serveur à ajouter, natifs **refusés**
  (`origin == "native"` → 403).
* Aucune exécution à l'import : le fichier n'est que du texte, et `prompt_block`
  n'est appelé qu'au tour.

## Lot 4 — Skills ↔ outils

* Réutiliser `describe_toolsets()` (`uh/toolsets`, utilisé par `uh/commands.py:326`).
  Il n'existe **pas** de `describe_tools` central côté UH : l'outil nommé
  `describe_tools` est côté **UHB** (`uh/toolsets/uhb.py:131`). Ne pas créer de
  second registre ; si la découverte UHB est utile, passer par ce toolset.
* Métadonnées déclaratives : ajouter une section optionnelle au `.md`
  (ex. `## Capacités` listant `file`, `uhb`, `web_search`) lue par
  `uh.skills.read_skill()`, puis **intersectée** avec `self.toolsets` — jamais un
  nom hors catalogue, jamais une permission.
* Priorité : skill opérateur > skill utilisateur activé pertinent > skill natif
  activé pertinent > aucun. Un skill décoché ne participe à rien (déjà vrai pour la
  liste ; l'étendre aux métadonnées).

## Validation finale exigée (10 étapes)

1 écrire un skill dans UH · 2 l'activer · 3 demande naturelle dans le chat ·
4 vérifier la sélection · 5 vérifier l'injection réelle (le prompt du tour contient
le corps du skill) · 6 vérifier qu'un outil existant est proposé · 7 constater le
**refus UHB** quand la permission manque · 8 résultat dans le chat · 9 décocher ·
10 vérifier qu'il n'est plus chargé ni proposé. Puis répéter avec un skill importé
et **deux fournisseurs réels**.

Tant que 7 et 10 ne sont pas constatés, `P3-15` reste `IN PROGRESS`.

## Sécurité (invariants à ne pas casser)

* Case cochée = ressource de contexte, **aucune** permission d'exécution.
* Sélection (routeur) / décision (modèle) / autorisation (UHB) restent séparées.
* Une erreur du routeur retombe sur le catalogue **inchangé**, jamais plus large.
* Aucun chemin fourni par un client n'atteint le disque ; aucun secret journalisé.
* `UH_INTENT_ROUTING` reste **absent** de l'installation de l'opérateur.

## Ne pas mélanger

Le sujet `hermes-home-vision` / Hermes Agent v0.23.2 (PR #1, scripts de reprise de
session) est **indépendant** : aucun commit de ce worktree ne doit le toucher, et
inversement.

## Commandes

```bash
cd /c/Users/searc/wt-intent
PYTHONPATH= ../UH-Universal-Harness/.venv/Scripts/python.exe -m pytest tests/test_intent.py tests/test_intent_wiring.py tests/test_skill_prefs.py tests/test_web.py tests/test_console.py -q
PYTHONPATH= ../UH-Universal-Harness/.venv/Scripts/python.exe -m ruff check uh tests
PYTHONPATH= ../UH-Universal-Harness/.venv/Scripts/python.exe -m mypy uh
PYTHONPATH= ../UH-Universal-Harness/.venv/Scripts/python.exe -m pytest tests/e2e/test_p3_14_role.py -q -o addopts="" -m e2e   # modèle d'e2e UI
```
