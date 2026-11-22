# Dossier d'architecture UH + UHB — sources

Ce dossier est **généré** : les fichiers ci-dessous sont les sources, le PDF en est le
rendu. **Le PDF n'est pas versionné** : il est produit localement
(`docs/UH_UHB_Dossier_Architecture_Roadmap.pdf`) et publié en pièce jointe de release.

| Fichier | Rôle |
| --- | --- |
| `build.py` | Contenu du dossier (texte, tableaux, statuts) + rendu : HTML paginé, puis PDF via Chromium (Playwright), avec table des matières remplie en deux passes. |
| `diagrams.py` | Génère les trois schémas vectoriels à partir de primitives (boîtes, flèches, étiquettes), pour que les alignements soient calculés. |
| `diagrams/01-flux-tour.svg` | Schéma 1 — architecture système et flux d'un tour (UH ↔ UHB ↔ fournisseurs / MCP). |
| `diagrams/02-couches-extension.svg` | Schéma 2 — couches et points d'extension. |
| `diagrams/03-cockpit-ui.svg` | Schéma 3 — cockpit : trois colonnes, panneaux détachables, flux de sécurité. |
| `styles.css` | Feuille de style d'impression (A4, en-têtes/pieds, tableaux, pastilles vertes/rouges). |
| `index.html` | Sortie intermédiaire du dernier rendu (utile pour inspecter une page dans un navigateur). |

## Régénérer

```bash
# 1. schémas
python docs/dossier_architecture/diagrams.py

# 2. dossier complet (HTML + PDF + copie Bureau)
python docs/dossier_architecture/build.py
```

**Ne jamais lancer `uv run` dans le dépôt.** `uv run` tente de réaligner le `.venv`
du projet sur la version demandée et en supprime une partie avant d'échouer
(constaté : pip, fastapi et playwright disparus). Le générateur tourne dans un venv
scratch séparé, créé hors du dépôt :

```bash
uv venv --python 3.12 <scratch>/dossier312
uv pip install --python <scratch>/dossier312/Scripts/python.exe playwright pymupdf
<scratch>/dossier312/Scripts/python.exe docs/dossier_architecture/build.py
```

Le générateur demande **Python ≥ 3.12** (f-strings contenant des littéraux entre
guillemets) et `playwright` + `pymupdf` :

```bash
uv venv --python 3.12 /tmp/dossier-venv
uv pip install --python /tmp/dossier-venv/bin/python playwright pymupdf
python -m playwright install chromium   # si aucun navigateur n'est déjà présent
```

Playwright et PyMuPDF sont des **outils de documentation** : ils n'entrent pas dans
les dépendances du paquet `uh-harness`, qui reste à `httpx` + stdlib.

## Ce que le document affirme, et d'où cela vient

- Les états de phase viennent de `docs/ROADMAP_UH_V2.md` (phrase d'état de chaque
  section) et de `CHANGELOG.md` (version livrée).
- Les compteurs de tests et la CI sont des mesures datées, refaites au moment de la
  génération (`pytest`, `gh api …/check-runs`).
- Aucune capacité n'est cochée sans jalon, PR, commit ou test : une case verte sans
  preuve serait un mensonge de documentation.

## Garde-fous respectés

- Aucune écriture dans `~/.uh` : les mesures utilisent des `UH_HOME` jetables.
- Aucun processus tiers arrêté ; les preuves tournent sur des ports dédiés.
- Aucun push, aucune fusion : le dossier est produit sur une branche de
  documentation, à relire avant publication.
