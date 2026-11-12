# Jalon 7 — phase 0 : analyse et plan

Point de départ : `fa10677`, tag `v0.8.0`, arbre propre, CI 20 cellules vertes
(18 `ci` + 2 `lint`). **Aucun code produit dans ce document** : il décrit l'état
des lieux, les décisions proposées, les risques, un découpage et les questions à
trancher avant d'écrire la moindre ligne.

Sources lues : `uh/` et `docs/` du dépôt, `gh api` en lecture seule,
`C:/Users/searc/hermes-home-vision` (dépôt privé local, commande `uhb`),
`C:/Users/searc/AppData/Local/hermes/hermes-agent/hermes_cli/commands.py`.

---

## A. Panneau « Fournisseurs » dans `uh web`

### A.1 État des lieux (mesuré)

| Élément | Aujourd'hui |
| --- | --- |
| Clés | CLI seulement : `uh providers add`, `uh providers free`, `uh keys` invisible en web |
| Route `/api/status` | renvoie `provider`, `model`, `terminal`, `workspace` — **aucune** information de clé |
| Politique distante | `allow_remote` / `UH_ALLOW_REMOTE` / `--local-only`, consentement dans `config.toml` |
| Sécurité web | jeton par processus (cookie HttpOnly SameSite=Strict), contrôle `Host`/`Origin`, CSP `default-src 'none'`, pas de script inline, `X-Frame-Options: DENY` |
| Stockage | `$UH_HOME/keys.toml`, multi-clés (`keys = [...]`), 0600 best-effort |
| Écriture de clé | `uh/keys.py` : `add_provider`, `add_key`, `remove_key` — atomique (temp + replace) |

Conséquence : tout le modèle de confiance existe déjà, il manque la surface HTTP
et le panneau. Le risque n'est pas le transport (loopback + jeton) mais
**l'écriture d'un secret depuis un navigateur** : une réponse qui renvoie une clé,
un journal qui la capture, ou une page qui accepte d'en écrire depuis une
interface exposée sur le réseau.

### A.2 Décisions proposées

**D-A1 — Les clés sont en écriture seule, sans exception.**
Aucune route ne renvoie une clé, même tronquée, même masquée partiellement
(`sk-ab…` est déjà une fuite d'information). `GET /api/providers` renvoie pour
chaque fournisseur `{name, base_url, model, keys: <nombre>, verified, in_chain}`.
`POST /api/providers/{name}/keys` accepte `{"key": "..."}` et répond
`{"stored": true, "keys": 3}`. `DELETE /api/providers/{name}/keys/{index}`
supprime par **index**, jamais par valeur (sinon la clé serait dans l'URL, donc
dans les journaux d'accès d'uvicorn — c'est exactement le piège à éviter).

**D-A2 — Refus d'écriture hors loopback.**
Si `uh web` a été lancé avec `--host` non loopback, les routes d'écriture de clé
(`POST`/`DELETE` …/keys, `POST /api/providers/custom`) répondent **403** avec un
message explicite, sauf si l'utilisateur ajoute `--allow-remote-key-entry`
(nouveau drapeau, nommé pour qu'on ne l'active pas par accident). Lire l'état
reste permis : il ne contient aucun secret. Justification : un jeton dans une URL
peut fuiter par l'historique du navigateur ; une clé écrite depuis une interface
joignable sur le LAN est un cran plus grave.

**D-A3 — Fournisseur personnalisé : validation stricte.**
- `https://` obligatoire, sauf hôte loopback (`127.0.0.1`, `localhost`, `::1`) ;
- schéma `http`/`https` uniquement (pas de `file://`, `ftp://`) ;
- `follow_redirects=False` ; un 3xx est refusé plutôt que suivi (une redirection
  vers un autre hôte est le vecteur classique d'exfiltration de clé) ;
- timeout 10 s, réponse maximale bornée ;
- le nom suit la contrainte existante `^[a-z0-9][a-z0-9_-]{0,31}$` ;
- un fournisseur personnalisé n'entre dans la chaîne que si `verified` n'est pas
  requis : il est marqué `custom` et **hors chaîne automatique** tant que
  l'utilisateur ne l'active pas explicitement (même règle que `partial` au 6a).

**D-A4 — Test de connexion sans contenu de prompt.**
`POST /api/providers/{name}/test` envoie un `POST /chat/completions` minimal :
`{"model": "<modèle>", "messages": [{"role":"user","content":"ping"}],
"max_tokens": 1}`, timeout 10 s, et renvoie `{ok, http_status, error_kind}`.
Aucune donnée de session, aucun historique, aucun contenu utilisateur. Le
résultat n'est jamais journalisé avec l'en-tête `Authorization`.

**D-A5 — `allow_remote` et consentement pilotés depuis le panneau.**
`GET /api/router/settings` → `{allow_remote, consent_date, strategy}`.
`PUT /api/router/settings` accepte `{allow_remote: bool, consent: bool}` : passer
`allow_remote` à vrai **exige** `consent: true` dans la même requête, et écrit
`remote_consent` via `uh.router.policy.record_consent()`. L'avertissement de
confidentialité existant est affiché dans le panneau avant la case à cocher — pas
de consentement implicite.

**D-A6 — Rien de nouveau dans les journaux.**
uvicorn journalise méthode + chemin + statut : les chemins ne contiennent donc
jamais de clé (d'où la suppression par index). Un middleware ne lit pas les
corps. Les erreurs renvoyées au navigateur sont des messages classifiés
(`auth`, `timeout`, …), jamais le corps de la requête amont.

**D-A7 — Formats natifs : jalon séparé.**
Le panneau ne gère que l'API compatible OpenAI. Anthropic (`/v1/messages`,
en-tête `x-api-key`, blocs de contenu) et Gemini (`generateContent`) demandent un
traducteur de messages et de tools : c'est un jalon à part (7d, plus tard), pas
une variante de formulaire. Le catalogue 6a est déjà OpenAI-only, la cohérence est
donc gratuite.

### A.3 Risques

| Risque | Parade |
| --- | --- |
| Fuite de clé dans une réponse ou une erreur | tests qui sérialisent **toutes** les réponses et cherchent la valeur ; suppression par index |
| Fuite dans les journaux | jamais dans l'URL ; pas de log de corps ; test sur le fichier de log après un ajout |
| XSS via nom de fournisseur/modèle | rendu par `textContent`, CSP sans `unsafe-inline` |
| SSRF via `base_url` personnalisé | https obligatoire, pas de redirection, timeout court ; reste possible vers un service local : assumé pour un outil local, documenté |
| Écriture de clé depuis une interface exposée | 403 hors loopback (D-A2) |
| Panneau qui échoue silencieusement | chaque action renvoie un statut explicite, aucune écriture partielle (réutilisation de l'écriture atomique existante) |

### A.4 Découpage

| Sous-jalon | Contenu | Critère de réussite mesurable |
| --- | --- | --- |
| **7a.1** | `GET /api/providers`, `GET /api/router/settings`, panneau en lecture + onglet | aucune réponse ne contient de clé (test qui injecte une clé connue et sérialise les routes) ; 401 sans jeton |
| **7a.2** | `POST`/`DELETE` des clés, rafraîchissement du nombre | l'ajout d'une clé change `keys: n` en `n+1` et le fichier `keys.toml` ; aucune réponse ni ligne de log ne contient la valeur ; DELETE par index |
| **7a.3** | Fournisseur personnalisé + `POST …/test` | `http://` hors loopback refusé ; redirection 302 vers un autre hôte refusée ; timeout simulé renvoie `timeout` ; le test n'envoie que « ping » |
| **7a.4** | `allow_remote` + consentement | sans `consent: true`, `allow_remote=true` renvoie 400 et n'écrit rien ; avec consentement, `config.toml` porte `remote_consent` |

Effort : **2 à 3 jours** (API + tests ≈ 1,5 j, panneau ≈ 1 j, relecture ≈ 0,5 j).

### A.5 Questions à trancher

1. `--allow-remote-key-entry` : nécessaire, ou on refuse carrément l'écriture de
   clé hors loopback sans échappatoire ? (ma recommandation : refus sec, l'option
   n'existe que si tu veux un jour piloter UH depuis une autre machine)
2. Le test de connexion : « ping » suffit, ou tu veux pouvoir tester un modèle
   précis du catalogue (liste déroulante) ?
3. Le panneau doit-il permettre de **supprimer un fournisseur personnalisé**
   entier (pas seulement sa clé) ? Cohérent avec `uh providers remove`, mais
   destructif depuis un navigateur.

---

## B. Parité des commandes (console et CLI)

### B.1 État des lieux

**UH aujourd'hui** — 17 commandes slash (registre `uh/commands.py`) :
`/help /model /provider /tools /clear /new /sessions /resume /history /retry
/title /status /config /doctor /memory /version /exit` ; côté CLI :
`uh doctor`, `uh model list|pull`, `uh chat`, `uh sessions …,` `uh config …`,
`uh memory …`, `uh providers list|add|remove|free`, `uh router status`, `uh web`,
`uh --local-only`.

**`uhb` (dépôt privé `hermes-home-vision`, paquet `uhb`)** — 14 commandes :
`status`, `config list|get|set`, `modes list|show`, `execute`, `run`, `docs`,
`brain status|search|start|read`, `ui`, `health`, `providers`, `update`.
Caractéristique : pivot « routeur + modes + point d'entrée HTTP », **aucune
gestion de clé** (credentials refusés dans `config set`).

**Registre Hermes** — 105 commandes, dont les familles utiles ici :
`model`, `tools`, `toolsets`, `plugins`, `skills`, `config`, `status`, `usage`,
`login`, `subscription`, `topup`, `whoami`, `memory`, `sessions`, `resume`,
`retry`, `undo`, `rewind`, `save`, `export`, `compress`, `context`, `doctor`,
`insights`, `providers`-like via `platform(s)`.

### B.2 Tableau des écarts

| Commande | Source | UH a | Décision | Raison |
| --- | --- | --- | --- | --- |
| `status` | uhb, Hermes | `/status` ✔ | **retenu** (déjà) | — |
| `providers` (lister) | uhb | `/provider` ✔ | **retenu** (déjà) | libellé différent, même rôle |
| `providers` (ajouter une clé) | — | ✖ | **retenu → 7b.1** | `/key add <provider>` en console + `uh providers add` existant |
| `providers` (tester) | — | ✖ | **retenu → 7b.1** | `uh providers test <name>` et `/provider test` |
| `config list|get|set` | uhb | `/config` ✔ | **retenu** (déjà) | — |
| `modes list|show` | uhb | ✖ | **reporté (7e)** | les modes d'exécution de UHB n'existent pas dans UH ; les importer suppose le contrat de harness, hors périmètre |
| `execute` | uhb | `uh chat --once` ✔ | **écarté** | redondant |
| `run` (point d'entrée HTTP) | uhb | ✖ | **reporté (6b)** | c'est exactement `uh router serve`, déjà planifié |
| `ui` | uhb | `uh web` ✔ | **écarté** | redondant |
| `health` | uhb | `/doctor` ✔ | **écarté** | même rôle |
| `brain status|search` | uhb | ✖ | **écarté** | second cerveau = autre dépôt (décision Jalon 6) |
| `docs` | uhb | ✖ | **retenu → 7b.2** | `uh docs` ouvrant `docs/` local, 10 lignes |
| `update` | uhb | ✖ | **écarté** | distribution PyPI : `pip install -U` |
| `login` / `subscription` / `topup` / `whoami` | Hermes | ✖ | **écarté** | suppose un compte et une facturation, contraires au modèle local-first |
| `plugins` / `skills` / `toolsets` | Hermes | `/tools` partiel | **reporté (7f)** | plugins de toolsets = chantier séparé |
| `undo` / `rewind` / `compress` / `context` | Hermes | ✖ | **reporté** | `undo` est simple et utile ; `rewind`/`compress` demandent un historique indexé |
| `save` / `export` | Hermes | ✖ | **retenu → 7b.3** | exporter une session en Markdown/JSON depuis SQLite : peu de code, très utile |
| `usage` / `insights` | Hermes | ✖ | **écarté** | suppose une télémétrie de coûts ; UH n'a pas de comptabilité (écart 6a accepté) |
| `model`, `memory`, `sessions`, `resume`, `retry`, `title`, `version`, `exit`, `clear`, `new`, `help`, `tools`, `doctor` | Hermes | ✔ | **retenu** (déjà) | couvert au Jalon 4 |

Commandes à proposer pour que la console et le web offrent **les mêmes actions**
(nouveau, 7b.1) :

```
/provider add <name>        # assistant clé (saisie masquée), comme `uh providers free`
/provider test <name>       # test de connexion, sans contenu de prompt
/key list | add | remove    # alias explicite de /provider pour les clés
/router                     # état du pool : clés, cooling, dernier type d'erreur
/router reset               # remet à zéro refroidissements et disjoncteurs
/local-only                 # bascule de session, sans redémarrer
```

Ces six commandes sont le miroir exact du panneau web : même code (`keys.py`,
`router.pool`), deux interfaces. Critère : chaque action du panneau a une
commande console équivalente, vérifié par un test qui compare les deux registres.

### B.3 Découpage

| Sous-jalon | Contenu | Critère |
| --- | --- | --- |
| **7b.1** | `/provider add|test`, `/key list|add|remove`, `/router [reset]`, `/local-only` | ajout d'une clé depuis la console → `keys.toml` à jour, aucune clé affichée ; `test` classifie `auth`/`timeout` |
| **7b.2** | `uh docs [--open]` | ouvre `docs/` local, aucune requête réseau |
| **7b.3** | `/save md|json`, `/export` | export d'une session SQLite vers un fichier, contenu identique à `uh sessions show` |

Effort : **1 jour** (7b.1 ≈ 0,5 j, 7b.2 + 7b.3 ≈ 0,5 j).

### B.4 Questions

1. `/key` et `/provider` en doublon assumé, ou une seule famille (`/provider`) ?
2. `undo` : on le prend maintenant (simple) ou on attend le jalon mémoire ?
3. `/save` : Markdown seul, ou Markdown + JSON pour la reproductibilité ?

---

## C. Dépôt, Git et GitHub

### C.1 État des lieux (mesuré aujourd'hui, lecture seule)

| Élément | État |
| --- | --- |
| Protection de `main` | **absente** — `GET /branches/main/protection` → 404 « Branch not protected » |
| Environnements GitHub | `pypi` et `testpypi` existent, **sans aucune règle** (`protection_rules: []`) → pas d'approbation manuelle |
| Description du dépôt | `null` |
| Topics | `[]` |
| Wiki / Discussions | wiki activé, discussions désactivées |
| Dependabot | alertes **désactivées**, aucun `.github/dependabot.yml` |
| Secret scanning | API interrogée : `[]` (aucune alerte) ; push protection non vérifiée |
| `SECURITY.md` | absent |
| `CODE_OF_CONDUCT.md` | absent |
| `CHANGELOG.md` | absent (les notes de version vivent dans les releases GitHub, et seules 3 releases existent) |
| `CODEOWNERS` | absent |
| Modèles d'issue / PR | absents |
| `THIRD_PARTY_NOTICES.md` | absent — justifié : **aucune** dépendance JS tierce, le badge shields est une image distante |
| Workflows | `ci` (18 cellules), `lint` (2), `publish` (3 jobs chaînés) |
| Tags | `v0.1.0 … v0.6.0` annotés ; `v0.7.0`, `v0.7.1`, `v0.7.2` **légers et absents du clone local** (créés par l'interface de release) ; `v0.7.3`, `v0.8.0` annotés |
| Releases GitHub | `v0.1.0`, `v0.1.1`, `v0.7.2` — `v0.7.3` et `v0.8.0` n'en ont pas encore |

### C.2 Ce que je peux faire en fichiers (7c.1, ≈ 0,5 j)

- `SECURITY.md` : version supportée, canal de signalement privé (GitHub Security
  Advisories, *Security → Report a vulnerability*), délai de réponse annoncé,
  périmètre (clés, jeton web, exécution d'outils).
- `CHANGELOG.md` : format Keep a Changelog, une section par version publiée, en
  reprenant les commits de release (0.1.0 → 0.8.0). Généré depuis `git log`, pas
  inventé.
- `.github/dependabot.yml` : `pip` (hebdomadaire, groupé) + `github-actions`
  (hebdomadaire).
- `.github/ISSUE_TEMPLATE/{bug,feature}.yml` + `.github/PULL_REQUEST_TEMPLATE.md`
  (checklist : `pytest`, `ruff`, `mypy`, pas de secret, pas de nouvelle
  dépendance runtime).
- `CODE_OF_CONDUCT.md` : Contributor Covenant 2.1, contact = le mainteneur.
- `THIRD_PARTY_NOTICES.md` : court — aucune dépendance JS, dépendances Python
  sous licences permissives (httpx BSD, fastapi MIT, uvicorn BSD, rich MIT,
  tomli MIT).
- Note dans `docs/RELEASING.md` : les releases GitHub sont la source des notes de
  version, et un tag léger créé depuis l'interface est à éviter.

**Non retenu pour l'instant** : `CODEOWNERS` (un seul mainteneur, aucune valeur
tant qu'il n'y a pas de PR externe), Discussions (personne pour y répondre),
`FUNDING.yml` (pas de canal), refonte du wiki (à désactiver plutôt).

### C.3 Ce que toi seul peux faire dans l'interface GitHub (7c.2, ≈ 20 min)

1. **Protection de `main`** — *Settings → Branches → Add branch protection rule* :
   motif `main`, cocher « Require a pull request before merging », « Require
   status checks to pass » avec `ci / ubuntu-latest / py3.11` et `lint / ruff +
   mypy`, « Require branches to be up to date », « Do not allow bypassing ».
   Conséquence à accepter : tes propres poussées directes passeront par une PR.
2. **Approbation de l'environnement `pypi`** — *Settings → Environments → pypi →
   Required reviewers* (toi). Aujourd'hui `protection_rules: []` : le job PyPI
   **ne s'arrête pas** pour approbation. C'est le point le plus important de la
   liste.
3. **Description et topics** — bouton ⚙ *About* : description
   (« Provider-agnostic agent harness: local-first CLI, free providers, optional
   web UI »), topics `agent`, `llm`, `harness`, `ollama`, `cli`, `python`,
   `local-first`, `openai-compatible`. Désactiver le wiki.
4. **Dependabot alerts** — *Settings → Code security → Dependabot alerts: Enable*
   (et *Dependabot security updates*).
5. **Secret scanning + push protection** — même écran, activer les deux (dépôt
   public : gratuit).
6. **Notes de release** — *Releases → Draft a new release* en cochant « Set as the
   latest release » et en générant les notes automatiquement ; c'est aussi ce qui
   déclenche `publish.yml`.
7. **Tags légers** — décider : on laisse `v0.7.0/0.7.1/0.7.2` tels quels (ils
   correspondent à des versions publiées, on n'y touche pas) et on documente que
   tout nouveau tag est annoté. Aucune suppression, aucun déplacement.

### C.4 Ce qui ne vaut pas la peine maintenant

- `CODEOWNERS`, Discussions, wiki maintenu, `FUNDING.yml`, release drafter
  automatique (une release par version, faite à la main, suffit à ce rythme).
- Signer les artefacts (Sigstore) : à faire quand il y aura des utilisateurs.
- Badge de couverture : demanderait Codecov et un jeton tiers ; un badge local
  « tests: 213 » serait faux dès le lendemain.

### C.5 Questions

1. Protection de `main` stricte (PR obligatoire, même pour toi) ou souple
   (checks obligatoires mais poussée directe autorisée) ?
2. `CHANGELOG.md` : je le génère depuis les commits de release en français ou en
   anglais ? (le dépôt est en anglais, je propose anglais.)
3. Environnement `pypi` : reviewer obligatoire = toi seulement, ou tu veux aussi
   que la publication TestPyPI demande une approbation ?

---

## Ordre proposé et effort total

| Étape | Pourquoi d'abord | Effort |
| --- | --- | --- |
| **7c.1 + 7c.2** (dépôt et GitHub) | protège tout le reste, aucun code produit, tu peux faire ta part en 20 min | 0,5 j |
| **7a** (panneau web) | le plus délicat : sécurité des clés ; le faire avant que d'autres surfaces n'en dépendent | 2–3 j |
| **7b** (parité console) | réutilise le modèle d'API de 7a, donc après | 1 j |
| **7d/7e/7f** | formats natifs, modes UHB, plugins de toolsets : hors périmètre immédiat | — |

Total phase 1 : **3,5 à 4,5 jours** de travail effectif, découpés en commits
vérifiables (un sous-jalon = un commit = une CI verte).

## Ce que je ne peux pas faire ou vérifier moi-même

- Le **chemin HTTP réel** d'un fournisseur gratuit : toujours pas testé (aucune
  clé, et tu ne dois pas en coller une ici). L'essai réel reste à faire par toi
  avant de considérer 6a comme validé en production.
- La **push protection** : l'API ne l'expose pas clairement ; l'écran *Code
  security* est la seule source fiable.
- Les **règles d'environnement** : je les lis, je ne les écris pas (pas de
  `gh api` en écriture, comme demandé).
