"""Construit le dossier PDF (UH + UHB) : HTML paginé, puis rendu Chromium.

Deux passes : la première trouve, dans le PDF produit, la page de chaque titre
(texte réellement imprimé) ; la seconde réécrit la table des matières avec ces
numéros. Aucune dépendance ajoutée au produit : Playwright et PyMuPDF restent des
outils de documentation, hors du paquet `uh-harness`.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PDF_OUT = REPO / "docs" / "UH_UHB_Dossier_Architecture_Roadmap.pdf"
DESKTOP = Path(r"C:/Users/searc/Desktop")
PYMUPDF_PYTHON = Path(
    r"C:/Users/searc/AppData/Local/hermes/cache/scratch/dossier-venv/Scripts/python.exe"
)
PLAYWRIGHT_PYTHON = REPO / ".venv" / "Scripts" / "python.exe"

# --------------------------------------------------------------------------- #
# petites fabriques HTML
# --------------------------------------------------------------------------- #
DONE = '<span class="pill done">✔ livré</span>'
OPEN = '<span class="pill open">✘ à faire</span>'
MID = '<span class="pill mid">partiel</span>'


def pill(kind: str, label: str) -> str:
    return f'<span class="pill {kind}">{label}</span>'


def h2(anchor: str, number: str, title: str, first: bool = False) -> str:
    cls = ' class="first"' if first else ""
    return f'<h2 id="{anchor}"{cls}>{number}. {title}</h2>'


def h3(anchor: str, number: str, title: str) -> str:
    return f'<h3 id="{anchor}">{number} {title}</h3>'


def table(headers: list[str], rows: list[list[str]], *, widths: list[str] | None = None) -> str:
    def th(index: int, cell: str) -> str:
        style = ""
        if widths is not None and index < len(widths):
            style = ' style="width:%s"' % widths[index]
        return "<th%s>%s</th>" % (style, cell)

    head = "".join(th(index, cell) for index, cell in enumerate(headers))
    body = "".join("<tr>" + "".join("<td>%s</td>" % cell for cell in row) + "</tr>" for row in rows)
    return "<table><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>"


def callout(html: str, kind: str = "") -> str:
    return f'<div class="callout {kind}">{html}</div>'


def figure(
    svg_name: str,
    title: str,
    caption: str,
    *,
    full: bool = True,
    first: bool = False,
    notes: str = "",
) -> str:
    svg = (HERE / "diagrams" / svg_name).read_text(encoding="utf-8")
    svg = re.sub(r"<\?xml[^>]*\?>", "", svg).strip()
    classes = "full first" if (full and first) else ("full" if full else "")
    cls = f' class="{classes}"' if classes else ""
    body = f"<figcaption>{caption}</figcaption>"
    if notes:
        body += f'<div class="figure-notes"><b>{title} — points clés</b><ul>{notes}</ul></div>'
    return f"<figure{cls}>{svg}{body}</figure>"


# --------------------------------------------------------------------------- #
# contenu
# --------------------------------------------------------------------------- #
TOC = [
    ("cover", "Couverture", 1, None),
    ("s1", "1. Présentation du projet (UH + UHB)", 1, "1. Présentation"),
    ("s1-1", "1.1 Vision et positionnement", 2, "1.1 Vision"),
    ("s1-2", "1.2 Principes directeurs", 2, "1.2 Principes"),
    ("s1-3", "1.3 État mesuré au commit 1d6be9d", 2, "1.3 État mesuré"),
    ("s2", "2. Feuille de route complète", 1, "2. Feuille de route"),
    ("s2-1", "2.1 Phases P0 à P5 — l'ordre de codage initial", 2, "2.1 Phases P0"),
    ("s2-2", "2.2 Jalons P6 à P20 — extensions livrées ou ouvertes", 2, "2.2 Jalons P6"),
    ("s2-3", "2.3 Chantiers transverses", 2, "2.3 Chantiers"),
    ("s2-4", "2.4 Lot 2 P5-UI — huit points ouverts", 2, "2.4 Lot 2"),
    ("s2-5", "2.5 Prochain lot prioritaire et critères d'acceptation", 2, "2.5 Prochain lot"),
    ("s3", "3. Cartographie des modules", 1, "3. Cartographie"),
    ("s3-1", "3.1 Arborescence commentée de uh/", 2, "3.1 Arborescence"),
    ("s3-2", "3.2 Rôle des modules UH", 2, "3.2 Rôle des modules"),
    ("s3-3", "3.3 Modules UHB", 2, "3.3 Modules UHB"),
    ("s3-4", "3.4 Stockage : SQLite FTS5, TOML et $UH_HOME", 2, "3.4 Stockage"),
    ("s4", "4. Schémas techniques et architecture UI/UX", 1, "4. Schémas"),
    ("s5", "5. Sécurité et cycle de vie de la fenêtre", 1, "5. Sécurité"),
    ("s6", "6. Annexes : preuves, sources, garde-fous, glossaire", 1, "6. Annexes"),
]


def cover() -> str:
    return """
<section class="cover" id="cover">
  <div>
    <div class="band"></div>
    <div class="kicker">Dossier d'architecture · feuille de route · modules</div>
    <h1>UH — Universal Harness<br>et son second cerveau UHB</h1>
    <p class="subtitle">Cockpit local, provider-agnostic : architecture mesurée, cartographie du code,
    feuille de route vérifiée point par point, schémas vectoriels et critères d'acceptation du prochain lot.</p>
    <p><span class="badge">UH 1.9.2 (PyPI) · main 1d6be9d · PR #42 et #44 fusionnées · UHB 0.3.0</span></p>
    <div class="keyfig">
      <div><b>2030</b><span>tests UH passés (2 ignorés, 77 e2e à part), mesurés le 22/11 sur 1d6be9d</span></div>
      <div><b>22 / 22</b><span>checks CI verts : 3 OS × Python 3.10→3.13, jobs web, e2e, ruff+mypy, pre-commit</span></div>
      <div><b>429</b><span>tests UHB passés (9 ignorés) sur le commit UHB 3ea361d</span></div>
      <div><b>8</b><span>points du lot 2 ouverts, ordonnés, chacun avec son critère de preuve</span></div>
    </div>
  </div>
  <div class="meta">
    <div><b>Commit de référence</b><br>main <span class="mono">1d6be9d</span> — 445 commits ; PR #42
    (<span class="mono">1a014f0</span>) et PR #44 (<span class="mono">1d6be9d</span>) fusionnées.</div>
    <div><b>Mesures</b><br>UH : 2030 tests passés, 2 ignorés, 77 e2e à part.
    UHB : 429 passés, 9 ignorés. CI : 22/22 checks verts sur <span class="mono">1d6be9d</span>.</div>
    <div><b>Document</b><br>Généré le 22 novembre 2026 depuis le dépôt local, sources
    <span class="mono">docs/dossier_architecture/</span>, aucune ressource distante.</div>
    <div><b>Règle de lecture</b><br>Une case ne vaut que par sa preuve : jalon, PR, commit ou test.
    Aucune capacité n'est annoncée sans mesure.</div>
  </div>
</section>
"""


def toc(pages: dict[str, int] | None) -> str:
    rows = []
    for anchor, label, level, _probe in TOC:
        if anchor == "cover":
            continue
        number = pages.get(anchor, "") if pages else ""
        cls = "row sub" if level == 2 else "row"
        rows.append(
            f'<div class="{cls}"><span class="label">{label}</span><span class="page">{number or "·"}</span></div>'
        )
    return f"""
<h2 class="first" id="toc">Sommaire</h2>
<p class="small">Les numéros de page renvoient au PDF paginé ; la couverture n'est pas comptée dans le sommaire.</p>
<div class="toc">{"".join(rows)}</div>
{
        callout(
            "<b>Convention de statut.</b> "
            + DONE
            + " signifie « livré, publié, prouvé par un test ou une CI verte ». "
            + OPEN
            + " signifie « reste à faire », avec priorité et critère de preuve. "
            + MID
            + " signale un jalon partiellement livré, et la ligne dit ce qui manque."
        )
    }
"""


def section1() -> str:
    return f"""
{h2("s1", "1", "Présentation du projet (UH + UHB)")}
<p><b>UH (Universal Harness)</b> est un cockpit local : un seul point d'entrée — la commande
<span class="mono">uh</span> ou son raccourci Bureau — qui ouvre une interface web servie depuis
<span class="mono">127.0.0.1</span>, parle à un ou plusieurs fournisseurs de modèles et travaille sur les
fichiers de l'opérateur avec des garde-fous visibles. <b>UHB (Universal Harness Backend)</b> est son second
cerveau : un plan de contrôle local qui décide quel modèle répond, ce que le mode courant autorise, et
qui exécute du code après approbation explicite.</p>
<p>Le produit n'est pas un chat : c'est un poste de travail. Chaque tour rend visible ce qui s'est passé —
modèle réellement servi, chaîne de repli, outils offerts puis refusés, contexte consommé, durée — et
chaque capacité absente est affichée comme absente plutôt que simulée.</p>

{h3("s1-1", "1.1", "Vision et positionnement")}
<p>Trois choix structurent l'ensemble. <b>Local d'abord</b> : rien ne sort de la machine sans un
fournisseur configuré ; UHB, MCP, RAG, SiYuan et la passerelle router vivent sur la boucle locale.
<b>Indépendant du fournisseur</b> : un adaptateur OpenAI-compatible suffit pour brancher un nouveau
service, et la chaîne de repli est déterministe. <b>Une dépendance runtime</b> : <span class="mono">httpx</span>
et la bibliothèque standard — le reste (FastAPI, uvicorn) est l'extra <span class="mono">[web]</span>.</p>

{h3("s1-2", "1.2", "Principes directeurs")}
{
        table(
            ["Principe", "Ce que cela impose", "Où cela se vérifie"],
            [
                [
                    "Local-first",
                    "Bind 127.0.0.1 par défaut ; refus d'un bind distant sans <span class='mono'>--allow-remote</span> ; garde Host / Origin / Sec-Fetch-Site sur chaque requête.",
                    "uh/web/app.py (check_bind, middleware), tests de sécurité web",
                ],
                [
                    "Provider-agnostic",
                    "Ollama n'est pas requis ; un fournisseur OpenAI-compatible se déclare par une URL, un modèle et une clé.",
                    "uh/providers/openai_compat.py, chaîne de repli, tests fournisseurs",
                ],
                [
                    "Une seule dépendance runtime",
                    '<span class="mono">httpx</span> + stdlib. Aucune bibliothèque de rendu, de CLI ou de YAML ajoutée sans raison écrite.',
                    "pyproject.toml, README",
                ],
                [
                    "Contrat strict des toolsets",
                    "Chaque outil est décrit par un JSON-Schema ; le mode décide de son exposition avant que le modèle voie la liste.",
                    "uh/toolsets/*, uh/mode_gate.py, catalogue UHB",
                ],
                [
                    'Fail-closed sur <span class="mono">execute_code</span>',
                    "Approbation par appel, nonce à usage unique (120 s), environnement nettoyé, écriture limitée au dossier du run — « dossier du run », jamais « sandbox ».",
                    "UHB tools/execute.py + approvals, tests de confinement",
                ],
                [
                    "Pas de fonctionnalité « finie » sans mode d'échec",
                    "UHB mort, checkout absent, port occupé, outil refusé : un état visible et une phrase, jamais une exception nue.",
                    "roadmap §4, bannières UI, tests e2e",
                ],
            ],
            widths=["26%", "44%", "30%"],
        )
    }

{h3("s1-3", "1.3", "État mesuré au commit 1d6be9d")}
<p class="small">Mesures exécutées sur le dépôt local le 22 novembre 2026, avec l'interpréteur du projet.
Les chiffres sont des instantanés datés, pas des promesses : le compteur suit chaque test ajouté.</p>
{
        table(
            ["Élément", "Valeur mesurée", "Source"],
            [
                [
                    "Version produit",
                    "UH <b>1.9.2</b> (référence stable PyPI) ; UHB <b>0.3.0</b>",
                    'uh/__init__.py, uhb/__init__.py, tag v1.9.2 (<span class="mono">42d72cc</span>)',
                ],
                [
                    "Commit de référence",
                    '<span class="mono">main = 1d6be9d</span> (445 commits)',
                    "git log, gh api",
                ],
                [
                    "Fusions récentes",
                    'PR #42 <span class="mono">1a014f0</span> (lot 1 : bail, passe visuelle, textes) ; PR #44 <span class="mono">1d6be9d</span> (watchdog <span class="mono">--no-open</span>, e2e rôle)',
                    "git show --stat, CHANGELOG [Unreleased]",
                ],
                [
                    "Tests UH",
                    "<b>2030 passés</b>, 2 ignorés, 77 e2e désélectionnés — 6 min 47 s",
                    "pytest -q, 22/11/2026",
                ],
                [
                    "Tests e2e",
                    '77 tests marqués <span class="mono">e2e</span> (Chromium réel, serveur réel), lancés séparément',
                    'pytest -o addopts="" -m e2e',
                ],
                [
                    "Tests UHB",
                    "<b>429 passés</b>, 9 ignorés — 21 s",
                    'pytest uhb/tests, HEAD <span class="mono">3ea361d</span>',
                ],
                [
                    "CI du commit",
                    "<b>22/22 checks verts</b> : matrice 3 OS × Python 3.10-3.13, 6 jobs web, 2 jobs e2e, ruff+mypy, pre-commit",
                    "gh api …/commits/1d6be9d/check-runs",
                ],
                [
                    "Environnement local",
                    "Windows 11 ; venv UH Python 3.10.11 ; venv UHB 3.11 ; GPU RTX 3070 Ti 8 Go",
                    "interpréteurs des deux dépôts",
                ],
            ],
            widths=["24%", "46%", "30%"],
        )
    }
{
        callout(
            '<b>Une mesure honnête inclut ses pièges.</b> Trois tests de dépôt (<span class="mono">test_projects</span>, '
            '<span class="mono">test_fixes</span>) échouent si le répertoire temporaire de pytest se trouve dans un arbre '
            "versionné : ils vérifient qu'un dossier n'est pas un dépôt. La suite de référence a donc été mesurée avec "
            '<span class="mono">--basetemp</span> hors dépôt ; les 3 échecs observés autrement étaient un artefact de mesure, '
            "pas une régression.",
            "gold",
        )
    }
"""


def section2() -> str:
    rows_p05 = [
        [
            "<b>P0</b> — socle (1.0.0 → 1.1.0)",
            'Bac à sable et règles de workspace, Ctrl+C propre, <span class="mono">uh upgrade</span>, métriques d\'usage, erreurs fournisseur lisibles, UHB niveaux 1 et 2.',
            DONE,
            "Roadmap §2 (P0) ; tags v1.0.0 → v1.1.0 ; CHANGELOG",
        ],
        [
            "<b>P1</b> — UX console + web (9 tâches)",
            'DeepSeek/Kimi, suppression + dossiers, onglet Second brain, <span class="mono">uh web</span> démarre UHB, entête console, <span class="mono">/web</span>, raccourci Bureau.',
            DONE + '<br><span class="small">2 tâches livrées tard en P2</span>',
            'Roadmap §2 ( tableau P1 ) ; commits P2 ; <span class="mono">uh/uhb_spawn.py</span>, <span class="mono">shortcut.py</span>',
        ],
        [
            "<b>P2</b> — cockpit shell",
            'Zones redimensionnables mémorisées, dossiers, suppression de message, pièces jointes, pruning de contexte, <span class="mono">read_pdf</span>, onglet Second brain, chips de commandes.',
            MID,
            'Livré (roadmap P2) ; <span class="small">reste : quickstart sans <span class="mono">pip install -e</span></span>',
        ],
        [
            "<b>P3</b> — mémoire visible",
            'P3.1 routes mémoire (<span class="mono">ec88165</span>), P3.2 onglet Mémoire (<span class="mono">a006cca</span>), P3.3 vue Décision (<span class="mono">83e0eb8</span>).',
            MID,
            "Reste P3.5 (routes RAG/SiYuan côté UHB), P3.6 (boutons Reindex/Sync), fidélité du résumé",
        ],
        [
            "<b>P4</b> — rêve et auto-réparation",
            'Passes nocturnes/au repos, journal, backup, <span class="mono">--rollback</span>, budget ; <span class="mono">uh fix</span> (patch vérifié, jamais commité) et « Réparer UH » dans le cockpit.',
            MID,
            'Dream livré (P7, <span class="mono">uh dream --daemon</span>) ; auto-réparation = proposition + GO humain, pas d\'auto-application',
        ],
        [
            "<b>P5</b> — Wazuh et packaging",
            'Flux d\'alertes Wazuh dans le cockpit, puis méta-paquet <span class="mono">uh-suite</span> (UH + UHB en une installation).',
            OPEN,
            'Non commencé : la matrice des composants marque Wazuh « Missing (P5) » ; aucun méta-paquet dans <span class="mono">pyproject.toml</span>',
        ],
    ]
    rows_p6 = [
        [
            "P6 — cockpit UI/UX et télémétrie",
            "Fermé le 13/11, livré en <b>1.3.0</b> : rapports, consoles dockables, télémétrie.",
            DONE,
            "Roadmap §6 (« State: closed, shipped in 1.3.0 »)",
        ],
        [
            "P7 — démon et « daydream »",
            'Passes au repos (<span class="mono">uh dream --daemon</span>), déclencheur d\'inactivité, endpoints de rêves.',
            DONE,
            "Roadmap §7 ; CHANGELOG 1.4.x",
        ],
        [
            "P8 — sauvegarde des données et hub de skills",
            'Export ZIP complet et restauration (<span class="mono">uh/backup.py</span>), onglet Données.',
            DONE,
            'Roadmap §8 ; tests <span class="mono">test_backup.py</span>',
        ],
        [
            "P9 — distribution et marque",
            "Métadonnées PyPI 1.3.0 livrées ; page boutique et vérifications npm/crates/Homebrew encore ouvertes.",
            MID,
            "Roadmap §9 (« in progress »)",
        ],
        [
            "P10 — fenêtre du cockpit et MCP",
            'Fenêtre <span class="mono">--app</span> (Chromium/Edge, sans barre d\'onglets) et client MCP stdio, livrés le 13/11.',
            DONE,
            '<span class="mono">uh/web/browser.py</span>, <span class="mono">uh/mcp.py</span>',
        ],
        [
            "P11 — connaissance de soi",
            'Carte système générée (<span class="mono">uh map</span>), dossier de sorties, cycle de rêve, livrés le 14/11.',
            DONE,
            '<span class="mono">uh/system_map.py</span>, roadmap P11',
        ],
        [
            "P13 — auto-réparation et indépendance modèle",
            "Hugging Face comme fournisseur livré (1.6.0) ; auto-réparation outillée mais bornée (diff + CI + PR), pas autonome.",
            MID,
            'CHANGELOG 1.6.0 ; <span class="mono">uh/fixes.py</span>',
        ],
        [
            "P14 — Positron et transport HTTP/SSE",
            'P14.1 (<span class="mono">mcp_http.py</span>) livré le 14/11 ; UI Connexions, bascule de modèle et passe de polish (1.7.0).',
            DONE,
            "Roadmap P14.x ; CHANGELOG 1.7.0",
        ],
        [
            "P15 / P16 — conscience de soi, environnement de dev",
            "Livrés avec la <b>1.7.0</b>.",
            DONE,
            "Roadmap P15/P16",
        ],
        [
            "P17 — vrai test Positron",
            "Test réel du transport et d'un serveur hébergé : non fait.",
            OPEN,
            "Roadmap P17 (« open »)",
        ],
        [
            "P18 — rapport de terrain 1.7.0",
            "Retours d'usage consolidés, partiellement absorbés par P20.",
            MID,
            "Roadmap P18",
        ],
        [
            "P19 — mécanismes hermes-webui",
            "Déféré par décision, pas par manque de temps.",
            OPEN,
            "Roadmap P19 (« deferred »)",
        ],
        [
            "P20 — stabilisation 1.7.x",
            "Cinq invariants de cycle de vie (message non perdu, UI qui n'efface pas le document, jeton, port, fin de flux), testés.",
            DONE,
            'Roadmap P20 ; <span class="mono">tests/e2e/test_ux_invariants.py</span>',
        ],
    ]
    rows_cross = [
        [
            'Web / Desktop <span class="mono">--app</span>',
            'Fenêtre chromeless centrée, profil isolé <span class="mono">$UH_HOME/web-app</span>, bail de 10 s, arrêt propre à la fermeture, watchdog désarmé sans fenêtre.',
            DONE,
            '<span class="mono">uh/web/browser.py</span>, PR #42, PR #44',
        ],
        [
            "Client MCP stdio + HTTP/SSE",
            'Serveurs stdio avec allowlist et secrets isolés (<span class="mono">mcp-secrets.toml</span>, 0600) ; transport HTTP/SSE (<span class="mono">uh/mcp_http.py</span>).',
            DONE,
            "CHANGELOG 1.6.x / 1.7.x",
        ],
        [
            "Cloud AWS S3 / Google Drive",
            'S3 (<span class="mono">put_object</span>, 7 outils) et Drive en lecture, depuis le panneau Connexions ; un modèle de « self-repair » en lecture seule.',
            DONE,
            "Roadmap « Delivered 2026-11-14 »",
        ],
        [
            'Night pass <span class="mono">uh dream</span>',
            'Passes lisant mémoire et documentation, journal, archive, budget, <span class="mono">--rollback</span>.',
            DONE,
            '<span class="mono">uh/dream*.py</span>, tests dédiés',
        ],
        [
            "Plan de contrôle UHB",
            '<span class="mono">/modes</span>, <span class="mono">/route</span>, <span class="mono">/tools/approvals</span> (nonce), <span class="mono">/tools/execute</span>, <span class="mono">/shutdown</span>.',
            DONE,
            'UHB <span class="mono">entrypoints/server.py</span>, <span class="mono">shutdown.py</span>',
        ],
        [
            "Router / passerelle OpenAI-compatible",
            '<span class="mono">uh router serve</span> : endpoint local, jeton, plafond de corps, streaming, trace par requête.',
            DONE,
            '<span class="mono">uh/router/serve.py</span>, CHANGELOG 1.9.x',
        ],
        [
            "CI multi-OS",
            "22/22 checks : Linux/macOS/Windows × Python 3.10→3.13, jobs web, e2e Windows+Ubuntu, ruff+mypy, pre-commit.",
            DONE,
            'gh api sur <span class="mono">1d6be9d</span>',
        ],
        [
            "Packaging « un seul produit »",
            "Binaire/wheel unique, matrice de validation close avant de commencer.",
            OPEN,
            "Roadmap « P4 – Target: one product, packaged »",
        ],
        [
            "Wazuh (P5)",
            "Flux d'alertes dans le cockpit + méta-paquet.",
            OPEN,
            "Matrice des composants : « Missing (P5) »",
        ],
    ]
    rows_lot2 = [
        [
            "1",
            "Barre de retour unique et « Écouter » conditionné",
            "<b>P0</b> — bug visible sur chaque réponse",
            "Réponse image : une seule barre, pas d'« Écouter » ; notice : aucune barre ; 3 échecs d'« Écouter » : un seul message (CDP + captures)",
        ],
        [
            "2",
            "Micro : arrêt garanti, rien ne reste branché",
            "<b>P0</b> — sécurité et vie privée",
            "Mesure Windows consignée d'abord ; puis 0 piste live après Arrêter, après F5, et arrêt à 120 s (faux micro Chromium)",
        ],
        [
            "3",
            "Moteur de docking des panneaux",
            "P1",
            "Chat jamais détaché ni détruit ; 1280 → 1024 → 1920 puis rechargement : aucun débordement, Réinitialiser rétablit",
        ],
        [
            "4",
            "Régisseur : rafraîchissement, réparation, mémoire, rêve",
            "P1",
            "Jeton coupé : état « semble bloqué » à 20 s, rafraîchissement doux à 60 s, saisie conservée ; tour sain : rien",
        ],
        [
            "5",
            "Planificateur de rendu (natif → diffusion → schéma étiqueté)",
            "P1",
            "Même demande « mouton photoréaliste » sur 3 configurations : outil choisi et raison affichés ; aucune capacité non vérifiée",
        ],
        [
            "6",
            "Voix, webcam, vidéo — phases A à D",
            "P2 (par phases)",
            "Phase A conversation vocale ; B capture webcam jointe (voyant, arrêt, rien sur disque) ; C transformation ; D spécifiée seulement",
        ],
        [
            "7",
            "Moteur vocal selon la clé ET la machine",
            "P1 (dépend de 2 et 6)",
            "Sans clé + IA locale : aucun appel Voxtral (assert réseau) ; paliers machine mesurés ; panneau qui dit « pourquoi »",
        ],
        [
            "8",
            "Auto-amélioration encadrée (cadre seulement)",
            "P2",
            "Carnet, format de patch, essai isolé, écran d'approbation, manifeste de module ; un cas CSS de bout en bout ; aucun push, aucune auto-application",
        ],
    ]
    note_numbering = callout(
        "<b>Une numérotation qui a bougé, et qui est dite.</b> Le document initial numérote P0 à P5 ; les "
        'jalons suivants ont réutilisé <span class="mono">P3</span> (« évolutions fonctionnelles ») et '
        '<span class="mono">P4</span> (« un seul produit empaqueté ») pour d\'autres objets. Ce dossier conserve '
        "les numéros du dépôt et signale le doublon au lieu de réécrire l'histoire : c'est le seul moyen qu'une "
        "ligne de cette feuille de route reste vérifiable.",
        "gold",
    )

    note_border = callout(
        "<b>Défaut mesuré, ouvert dans ce lot (hors des huit points).</b> En thème clair, la bordure du compositeur "
        '(<span class="mono">#input</span>) mesure <b>4,37:1</b> contre son fond. C\'est au-dessus du seuil des composants '
        "d'interface (3:1, WCAG 1.4.11) mais loin du 4,5:1 attendu pour du texte : il ne doit donc jamais être présenté "
        "comme une preuve de contraste. À corriger dans la passe d'accessibilité (couleur de "
        '<span class="mono">--control-border</span> en clair) et à re-mesurer avec '
        '<span class="mono">scripts/prove_ui_cdp.py</span> avant toute nouvelle capture.',
        "warn",
    )

    note_next_lot = callout(
        "<b>Lot suivant : points 1 puis 2 du lot 2.</b><br>"
        "<b>1 — retour unique.</b> Une seule barre par réponse (celle de l'artefact fait foi), barre texte "
        "discrète sur le dernier message assistant, aucune barre sur system/erreur/notice ou moins de 20 caractères, "
        "« Écouter » seulement au-delà de 3 mots réels, un seul message d'erreur qui se remplace. Preuve : quatre "
        "cas CDP (image seule, texte long, notice, trois échecs) avec captures avant/après.<br>"
        "<b>2 — micro.</b> Avant tout code, mesure Windows (Confidentialité > Microphone, dernier accès par "
        'application) consignée dans <span class="mono">docs/HANDOFF_POINT0.md</span>. Ensuite : arrêt garanti à '
        '120 s et sur hidden/pagehide/beforeunload/erreur/changement de session, <span class="mono">track.stop()</span> '
        'dans un <span class="mono">finally</span>, indicateur permanent, « Dicter » distinct d\'« Écouter », erreurs '
        "429 et faster-whisper lisibles. Preuve : faux micro Chromium, 0 piste live après chaque sortie.<br>"
        '<b>Règles inchangées</b> : un commit par point, aucun push, aucun <span class="mono">--no-verify</span>, '
        "aucune modification de l'authentification, preuve navigateur plus captures.",
        "gold",
    )

    return f"""
{h2("s2", "2", "Feuille de route complète")}
<p>La feuille de route du dépôt (<span class="mono">docs/ROADMAP_UH_V2.md</span>, 2 242 lignes) n'est pas un
plan abstrait : chaque phase porte son état et sa preuve. Ce dossier la reprend intégralement, en séparant
trois choses qu'un tableau unique confondrait : <b>l'ordre de codage initial</b> (P0 à P5),
<b>les jalons ajoutés ensuite</b> (P6 à P20), et <b>les chantiers transverses</b> qui traversent plusieurs
phases (fenêtre, MCP, cloud, rêve, plan de contrôle, router, CI).</p>
{note_numbering}

{h3("s2-1", "2.1", "Phases P0 à P5 — l'ordre de codage initial")}
{
        table(
            ["Phase", "Contenu", "État", "Preuve / ce qui reste"],
            rows_p05,
            widths=["22%", "38%", "16%", "24%"],
        )
    }

{h3("s2-2", "2.2", "Jalons P6 à P20 — extensions livrées ou ouvertes")}
{table(["Jalon", "Objet", "État", "Preuve"], rows_p6, widths=["24%", "38%", "14%", "24%"])}

{h3("s2-3", "2.3", "Chantiers transverses")}
{
        table(
            ["Chantier", "Périmètre", "État", "Preuve"],
            rows_cross,
            widths=["22%", "40%", "14%", "24%"],
        )
    }

{h3("s2-4", "2.4", "Lot 2 P5-UI — huit points ouverts")}
<p>Retours du 22/11, inscrits dans la roadmap section « P5-UI lot 2 ». L'ordre est imposé : les points 1 et 2
d'abord (bug visible et sécurité du micro), puis 3 à 6, les points 7 et 8 suivant la machine et la clé.</p>
{
        table(
            ["#", "Point", "Priorité", "Critère de preuve exigé"],
            rows_lot2,
            widths=["5%", "30%", "16%", "49%"],
        )
    }
{note_border}

{h3("s2-5", "2.5", "Prochain lot prioritaire et critères d'acceptation")}
{note_next_lot}
"""


def section3() -> str:
    tree = """<b>uh/</b>
├── <b>cli.py</b> · __main__.py · commands.py      entrées CLI, commandes slash, parsing
├── <b>agent.py</b> · console.py                   boucle d'agent synchrone, REPL, bannière
├── <b>web/</b>  app.py · sse.py · browser.py      FastAPI, flux SSE, fenêtre --app, statique/
├── <b>providers/</b>                              ollama · cloudflare · deepseek · omnirouter · openai_compat · huggingface
├── <b>router/</b>                                 catalog.toml · client · policy · pool · provider · serve (passerelle)
├── <b>toolsets/</b>                               file · memory · web_search · terminal · uhb
├── <b>mcp.py · mcp_http.py · mcp_cloud.py</b>     client MCP stdio, transport HTTP/SSE, connecteurs cloud
├── <b>intent.py · mode_gate.py · context.py</b>   capacités d'un tour, outils autorisés, budget de contexte
├── <b>decision.py · turnlog.py · telemetry.py</b> décision du tour, journal d'événements, métriques
├── <b>memory*.py · dream*.py · skills.py</b>      mémoire lue/écrite, passes de rêve, skills opérateur
├── <b>store.py · config.py · keys.py</b>          SQLite FTS5, configuration TOML, clés chiffrées par l'OS
├── <b>sandbox.py · urlguard.py · maintenance.py</b> règles de workspace, URLs sortantes, ménage réversible
├── <b>fire  uhb_spawn.py · uhb_runs.py</b>        cycle de vie UHB vu depuis UH (spawn, runs, arrêt)
├── <b>fixes.py · backup.py · projects.py</b>      patches proposés, archive complète, dépôts Git
└── <b>audio/ · media/ · images.py</b>             voix locale/Voxtral, images d'outils, artefacts

<b>Les trois points d'entrée réels :</b> <span class="mono">uh</span> (CLI, dispatch dans uh/cli.py),
<span class="mono">uh web</span> (FastAPI + uvicorn, uh/web/app.py) et <span class="mono">python -m uh</span>
(équivalent CLI). Tout le reste est importé par ces trois-là."""
    table_uhb_modules = table(
        ["Module", "Rôle", "Surface exposée"],
        [
            [
                "entrypoints/server.py",
                'Point d\'entrée HTTP : routes Anthropic <span class="mono">/v1/messages</span> et OpenAI <span class="mono">/v1/chat/completions</span>, garde d\'origine, routes de contrôle.',
                "HTTP loopback 8101",
            ],
            [
                "entrypoints/shutdown.py",
                'Arrêt ordonné : ce qu\'on arrête, dans quel ordre, et la preuve que rien ne reste ; cibles <span class="mono">docker</span>, <span class="mono">local</span>, <span class="mono">self</span>.',
                '<span class="mono">POST /shutdown</span> (jeton, confirm)',
            ],
            [
                "entrypoints/cli.py",
                '<span class="mono">uhb</span> : run, status, config, update ; c\'est l\'entrée que UH démarre (elle honore <span class="mono">--port</span> et <span class="mono">--mode</span>).',
                "CLI",
            ],
            [
                "hub/modes",
                "Modes d'exécution déclarés comme données (chat, dev, review, observe, dream) : ce que l'agent peut faire.",
                '<span class="mono">GET /modes</span>',
            ],
            [
                "hub/router",
                "Routage d'intention : choisir le modèle, dire pourquoi, et quoi faire quand aucun ne peut.",
                '<span class="mono">POST /route</span>',
            ],
            [
                "hub/guard.py",
                "Porte d'écriture : permission appliquée avant que quoi que ce soit atteigne le disque.",
                "interne",
            ],
            [
                "tools/catalog.py, tools/execute.py",
                "Catalogue d'outils et exécution Python confinée (dossier du run, environnement nettoyé).",
                '<span class="mono">/tools/*</span>',
            ],
            [
                "tools/desktop.py",
                "Arbre d'accessibilité du système exposé comme outils, selon le mode.",
                "outils",
            ],
            [
                "core/*",
                "Les huit fonctions pivots du contrat (execute, stream, cancel, health, memory, route, resolve, skill).",
                "interne",
            ],
            [
                "adapters/*",
                "Un adaptateur par produit à qui UHB doit se présenter (Hermes, OpenClaw, Harness Maxxing, second brain).",
                "interne",
            ],
            [
                "services/rag",
                "RAG local : recherche hybride + reranking, serveur HTTP local.",
                "service local",
            ],
            [
                "runs.py",
                "Cycle de vie d'un run : ce qu'il laisse, et pour combien de temps (archivage ou suppression).",
                '<span class="mono">/runs</span>',
            ],
        ],
        widths=["22%", "52%", "26%"],
    )

    return f"""
{h2("s3", "3", "Cartographie des modules")}
<p>La carte ci-dessous décrit le code tel qu'il est au commit <span class="mono">1d6be9d</span>, pas une
intention : les rôles sont repris des docstrings de module, les entrées HTTP des routes réellement déclarées.</p>

{h3("s3-1", "3.1", "Arborescence commentée de uh/")}
<pre class="tree">{tree}</pre>

{h3("s3-2", "3.2", "Rôle des modules UH")}
{
        table(
            ["Domaine", "Modules", "Rôle et dépendances internes"],
            [
                [
                    "Entrées",
                    "cli.py, __main__.py, commands.py, onboarding.py",
                    "Parsing, sous-commandes, commandes slash ; appelle le reste, n'est appelé par personne.",
                ],
                [
                    "Orchestration",
                    "agent.py, intent.py, mode_gate.py, context.py, decision.py, turnlog.py, roles.py",
                    "Un tour : capacités → outils offerts → payload → réponse ; écrit la décision et le journal.",
                ],
                [
                    "Web / UI",
                    "web/app.py, web/sse.py, web/browser.py, web/providers_api.py, static/*",
                    "Routes /api, flux SSE, garde Host/Origin/CSRF, CSP, fenêtre --app, bail de fenêtre.",
                ],
                [
                    "Fournisseurs",
                    "providers/*, capabilities.py, llm_params.py, keys.py, providers_admin.py",
                    "Adaptateurs, capacités réelles (vision, outils), paramètres honorés, clés hors du code.",
                ],
                [
                    "Router",
                    "router/{catalog,client,policy,pool,provider,serve}.py",
                    "Catalogue de modèles, politique de choix, pool, passerelle OpenAI-compatible locale.",
                ],
                [
                    "Capacités",
                    "toolsets/*, mcp*.py, skills.py, skill_prefs.py, extensions.py",
                    "Outils JSON-Schema, MCP stdio/HTTP/cloud, skills markdown, extensions déclaratives.",
                ],
                [
                    "Mémoire & rêve",
                    "memory*.py, dream*.py, system_map.py",
                    "MEMORY.md, wiki, RAG/SiYuan en lecture, passes de rêve réversibles, carte système générée.",
                ],
                [
                    "Persistance",
                    "store.py, config.py, artifacts.py, attachments.py, backup.py",
                    "SQLite + FTS5, TOML, artefacts/pièces jointes, archive complète.",
                ],
                [
                    "Garde-fous",
                    "sandbox.py, urlguard.py, mode_gate.py, maintenance.py",
                    "Chemins autorisés, URLs sortantes, modes, nettoyage réversible.",
                ],
                [
                    "Cycle de vie UHB",
                    "uhb_spawn.py, uhb_runs.py, ollama_spawn.py",
                    "Spawn invisible (CREATE_NO_WINDOW, job object), arrêt, runs ; jamais un processus non possédé.",
                ],
                [
                    "Diagnostic",
                    "fixes.py, projects.py, infra.py, telemetry.py, hardware.py",
                    "Patches proposés, état Git, supervision lecture seule, métriques, paliers machine.",
                ],
            ],
            widths=["16%", "38%", "46%"],
        )
    }

{h3("s3-3", "3.3", "Modules UHB")}
{table_uhb_modules}

{h3("s3-4", "3.4", "Stockage : SQLite FTS5, TOML et $UH_HOME")}
{
        table(
            ["Emplacement", "Contenu", "Écrit par"],
            [
                [
                    '<span class="mono">$UH_HOME/store.db</span>',
                    "Sessions, messages, index FTS5 (triggers de synchronisation), métadonnées de session (dossier, décisions).",
                    "uh/store.py",
                ],
                [
                    '<span class="mono">$UH_HOME/config.toml</span>',
                    "Modèle et fournisseur par défaut, budget de contexte, options de l'interface.",
                    "uh/config.py, panneau Modèles",
                ],
                [
                    '<span class="mono">$UH_HOME/keys.toml</span>',
                    "Clés fournisseur, jamais sérialisées vers le navigateur (seulement des indices).",
                    "uh/keys.py",
                ],
                [
                    '<span class="mono">$UH_HOME/mcp.toml</span> + <span class="mono">mcp-secrets.toml</span>',
                    "Serveurs MCP, allowlist, secrets isolés (0600).",
                    "panneau Connexions",
                ],
                [
                    '<span class="mono">$UH_HOME/web-token</span>',
                    'Jeton de 43 caractères, réutilisé entre deux lancements sauf <span class="mono">--rotate-token</span>.',
                    "uh/web/app.py",
                ],
                [
                    '<span class="mono">$UH_HOME/MEMORY.md</span>, <span class="mono">skills/</span>, <span class="mono">uploads/</span>',
                    "Mémoire longue, skills de l'opérateur, pièces jointes par session.",
                    "toolsets, skills, attachments",
                ],
                [
                    '<span class="mono">$UH_HOME/dreams/</span>',
                    "Journal, archives, audits, idées des passes de rêve.",
                    "uh/dream*.py",
                ],
                [
                    '<span class="mono">$UHB/hub</span>, <span class="mono">runs/</span>',
                    "Modes, router, wiki, et ce que chaque run laisse derrière lui.",
                    "UHB",
                ],
            ],
            widths=["30%", "46%", "24%"],
        )
    }
"""


def section4() -> str:
    return f"""
{h2("s4", "4", "Schémas techniques et architecture UI/UX")}
<p>Trois planches vectorielles, dessinées pour cette page (aucune capture d'écran, aucun texte perdu à
l'impression). Elles sont livrées à part en SVG dans <span class="mono">docs/dossier_architecture/diagrams/</span>
et intégrées ici en vectoriel.</p>
{
        figure(
            "01-flux-tour.svg",
            "Schéma 1 — architecture système et flux d'un tour",
            "Lecture : le navigateur ne parle qu'à UH ; UH construit le payload, consulte UHB quand c'est demandé, "
            "puis parcourt la chaîne de repli. Les outils passent par le même contrat, et <span class='mono'>execute_code</span> "
            "ne s'exécute qu'après approbation.",
            full=True,
            first=True,
            notes=(
                "<li>Un seul sens de dépendance : la page appelle UH, UH appelle UHB, les fournisseurs ne voient que le payload.</li>"
                "<li>La chaîne de repli est ordonnée et ne s'applique pas aux refus d'authentification (401/402/403).</li>"
                "<li>Le bail ne dépend d'aucun outil : il ne fait que constater le silence de la page.</li>"
            ),
        )
    }
{
        figure(
            "02-couches-extension.svg",
            "Schéma 2 — couches et points d'extension",
            "Lecture : les flèches dorées sont les seuls gestes d'extension prévus. Ajouter un outil, un fournisseur, "
            "un serveur MCP, un connecteur cloud, un skill ou un correctif ne demande aucune modification du noyau — et "
            "le contrat qui ne bouge pas est rappelé en bas de planche.",
            notes=(
                "<li>Les six points d'extension sont en marge : le noyau ne connaît que des contrats (JSON-Schema, classe provider, entrée TOML).</li>"
                "<li>Aucun secret dans le code ni dans les journaux : clés TOML (0600) et <span class='mono'>mcp-secrets.toml</span> (0600).</li>"
                "<li>L'auto-réparation passe par un diff + une CI, jamais par une écriture directe dans l'arbre de l'opérateur.</li>"
            ),
        )
    }
{
        figure(
            "03-cockpit-ui.svg",
            "Schéma 3 — cockpit : espace, panneaux, sécurité",
            "Lecture : trois colonnes, des panneaux (détachement partiel aujourd'hui, complet au lot 2), et un flux de sécurité en six étapes qui va du jeton "
            "au port libéré. La dernière bande liste ce que la fenêtre ne fait jamais — la partie la plus utile d'une "
            "architecture.",
            notes=(
                "<li>Le chat reste dans la grille : c'est la colonne qu'on ne détache pas, et celle qui garde sa largeur utile.</li>"
                "<li>Chaque panneau est une vue de la même API locale ; aucun panneau ne parle directement à UHB.</li>"
                "<li>Le bail (45 s / 300 s) est la seule chose qui relie la fermeture d'une fenêtre à l'arrêt des processus.</li>"
            ),
        )
    }
"""


def section5() -> str:
    table_guardrails = table(
        ["Garde", "Ce qui est imposé", "Ce que cela empêche"],
        [
            [
                "Bind loopback",
                '127.0.0.1 par défaut ; un bind distant est refusé sans <span class="mono">--allow-remote</span> et affiché en avertissement.',
                "Exposer le cockpit et ses clés au réseau local.",
            ],
            [
                "Jeton de page",
                '43 caractères dans <span class="mono">$UH_HOME/web-token</span> (0600), accepté en <span class="mono">?token=</span> une fois puis en cookie.',
                "Une page tierce qui pilote l'API.",
            ],
            [
                "Cookie",
                '<span class="mono">HttpOnly; SameSite=Strict</span>, nommé par port : deux instances ne se volent pas la session.',
                "Vol de cookie par XSS ou par un second serveur local.",
            ],
            [
                "Origin / Sec-Fetch-Site",
                "Toute méthode qui change l'état exige une preuve d'origine ; un appel cross-site est refusé même sans Origin.",
                "CSRF et DNS rebinding.",
            ],
            [
                "CSP stricte",
                "<span class=\"mono\">default-src 'none'; script-src 'self'; style-src 'self'</span>, aucun inline, aucun CDN.",
                "Injection de script, ressource distante, fuite par balise.",
            ],
            [
                'Fenêtre <span class="mono">--app</span>',
                'Profil isolé <span class="mono">$UH_HOME/web-app</span>, fenêtre centrée, aucune barre d\'adresse.',
                "Qu'un navigateur déjà ouvert récupère l'URL et perde le mode application.",
            ],
            [
                "Bail de fenêtre",
                '<span class="mono">POST /api/system/alive</span> toutes les 10 s ; arrêt à 45 s de silence (300 s si la page se déclare réduite) ; jamais de <span class="mono">pagehide</span>.',
                "Un UHB orphelin après fermeture — sans tuer le serveur sur un simple F5.",
            ],
            [
                "Watchdog",
                'Armé seulement si <span class="mono">--app</span> et <span class="mono">open_browser</span> (PR #44) ; un tour en cours bloque l\'arrêt (409).',
                "Qu'un test headless ou <span class=\"mono\">--no-open</span> s'arrête tout seul.",
            ],
            [
                "Approbation d'outil",
                'Nonce à usage unique (120 s), lié à <span class="mono">execute_code</span>, consommé même en cas d\'échec ; <span class="mono">approved: true</span> dans le corps est ignoré.',
                "Un modèle qui s'auto-approuve.",
            ],
            [
                "Confinement",
                'Écriture limitée au dossier du run, secrets (clés, <span class="mono">.env</span>) refusés en lecture ; le badge dit « dossier du run uniquement ».',
                "Qu'un snippet lise les clés ou écrive ailleurs — sans prétendre à un bac à sable noyau.",
            ],
        ],
        widths=["18%", "50%", "32%"],
    )

    return f"""
{h2("s5", "5", "Sécurité et cycle de vie de la fenêtre")}
<p>La sécurité d'UH n'est pas un module : c'est un ensemble de gardes sur le chemin entre le navigateur et les
outils. Chacune a un test ou une mesure, et l'échec est visible.</p>
{table_guardrails}
{
        callout(
            "<b>Le point qui a coûté le plus cher, et sa leçon.</b> Fermer la fenêtre laissait UH et UHB vivants : "
            'rien ne reliait la fin de la page à la fin des processus. Un <span class="mono">pagehide</span> aurait réglé '
            "la fuite mais tué le serveur à chaque F5. La réponse est un bail : la page le renouvelle, le serveur constate "
            "le silence, arrête UHB puis sort. Deux mesures l'ont prouvé — 48 s après fermeture d'une page Playwright, "
            "305 s après fermeture d'une vraie fenêtre Edge (voie « cachée »), code 0 dans les deux cas, port libéré et "
            "enfant arrêté avant la sortie."
        )
    }
"""


def section6() -> str:
    return f"""
{h2("s6", "6", "Annexes : preuves, sources, garde-fous, glossaire")}
{h3("s6-1", "6.1", "Preuves mesurées (commandes et résultats)")}
{
        table(
            ["Mesure", "Commande", "Résultat"],
            [
                [
                    "Suite UH",
                    '<span class="mono">.venv/Scripts/python -m pytest -q --basetemp &lt;hors dépôt&gt;</span>',
                    "2030 passed, 2 skipped, 77 deselected — 6 min 47 s",
                ],
                [
                    "Tests de bail",
                    '<span class="mono">pytest tests/test_web_lease.py -q</span>',
                    '13 passed (dont <span class="mono">test_no_open_disables_the_watchdog_even_with_app_window</span>)',
                ],
                [
                    "Preuve UI navigateur",
                    '<span class="mono">python scripts/prove_ui_cdp.py</span>',
                    "0 variable CSS non définie ; modale fermée par Échap, clic dehors, Annuler ; onglets non tronqués à 1280/1024 px. Contraste du <b>texte</b> : 9,53:1 (sombre) et 14,90:1 (clair). <b>Bordures</b> de contrôles : 4,65:1 et 4,37:1 — seuil 3:1 ; le 4,37 clair est un défaut ouvert (voir 2.4), pas une preuve de contraste texte",
                ],
                [
                    "Suite UHB",
                    '<span class="mono">pytest uhb/tests -q</span> (dépôt UHB)',
                    "429 passed, 9 skipped — 21 s",
                ],
                [
                    "CI du commit",
                    '<span class="mono">gh api repos/…/commits/1d6be9d/check-runs</span>',
                    '22 checks, tous <span class="mono">success</span>',
                ],
                [
                    "Lint",
                    '<span class="mono">ruff check</span> · <span class="mono">ruff format --check</span> · <span class="mono">mypy uh</span>',
                    "verts sur le dépôt (312 fichiers formatés, 85 modules typés)",
                ],
            ],
            widths=["20%", "40%", "40%"],
        )
    }

{h3("s6-2", "6.2", "Sources du dossier")}
<ul>
  <li><span class="mono">docs/ROADMAP_UH_V2.md</span> (2 242 lignes) — phases, états, preuves ; section « P5-UI lot 2 ».</li>
  <li><span class="mono">CHANGELOG.md</span> — versions, correctifs, entrées <span class="mono">[Unreleased]</span> et 1.9.2 rectifiée.</li>
  <li><span class="mono">README.md</span>, <span class="mono">docs/API.md</span>, <span class="mono">docs/WEB_DESIGN.md</span> — contrat HTTP, modèle de sécurité.</li>
  <li>Code lu : <span class="mono">uh/</span> (54 modules à la racine, 7 sous-paquets), <span class="mono">uhb/</span> (points d'entrée, hub, outils, services, adaptateurs).</li>
  <li>Génération : <span class="mono">docs/dossier_architecture/build.py</span>, <span class="mono">diagrams.py</span>,
      <span class="mono">styles.css</span>, <span class="mono">diagrams/*.svg</span>.</li>
</ul>

{h3("s6-3", "6.3", "Garde-fous respectés pendant la production")}
<ul>
  <li>Aucune modification du comportement runtime : le dossier n'ajoute que de la documentation et des sources de rendu.</li>
  <li>Aucune écriture dans le <span class="mono">~/.uh</span> de l'opérateur : les mesures ont utilisé des <span class="mono">UH_HOME</span> jetables.</li>
  <li>Aucun processus tiers arrêté : les preuves ont tourné sur des ports dédiés, l'instance 8765/8101 de l'opérateur est restée intacte.</li>
  <li>Aucun push, aucune fusion : le dossier est produit sur une branche de documentation, à relire avant toute publication.</li>
</ul>

{h3("s6-4", "6.4", "Glossaire")}
{
        table(
            ["Terme", "Définition"],
            [
                [
                    "Toolset",
                    "Ensemble d'outils exposés au modèle, décrits par un JSON-Schema et filtrés par le mode courant.",
                ],
                [
                    "Bail (lease)",
                    "Renouvellement périodique par la page ; son silence prouve que la fenêtre a disparu.",
                ],
                [
                    "Mode",
                    "chat (lecture seule), dev (exécution après approbation), review, observe, dream : déclarés par UHB.",
                ],
                [
                    "Nonce",
                    "Jeton à usage unique lié à une approbation d'outil ; consommé même si l'exécution échoue.",
                ],
                [
                    "Plan de contrôle",
                    "UHB : /modes, /route, /tools/approvals, /tools/execute, /shutdown — il décide, UH exécute.",
                ],
                [
                    "Repli (fallback)",
                    "Chaîne déterministe ollama → cloudflare → deepseek → omnirouter → router, hors 401/402/403.",
                ],
                [
                    "Régisseur",
                    "Module à venir qui possède les automatismes de l'interface (rafraîchir, relancer, rêver, réparer), chacun journalisé et interruptible.",
                ],
            ],
            widths=["20%", "80%"],
        )
    }
"""


def build_html(pages: dict[str, int] | None = None) -> str:
    css = (HERE / "styles.css").read_text(encoding="utf-8")
    body = (
        cover()
        + toc(pages)
        + section1()
        + section2()
        + section3()
        + section4()
        + section5()
        + section6()
    )
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>UH · UHB — Dossier d'architecture et feuille de route</title>
<style>{css}</style>
</head>
<body>{body}</body>
</html>"""


def render(html_path: Path, pdf_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    header = (
        '<div style="font-size:7.6pt; width:100%; margin:0 16mm; color:#5d7370; '
        "font-family:'Segoe UI',sans-serif; display:flex; justify-content:space-between; "
        'border-bottom:0.4pt solid #cfdfdc; padding-bottom:1.6mm;">'
        "<span>UH · UHB — Dossier d'architecture et feuille de route</span>"
        "<span>main 1d6be9d · 22 novembre 2026</span></div>"
    )
    footer = (
        '<div style="font-size:7.6pt; width:100%; margin:0 16mm; color:#5d7370; '
        "font-family:'Segoe UI',sans-serif; display:flex; justify-content:space-between; "
        'border-top:0.4pt solid #cfdfdc; padding-top:1.6mm;">'
        "<span>Sources vérifiées sur le dépôt — aucune capacité non mesurée</span>"
        '<span>Page <span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.as_uri(), wait_until="load")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template=header,
            footer_template=footer,
            margin={"top": "20mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
        )
        browser.close()


def page_map(pdf_path: Path) -> dict[str, int]:
    import fitz

    doc = fitz.open(str(pdf_path))
    found: dict[str, int] = {}
    probes = {anchor: probe for anchor, _label, _level, probe in TOC if probe}
    for index, page in enumerate(doc, start=1):
        if index <= 2:  # couverture + sommaire : les titres y figurent déjà
            continue
        text = page.get_text("text")
        for anchor, probe in probes.items():
            if anchor not in found and probe in text:
                found[anchor] = index
    doc.close()
    return found


def write_html(pages: dict[str, int] | None = None) -> Path:
    """Écrit ``index.html`` avec un saut de ligne final.

    ``end-of-file-fixer`` (pre-commit) refuse un fichier sans ``\\n`` final : sans ce
    ``+ "\\n"``, chaque génération rendait l'arbre de travail modifié, et le hook
    bloquait le commit suivant pour un octet invisible.
    """
    html_path = HERE / "index.html"
    html_path.write_text(build_html(pages) + "\n", encoding="utf-8", newline="\n")
    print(f"HTML écrit : {html_path} ({html_path.stat().st_size} octets)")
    return html_path


def main() -> int:
    html_path = write_html(None)
    render(html_path, PDF_OUT)
    print(f"PDF (passe 1) : {PDF_OUT}")

    pages = page_map(PDF_OUT)
    missing = [entry[0] for entry in TOC if entry[0] not in ("cover",) and entry[0] not in pages]
    print(f"pages trouvées pour {len(pages)}/{len(TOC) - 1} entrées ; manquantes : {missing}")

    html_path = write_html(pages)
    render(html_path, PDF_OUT)

    import fitz

    doc = fitz.open(str(PDF_OUT))
    print(f"PDF final : {doc.page_count} pages, {PDF_OUT.stat().st_size / 1024:.0f} Ko")
    doc.close()

    if DESKTOP.is_dir():
        # Un aperçu ouvert (ou l'indexeur Windows) verrouille le fichier : on essaie le
        # nom canonique, puis des suffixes numérotés, et on dit lequel a été écrit.
        candidates = [DESKTOP / PDF_OUT.name] + [
            DESKTOP / f"{PDF_OUT.stem} ({index}){PDF_OUT.suffix}" for index in range(2, 10)
        ]
        last: OSError | None = None
        for candidate in candidates:
            try:
                shutil.copy2(PDF_OUT, candidate)
            except OSError as exc:
                last = exc
                time.sleep(0.4)
                continue
            suffix = (
                ""
                if candidate.name == PDF_OUT.name
                else f" (nom canonique verrouillé : {last.strerror if last else '?'})"
            )
            print(f"copie Bureau : {candidate}{suffix}")
            break
        else:
            print(f"Bureau : aucune copie possible ({last})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
