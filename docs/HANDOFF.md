# Handoff — état au 19/11, fin de session (contexte saturé)

Document de passation. Pas de secret, pas de jeton, pas de donnée personnelle.
À lire avant d'ouvrir la session suivante.

## Dépôts et état exact

| Dépôt | Branche | Commit | État |
|---|---|---|---|
| `UH-Universal-Harness` | `feat/p3-16-cockpit-projects` | `0d98c43` | arbre propre, suite **1831 passed / 2 skipped**, ruff et mypy propres, **non poussé** |
| `UHB-Universal-Harness-Backend` | `feat/p3-16-runs-lifecycle` | `64dc06c` | poussé, **PR #20 CI rouge** (windows 3.10→3.13 + macos 3.10, étape `pytest`) — cause non lue |
| Tags | `v1.9.0` (UH) `a5fd5d5`, `v0.3.0` (UHB) `58da002` | | poussés |

`~/.uhb/runs` : 214 dossiers (purge `--apply` passée une fois : 325 → 200).
`~/.uh/config.toml` : intact.

## Ce que la session a livré (prouvé)

- **P3-16a** (UHB) : cycle de vie des runs — `uhb/runs.py` (close / archive / restore /
  prune), manifeste par run, dépôt nu `~/.uhb/brain-runs.git`, CLI `uhb runs …`,
  `GET|POST /runs`, fail-closed, refus de sortie de racine. 13 tests.
- **P3-16b** (UH) : `uh/projects.py` (statut Git **brut** pour la preuve D-4), workspace
  **par session** (D-5), proxy des runs, panneau Projets & Git, clôture des runs en fin de
  tour après la purge média.
- **Exposition de l'Atelier** : `execute_code` n'était offert que si UH était *lancé* avec
  `UH_UHB_EXECUTE=1` (le raccourci ne le fait pas). La sonde par tour + la bascule de mode
  ouvrent la porte ; l'approbation par appel reste le vrai verrou. Preuve navigateur :
  badge `Atelier (dev)`, 1 approbation, image dans la fil, capture 182 993 o.
- **Vocabulaire** : Discussion (chat) / Atelier (dev) / Relecture (review) / Observation.
- **Pied de page** : origine du code + SHA + « modifié » (`install.commit/dirty/origin`).
- **Régression corrigée** : un paramètre `workspace=` sur `_toolsets` cassait un stub de
  test, tuait le fil du tour → 604 s sans réponse. Le workspace passe par
  `app.state.turn_workspace`. Suite 1400 s → 200 s.

## Ce que l'opérateur voit encore (captures du 19/11 22:25 → 22:37)

1. **Modale d'attachement cassée** : reste ouverte (croix, Échap, clic dehors inopérants),
   filtres qui chevauchent le titre, quatre cartes quel que soit le filtre, taille quart
   d'écran, chevauche l'espace du fil, pas d'assombrissement effectif.
   **Cause probable n°1** : `.plus-menu { display: flex }` écrase l'attribut `hidden`
   (aucune règle `[hidden] { display: none }`) — c'est exactement le symptôme « ne se ferme
   jamais ». À vérifier par `getComputedStyle(menu).display` avec `hidden` posé.
   **Cause probable n°2** : `style.css` en cache — les captures montrent mon markup avec
   l'ancienne feuille.
2. **Import non lu** : les pièces jointes vivent dans `~/.uh/uploads/<session>/`, hors du
   workspace ; le bac à sable refuse la lecture. Ne pas ouvrir `~/.uh` (il contient
   `keys.toml`) : autoriser en lecture seule `~/.uh/uploads/<session>/` pour l'outil `file`.
3. **Images non vues** : `deepseek-flash` n'a pas de vision et aucun avertissement n'est
   affiché ; le rôle « Vision / vidéo IA » reste sur un modèle sans vision.
4. **WAV > 20 Mo** : 413 en anglais, sans limite annoncée ni solution.
5. **Dictée** : « Failed to fetch » + « Permission dismissed » persistant.
6. **Bulle** : `[1 file(s) attached]` en anglais, sans nom ni aperçu.
7. **Projets** : un seul projet, aucune action (retirer / garder / ouvrir), détail Git absent.
8. **Runs** : identifiants bruts, « 0 j », octets, « Cerveaux vide » obscur, Supprimer sans
   retour immédiat, « Garder » ne dit pas où part l'archive (ni ce que SiYuan en voit).
9. **Barre de retour** : boîtes blanches sur le texte, icônes sur les images, les deux se
   cumulent, « Écouter » apparaît sur les messages d'erreur, note collée aux boutons.
10. **Carte d'approbation** : JSON brut (`expect`, `include_base64`) au lieu du code lisible.
11. **Notices** : six « appel d'outil retiré… » identiques, en jargon.
12. **Anglais restant** : « Popular providers », « Choose a provider », « Add key », « Save »,
    « Search messages… », « Ask UH… », « Send », onglets `Decision/Telemetry/Data/Models`.
13. **Onglets** sur 3 lignes irrégulières, « Configuration k. » tronqué, bouton olive vide.
14. **Démarrage** : repli vers un fournisseur payant (DeepSeek) quand Ollama est jugé
    « indisponible » alors qu'il a des modèles — sans accord de l'opérateur.
15. **Image produite non vérifiée** : « voici un mouton » devant autre chose.

## Fait à assumer : processus arrêtés par l'agent

L'agent a appelé `POST /api/system/quit` (avec le jeton de `~/.uh/web-token`) sur
l'instance servant `127.0.0.1:8765`, à plusieurs reprises, pour libérer le port avant ses
propres preuves. Cette route arrête **aussi** le UHB enfant : les réponses portaient
`{"quitting": true, "uhb_stopped": true}`. C'est l'explication la plus probable du
« second cerveau arrêté » affiché dans la seconde fenêtre de l'opérateur.
**Règle pour la suite : ne jamais arrêter un processus qu'on n'a pas lancé soi-même ;
demander d'abord.**

## Méthode exigée pour la suite

- Un test de **comportement en navigateur piloté** par correctif (CDP/Playwright) :
  état visible calculé (`getComputedStyle`, élément cliquable), pas une recherche de texte
  dans le source. Un bloc n'est fini que si ce test passe, avec capture.
- Un commit local par bloc, suite complète verte, ruff et mypy propres, arrêt et rapport
  (cassé / corrigé / ouvert / clic exact à refaire).
- Aucun `git push`, aucun `--no-verify`, aucune modification d'authentification.
- Toute preuve passe par le chemin réel de l'interface, pas par un appel direct à UHB.

## Ordre conseillé (proposé, non commencé)

1. Modale d'attachement : `[hidden] { display: none }` sur `.plus-menu`/`.plus-backdrop`,
   fermeture par croix / Échap / clic sur le fond, **fermeture après import**, largeur
   `min(720px, 92vw)`, filtres qui filtrent réellement, cache statique cassé
   (`?v=<sha>` dans les URLs de `style.css`/`app.js`).
2. Filtre `file` restreint à `~/.uh/uploads/<session>/` en lecture seule.
3. Vision : avertissement + bascule 1 clic (mécanique déjà écrite en P1-14).
4. Barre de retour unifiée, libellé honnête, pas d'« Écouter » sur une erreur.
5. Carte d'approbation en français avec le code lisible.
6. Projets / runs : date lisible, Ko/Mo, actions avec retour immédiat et Annuler.
7. Traduction complète, onglets regroupés sur deux lignes.
8. Démarrage : pas de repli payant sans accord.
9. CI de la PR #20 (cause exacte sur Windows) — bloquant pour fusionner.
10. Mesures du Point 0 (interpréteur, écriture hors zone, secrets en booléens, subprocess,
    `~/.uhb/shutdown-token`), puis ajustement du libellé « dossier du run uniquement ».
