"""Génère les trois schémas vectoriels du dossier (sources .svg conservées).

Chaque schéma est assemblé à partir de primitives (boîte, flèche, texte) pour que
les alignements soient calculés, pas devinés : un décalage de 6 px se voit sur une
page A4 imprimée.
"""

from __future__ import annotations

from pathlib import Path

W = 980
INK = "#284543"
TEAL = "#0c9f93"
TEAL_SOFT = "#d9efec"
GOLD = "#fdc502"
GOLD_SOFT = "#fdf0c2"
GRAY = "#5d7370"
BORDER = "#c3d6d3"
PANEL = "#f6faf9"
WHITE = "#ffffff"
GREEN = "#16a34a"
RED = "#dc2626"
FONT = "Segoe UI, system-ui, -apple-system, sans-serif"
MONO = "Cascadia Mono, Consolas, monospace"

DEFS = f"""<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
          orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{INK}"/></marker>
  <marker id="arrow-teal" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
          orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{TEAL}"/></marker>
  <marker id="arrow-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
          orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{RED}"/></marker>
  <marker id="arrow-gold" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
          orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{GOLD}"/></marker>
</defs>"""


def esc(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rect(x, y, w, h, *, fill=WHITE, stroke=BORDER, rx=8, sw=1.3, dash=None, opacity=None):
    extra = f' stroke-dasharray="{dash}"' if dash else ""
    if opacity is not None:
        extra += f' fill-opacity="{opacity}"'
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{sw}"{extra}/>'
    )


def text(
    x,
    y,
    value,
    *,
    size=12,
    weight=400,
    fill=INK,
    anchor="start",
    mono=False,
    italic=False,
    opacity=None,
):
    family = MONO if mono else FONT
    style = ' font-style="italic"' if italic else ""
    op = f' fill-opacity="{opacity}"' if opacity is not None else ""
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}"{style}{op}>{esc(value)}</text>'
    )


def lines(x, y, values, *, size=11.5, lh=15, fill=INK, weight=400, anchor="start", mono=False):
    return "".join(
        text(
            x, y + index * lh, value, size=size, fill=fill, weight=weight, anchor=anchor, mono=mono
        )
        for index, value in enumerate(values)
    )


MARKERS = {INK: "arrow", TEAL: "arrow-teal", RED: "arrow-red", GOLD: "arrow-gold"}


def arrow(
    x1,
    y1,
    x2,
    y2,
    *,
    label=None,
    color=INK,
    dash=None,
    marker=None,
    lab_dx=6,
    lab_dy=-6,
    lab_anchor="start",
):
    extra = f' stroke-dasharray="{dash}"' if dash else ""
    marker_id = MARKERS.get(color, "arrow")
    out = [
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.6"{extra} '
        f'marker-end="url(#{marker_id})"/>'
    ]
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        out.append(text(mx + lab_dx, my + lab_dy, label, size=10.5, fill=GRAY, anchor=lab_anchor))
    return "".join(out)


def header(y, kicker, title):
    return text(28, y, kicker.upper(), size=10.5, weight=700, fill=TEAL) + text(
        28, y + 22, title, size=17, weight=700, fill=INK
    )


def pill(x, y, label, *, good=True, w=None):
    color = GREEN if good else RED
    width = w or max(64, 8 * len(label) + 24)
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="20" rx="10" fill="{color}" fill-opacity="0.12" '
        f'stroke="{color}" stroke-width="1"/>'
        + text(x + 10, y + 14, label, size=10, weight=700, fill=color)
        + text(
            x + width - 10,
            y + 14,
            "✔" if good else "✘",
            size=10,
            weight=700,
            fill=color,
            anchor="end",
        )
    )


# --------------------------------------------------------------------------- #
# 1. Architecture système et flux d'un tour
# --------------------------------------------------------------------------- #
def diagram_turn_flow() -> str:
    h = 780
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}">',
        DEFS,
    ]
    parts.append(rect(0, 0, W, h, fill=WHITE, stroke=WHITE, sw=0))
    parts.append(
        header(
            34, "Schéma 1 — architecture système", "Un tour de A à Z : UH, UHB, fournisseurs et MCP"
        )
    )

    # cockpit
    parts.append(rect(250, 78, 480, 52, fill=TEAL_SOFT, stroke=TEAL))
    parts.append(
        text(
            490,
            100,
            "Cockpit navigateur — uh web / uh web --app",
            size=13,
            weight=700,
            anchor="middle",
        )
    )
    parts.append(
        text(
            490,
            118,
            "HTML · CSS · JS vanilla, CSP 'self' (aucun CDN, aucun inline)",
            size=10.5,
            fill=GRAY,
            anchor="middle",
        )
    )

    parts.append(
        arrow(
            490,
            130,
            490,
            162,
            label="HTTP loopback · jeton 43 car. → cookie HttpOnly; SameSite=Strict",
            lab_dx=8,
            lab_dy=0,
        )
    )

    # UH web layer
    parts.append(rect(40, 166, 900, 120, fill=PANEL))
    parts.append(text(58, 188, "UH — couche HTTP (uh/web/app.py, FastAPI)", size=12.5, weight=700))
    boxes = [
        (58, "Routes /api/*", "sessions, models, keys,\nproviders, git, dreams"),
        (268, "SSE /chat", "jetons, outil, erreur,\nheartbeat 15 s"),
        (478, "Gardes", "Host · Origin ·\nSec-Fetch-Site · CSP"),
        (688, "Bail de fenêtre", "/api/system/alive\ntoutes les 10 s"),
    ]
    for x, title, body in boxes:
        parts.append(rect(x, 200, 190, 74, fill=WHITE))
        parts.append(text(x + 12, 220, title, size=11.5, weight=700, fill=TEAL))
        parts.append(lines(x + 12, 238, body.split("\n"), size=10.5, fill=GRAY))

    parts.append(
        arrow(
            490,
            286,
            490,
            316,
            label="run_turn() — boucle synchrone, un tour à la fois",
            lab_dx=8,
            lab_dy=0,
        )
    )

    # orchestration + uhb consult
    parts.append(rect(40, 320, 470, 118, fill=WHITE))
    parts.append(text(56, 340, "Orchestration du tour", size=12, weight=700))
    parts.append(
        lines(
            56,
            360,
            [
                "context.py      budget, résumé, coupe du payload",
                "intent.py       capacités nécessaires avant la liste d'outils",
                "mode_gate.py    ce que le mode autorise à offrir",
                "decision.py     complexité, chaîne, modèle servi",
            ],
            size=10.5,
            fill=GRAY,
            lh=17,
            mono=False,
        )
    )

    parts.append(rect(530, 320, 410, 118, fill=GOLD_SOFT, stroke=GOLD))
    parts.append(text(546, 340, "UHB — plan de contrôle (loopback, 8101)", size=12, weight=700))
    parts.append(
        lines(
            546,
            360,
            [
                "GET  /modes               ce que le mode permet",
                "POST /route               modèle choisi + raison",
                "POST /tools/approvals     nonce à usage unique",
                "POST /tools/execute       exécution après approbation",
            ],
            size=10.5,
            fill=GRAY,
            lh=17,
            mono=True,
        )
    )

    parts.append(arrow(275, 438, 275, 462))
    parts.append(arrow(735, 438, 735, 462))

    # fallback chain
    parts.append(rect(40, 466, 900, 118, fill=PANEL))
    parts.append(
        text(
            56,
            486,
            "Chaîne de repli déterministe — hors 401 / 402 / 403 ; un modèle choisi pour la session reste seul",
            size=11.5,
            weight=700,
        )
    )
    chain = ["ollama\n(local)", "cloudflare", "deepseek", "omnirouter", "router\n(passerelle)"]
    x = 56
    for index, name in enumerate(chain):
        parts.append(rect(x, 500, 150, 62, fill=WHITE, stroke=TEAL if index == 0 else BORDER))
        parts.append(lines(x + 12, 524, name.split("\n"), size=11.5, weight=700, lh=15))
        parts.append(text(x + 12, 552, "1er" if index == 0 else "puis", size=9.5, fill=GRAY))
        if index < len(chain) - 1:
            parts.append(arrow(x + 150, 531, x + 176, 531, color=TEAL, marker="arrow-teal"))
        x += 176

    parts.append(arrow(490, 584, 490, 610))

    # capabilities row
    caps = [
        (40, 320, "Toolsets UH", "file · memory · web_search\nterminal (opt-in) · uhb"),
        (372, 300, "MCP", "stdio + HTTP · allowlist\nsecrets dans mcp-secrets.toml"),
        (684, 256, "execute_code (UHB)", "approbation par appel,\nconfinement « dossier du run »"),
    ]
    for x, w, title, body in caps:
        parts.append(rect(x, 614, w, 70, fill=WHITE))
        parts.append(text(x + 12, 634, title, size=11.5, weight=700, fill=TEAL))
        parts.append(lines(x + 12, 652, body.split("\n"), size=10.5, fill=GRAY))

    # storage strip
    parts.append(rect(40, 700, 900, 56, fill="#eef4f2", stroke=BORDER, dash="4 3"))
    parts.append(text(56, 720, "$UH_HOME", size=11, weight=700, mono=True))
    parts.append(
        text(
            140,
            720,
            "SQLite FTS5 (sessions, messages) · config.toml · web-token · MEMORY.md · skills/ · uploads/ · dreams/",
            size=10.5,
            fill=GRAY,
        )
    )
    parts.append(text(56, 742, "$UHB", size=11, weight=700, mono=True))
    parts.append(
        text(
            140,
            742,
            "hub/ (modes, router, wiki) · runs/ · adapters/ · tools/ · journal des passes",
            size=10.5,
            fill=GRAY,
        )
    )
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# 2. Couches et points d'extension
# --------------------------------------------------------------------------- #
def diagram_layers() -> str:
    h = 780
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}">',
        DEFS,
    ]
    parts.append(rect(0, 0, W, h, fill=WHITE, stroke=WHITE, sw=0))
    parts.append(header(34, "Schéma 2 — couches et extension", "Étendre UH sans casser le contrat"))

    bands = [
        (
            "Présentation",
            "uh/web (app.py, sse.py, browser.py, static/) · console.py · shortcut.py",
            TEAL_SOFT,
            TEAL,
        ),
        (
            "Entrées CLI",
            "uh/cli.py · __main__.py · commands.py (slash) · onboarding.py",
            WHITE,
            BORDER,
        ),
        (
            "Orchestration",
            "agent.py · intent.py · mode_gate.py · context.py · decision.py · turnlog.py · roles.py",
            WHITE,
            BORDER,
        ),
        (
            "Capacités",
            "toolsets/ · mcp*.py · dream*.py · memory*.py · artifacts.py · attachments.py · audio/",
            WHITE,
            BORDER,
        ),
        (
            "Fournisseurs",
            "providers/ · router/ · capabilities.py · llm_params.py · keys.py · providers_admin.py",
            WHITE,
            BORDER,
        ),
        ("Persistance", "store.py (SQLite FTS5) · config.py (TOML) · $UH_HOME", "#eef4f2", BORDER),
    ]
    y = 80
    for name, modules, fill, stroke in bands:
        parts.append(rect(40, y, 570, 88, fill=fill, stroke=stroke))
        parts.append(text(58, y + 26, name, size=13, weight=700, fill=INK))
        parts.append(text(58, y + 50, modules[:96], size=10, fill=GRAY))
        if len(modules) > 96:
            parts.append(text(58, y + 66, modules[96:196], size=10, fill=GRAY))
        y += 100

    parts.append(text(640, 92, "POINTS D'EXTENSION", size=10.5, weight=700, fill=GOLD))
    parts.append(text(640, 108, "ce qu'on ajoute sans toucher au noyau", size=9.5, fill=GRAY))

    callouts = [
        (
            120,
            "Toolset",
            "déclaration JSON-Schema + registre ;\nle mode autorise ou non son exposition",
            "toolsets/",
        ),
        (
            216,
            "Provider",
            "classe openai_compat + clé écrite\nen TOML ; jamais dans le code",
            "providers/",
        ),
        (
            312,
            "Serveur MCP",
            "[[mcp.servers]] stdio · allowlist\n+ secrets isolés (mcp-secrets.toml)",
            "mcp.py",
        ),
        (408, "Cloud", "S3 / Drive via mcp_cloud.py ;\njeton lu hors du shell", "mcp_cloud.py"),
        (
            504,
            "Skills / hooks",
            "markdown que l'opérateur possède ;\ndésactivation mémorisée",
            "skills.py",
        ),
        (
            600,
            "Auto-réparation",
            "uh fix : diagnostic → patch →\nessai isolé → GO humain",
            "fixes.py",
        ),
    ]
    for y2, title, body, target in callouts:
        parts.append(rect(640, y2, 300, 82, fill=PANEL))
        parts.append(text(654, y2 + 22, title, size=11.5, weight=700, fill=TEAL))
        parts.append(lines(654, y2 + 40, body.split("\n"), size=9.8, fill=GRAY, lh=13))
        parts.append(text(654, y2 + 72, target, size=9, fill=GRAY, mono=True))
        parts.append(arrow(636, y2 + 41, 612, y2 + 41, color=GOLD))

    parts.append(rect(40, 700, 900, 60, fill=PANEL, stroke=BORDER))
    parts.append(text(58, 722, "Le contrat qui ne bouge pas", size=11, weight=700))
    parts.append(
        text(
            58,
            742,
            "1 dépendance runtime (httpx) · le navigateur ne parle qu'à UH · JSON-Schema pour chaque outil · "
            "fail-closed sur execute_code · aucune fonctionnalité « finie » sans mode d'échec visible",
            size=9.8,
            fill=GRAY,
        )
    )
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# 3. Cockpit : espace, panneaux, sécurité
# --------------------------------------------------------------------------- #
def diagram_cockpit() -> str:
    h = 780
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}">',
        DEFS,
    ]
    parts.append(rect(0, 0, W, h, fill=WHITE, stroke=WHITE, sw=0))
    parts.append(
        header(
            34,
            "Schéma 3 — cockpit UI/UX",
            "Trois colonnes, des panneaux, un flux de sécurité en six étapes",
        )
    )

    # frame
    parts.append(rect(30, 74, 920, 400, fill=PANEL, stroke=BORDER, rx=12))

    # sidebar
    parts.append(rect(44, 88, 180, 372, fill=WHITE))
    parts.append(text(58, 110, "Sessions", size=11.5, weight=700))
    parts.append(
        lines(
            58,
            130,
            ["Recherche FTS", "Dossiers", "liste des conversations", "fichier / glisser-déposer"],
            size=9.8,
            fill=GRAY,
            lh=16,
        )
    )
    parts.append(rect(58, 212, 152, 46, fill=TEAL_SOFT, stroke=TEAL, rx=6))
    parts.append(text(68, 232, "session ouverte", size=9.8, weight=700, fill=TEAL))
    parts.append(text(68, 248, "titre · date · modèle", size=9.2, fill=GRAY))
    parts.append(text(58, 430, "pied : provider · version", size=9.2, fill=GRAY))
    parts.append(text(58, 446, "⚙ Fournisseurs   ◐ thème", size=9.2, fill=GRAY))

    # main column
    parts.append(rect(236, 88, 470, 372, fill=WHITE))
    parts.append(rect(236, 88, 470, 40, fill=PANEL, stroke=BORDER, rx=8))
    parts.append(text(250, 105, "titre de session · badge de mode", size=10, weight=700))
    parts.append(
        text(250, 120, "modèle · rôle · jauge de contexte (payload / budget)", size=9, fill=GRAY)
    )
    parts.append(
        lines(
            250,
            152,
            [
                "bulles user / assistant",
                "cartes d'outils repliables (ok · refusé · erreur)",
                "artefacts : image, fichier, audio",
            ],
            size=9.8,
            fill=GRAY,
            lh=17,
        )
    )
    parts.append(text(250, 206, "○ barre de retour unique — prévu (lot 2)", size=9.2, fill=GOLD))
    parts.append(rect(250, 300, 442, 96, fill=PANEL, stroke=BORDER, rx=8))
    parts.append(text(262, 318, "compositeur", size=9.8, weight=700))
    parts.append(
        text(262, 336, "chips /tools  /web  /new  /clear  /help", size=9, fill=GRAY, mono=True)
    )
    parts.append(text(262, 352, "Écouter (synthèse vocale) · Envoyer", size=9, fill=GRAY))
    parts.append(text(262, 368, "pièce jointe", size=9, fill=GRAY))
    parts.append(text(342, 368, "○ Dicter · ○ caméra — prévus (lot 2)", size=8.8, fill=GOLD))
    parts.append(text(262, 386, "Ctrl/Cmd-K : palette de commandes", size=9, fill=TEAL))

    # right panel
    parts.append(rect(718, 88, 218, 372, fill=WHITE))
    parts.append(text(732, 108, "Panneaux (détachement : lot 2)", size=11, weight=700))
    tabs = [
        "Fournisseurs (API keys)",
        "Modèles",
        "Second brain UHB",
        "Mémoire (wiki, RAG, SiYuan)",
        "Décision (trace)",
        "Télémétrie",
        "Données / Connexions cloud",
        "Outils · MCP",
        "Infra (lecture seule)",
    ]
    for index, tab in enumerate(tabs):
        parts.append(text(732, 130 + index * 17, "· " + tab, size=9.4, fill=GRAY))
    parts.append(text(732, 306, "aujourd'hui : seul le panneau", size=9.2, fill=GRAY))
    parts.append(text(732, 322, "« Fournisseurs » peut flotter", size=9.2, fill=GRAY))
    parts.append(text(732, 342, "○ détacher · redocker · replier", size=9.2, fill=GOLD))
    parts.append(text(732, 358, "○ disposition versionnée + Réinitialiser", size=9.2, fill=GOLD))
    parts.append(text(732, 372, "thèmes clair / sombre", size=9.2, fill=GRAY))
    parts.append(text(732, 384, "contraste texte mesuré : 9,5:1 / 14,9:1", size=9.2, fill=GREEN))
    parts.append(
        text(732, 400, "bordure compositeur 4,4:1 (clair) → défaut suivi", size=9, fill=GOLD)
    )

    # security flow
    parts.append(text(30, 500, "FLUX UX DE SÉCURITÉ", size=10.5, weight=700, fill=GOLD))
    steps = [
        ("1. Jeton", "43 caractères\n$UH_HOME/web-token\n0600"),
        ("2. Cookie", "HttpOnly\nSameSite=Strict\nport-scoped"),
        ("3. CSP", "default-src 'none'\nscript-src 'self'\naucun inline"),
        ("4. Fenêtre", "--app Edge/Chrome\nprofil isolé\n$UH_HOME/web-app"),
        ("5. Bail", "battement 10 s\n45 s visible\n300 s réduite"),
        ("6. Sortie", "UHB /shutdown\npuis os._exit(0)\nport libéré"),
    ]
    x = 30
    for index, (title, body) in enumerate(steps):
        parts.append(rect(x, 512, 140, 84, fill=WHITE, stroke=TEAL if index in (2, 4) else BORDER))
        parts.append(text(x + 10, 532, title, size=10.5, weight=700, fill=TEAL))
        parts.append(lines(x + 10, 550, body.split("\n"), size=9, fill=GRAY, lh=12))
        if index < len(steps) - 1:
            parts.append(arrow(x + 140, 554, x + 156, 554, color=TEAL))
        x += 156

    parts.append(rect(30, 610, 920, 74, fill=PANEL))
    parts.append(text(46, 632, "Ce que la fenêtre ne fait jamais", size=11, weight=700))
    parts.append(
        text(
            46,
            652,
            "· parler directement à UHB depuis le navigateur (tout passe par UH, le CSP reste 'self')",
            size=9.6,
            fill=GRAY,
        )
    )
    parts.append(
        text(
            46,
            668,
            "· exécuter du code sans approbation par appel · afficher une capacité non mesurée · promettre « sandbox » au lieu de « dossier du run »",
            size=9.6,
            fill=GRAY,
        )
    )

    parts.append(rect(30, 696, 920, 62, fill=WHITE, stroke=BORDER, dash="4 3"))
    parts.append(text(46, 716, "Légende du dossier", size=10, weight=700))
    parts.append(pill(46, 724, "livré et prouvé", good=True, w=150))
    parts.append(pill(210, 724, "reste à faire", good=False, w=140))
    parts.append(
        text(
            366,
            722,
            "Chaque ligne de la feuille de route porte son jalon, sa PR ou son commit de preuve.",
            size=9.2,
            fill=GRAY,
        )
    )
    parts.append(
        text(
            366,
            738,
            "○ = capacité prévue au lot 2, non mesurée : elle n'est pas présentée comme livrée.",
            size=9.2,
            fill=GOLD,
        )
    )
    parts.append("</svg>")
    return "".join(parts)


def main() -> None:
    out = Path(__file__).resolve().parent / "diagrams"
    out.mkdir(parents=True, exist_ok=True)
    for name, builder in (
        ("01-flux-tour.svg", diagram_turn_flow),
        ("02-couches-extension.svg", diagram_layers),
        ("03-cockpit-ui.svg", diagram_cockpit),
    ):
        path = out / name
        # newline="\n": un SVG écrit avec des CRLF fait échouer
        # tests/test_line_endings.py dans l'arbre de travail. Le "\n" final est requis
        # par `end-of-file-fixer` : sans lui, chaque génération salissait l'arbre.
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(builder() + "\n")
        print(f"{path.name}: {path.stat().st_size} octets")


if __name__ == "__main__":
    main()
