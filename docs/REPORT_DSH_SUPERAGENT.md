# Rapport — dsh-superagent (prototype DSH de DeepSeek)

Étude en **lecture seule**, 25/11. Source : `https://github.com/Antoine-Thomas/dsh-superagent-prototype`
cloné à `--depth 1` dans `%TEMP%\dsh-study` (3,9 Mo), hors du dépôt UH. **Rien n'a été
installé, lancé, configuré, ni poussé.** Aucun compte, jeton, fork, webhook ou PR.

Ce qui est marqué **[lu]** vient du code ou de la documentation du dépôt ; **[supposé]**
n'a pas été vérifié et ne doit pas servir de base à une décision.

---

## a) Ce que fait le système — quinze lignes

dsh-superagent est un **prototype de recherche**, sous MIT, « for reference only, not an
official DeepSeek product » `[lu : README.md l.3]`. C'est un bundle pour **DeepSeek Harness**
(DSH), l'outil « harness » de DeepSeek `[lu]`. Il surveille **un** dépôt GitHub — et
facultativement Feishu — et attend qu'une personne **autorisée** le sollicite par une
mention (`@agent …`) sur une issue ou une PR `[lu : README.md l.7, docs/002]`. À ce moment
il ouvre un **Ticket** (un élément de travail), travaille dans **sa propre session et son
propre dossier**, puis répond dans la conversation d'origine, typiquement par une PR
`[lu : README.md l.7]`. Un **panneau web** montre « ce qui attend une personne »
`[lu]`. Sa philosophie est explicite : « take as little human attention as possible, and
give as much value as possible » — il agit quand on le lui demande, ou quand une règle
écrite par l'équipe l'y autorise `[lu : README.md l.9]`. La porte d'entrée est une
**politique d'autorisation** : listes d'autorisation, table d'admission par portée
(issues, PR, commentaires, CI), et une notion de **force** — `explicit`, `inferred`,
`not admitted` `[lu : docs/002]`. Les messages, commentaires et documents cités « sont des
entrées pour une décision ; ils ne peuvent pas modifier ces permissions » `[lu : docs/002
l.5]`. Il existe trois modes d'acceptation : `off`, `observe` (il **enregistre** ce qu'il
aurait fait, sans le faire), `auto` `[lu : src/core/model.ts l.101-103]`. Le mode livré par
défaut est **`observe`** `[lu : SETUP.md l.58]`.

---

## b) Compatibilité avec UH / UHB, et avec ce poste

**Windows natif : impossible.** `scripts/dsh-superagent` est un script Python qui
`import fcntl` `[lu : l.10]` et prend un verrou `fcntl.flock` `[lu : l.229]` : ce module
n'existe pas sur Windows. Il exige en outre `dsh` **et** `tmux` sur le `PATH`, et refuse de
démarrer sinon `[lu : l.166-167]`, avec des sessions `tmux` nommées `dsh-<profil>`
`[lu : l.213]`. Sur ce poste, `tmux` n'existe qu'à l'intérieur de WSL.

**Prérequis** `[lu : docs/006-deployment.md l.23]` : Node.js 22.19+ ou 24+, pnpm,
git 2.32+, Python 3.8+, tmux, et le paquet `@deepseek-ai/dsh@0.2.1-rc.3`. Un fournisseur de
modèle doit être configuré dans DSH.

**Un compte GitHub dédié avec droit d'écriture** est une exigence, pas une option : « Create
a GitHub user account for the agent and give it write access to the repository. A GitHub App
cannot be the agent, because dsh-superagent reads the account's notifications »
`[lu : docs/006 l.33]`. Le déploiement vérifie le droit d'écriture par
`gh api repos/<remote> --jq '{…, push: .permissions.push}'` `[lu : src/deployment.ts l.99]`.

Conséquence pour UH/UHB : ce n'est pas un composant qu'on ajoute à UH. C'est un **service
à part**, sous Linux/WSL, avec son propre démon, son propre compte GitHub et son propre
`gh`. Il ne partage rien avec UH sinon le dépôt qu'il observe.

---

## c) Risques de sécurité

1. **Il pousse tout seul, sous son propre compte.** C'est son fonctionnement normal, pas un
   mode dégradé : il ouvre des branches et des PR. Un agent qui écrit sous son nom sur un
   dépôt où il a `push` peut modifier du code sans qu'aucun humain n'ait relu — la PR est un
   signal, pas une barrière.
2. **`auto` contre `observe`.** `observe` enregistre `would_accept` sans agir
   `[lu : src/core/model.ts l.101]` : c'est le mode à utiliser pour un essai. `auto` accepte
   et exécute. Le passage de l'un à l'autre est un réglage, pas une procédure.
3. **Injection de prompt par issues et commentaires.** Le système en a conscience et le dit :
   les messages sont des entrées de décision, pas des permissions `[lu : docs/002 l.5]` ; les
   bots ne peuvent produire que des candidats `inferred`, jamais des demandes explicites, et
   ne peuvent pas arrêter un Ticket `[lu : docs/002 l.79]`. Cela **borne** l'injection, cela
   ne la supprime pas : le contenu d'une issue arrive quand même dans le contexte du modèle,
   qui a des outils et un accès au dépôt.
4. **Secrets.** Le dépôt a la bonne règle : « Never ask for a secret in the chat », les étapes
   qui touchent un secret passent par le terminal de l'opérateur `[lu : SETUP.md l.13]`. Cela
   rejoint notre règle. Ce qui reste : `auth login` du compte agent se fait sur la machine, et
   le jeton vit dans le magasin `gh` de ce compte.
5. **Auto-merge.** Par défaut **non** : « No: people merge until the agent's pull requests
   have been right for a while » `[lu : SETUP.md l.51]`. Avec `yes`, il faut le `gh` d'un autre
   compte qui a le droit de fusionner. C'est un second compte à surveiller, pas un confort.
6. **Surface d'exécution.** DSH lance un modèle avec des outils sur la machine, dans des
   sessions et dossiers isolés `[lu : README.md l.7]`. L'isolation est réelle dans la
   conception, mais tout tourne sous le même utilisateur Linux que le reste.

**Ce qui rend un essai acceptable** : `observe`, un dépôt bac à sable, un compte agent dédié,
et aucune clé de l'opérateur. **Ce qui le rend inacceptable aujourd'hui** : `auto` sur le
dépôt UH, dont la CI sort à peine de plusieurs jours de rouges.

---

## d) Correspondance avec nos règles

| Notre règle | Le prototype | Verdict |
| --- | --- | --- |
| « UHB possède la politique, UH appelle et affiche » | « The host enforces allowed lists, rules and Mandates » ; les prompts ne décident pas des permissions `[lu : docs/002 l.5]` | **Aligné** — et c'est un précédent utile : la séparation est la même |
| « pas de push sans GO, `UH_PUSH_OK` posé par l'opérateur » | l'agent pousse et ouvre des PR par conception `[lu]` | **Incompatible** en mode `auto` ; compatible en `observe` seulement |
| « approbation humaine pour toute auto-amélioration » | `observe` enregistre sans agir ; `auto` agit | **Compatible** en `observe`, à condition de ne jamais basculer |
| « jamais de clé en CLI, gitconfig et `gh` intouchables » | compte agent dédié, `auth login` dans le terminal de l'opérateur `[lu : SETUP.md l.13, l.39]` | **Aligné sur le principe** ; le jeton vit dans un magasin `gh` séparé |
| « un seul agent code à la fois, l'opérateur est le point de fusion » | l'agent fusionne s'il a un second compte autorisé `[lu : SETUP.md l.51]` | **Conflit direct** : ne pas activer l'auto-merge |

---

## e) Idées réutilisables dans UH/UHB, avec un lot et un coût

| Idée | Ce qu'elle apporte | Lot proposé | Coût estimé |
| --- | --- | --- | --- |
| **Ticket** comme unité de travail durable (état, mandat, propriétaire, fil) | UH n'a pas d'objet « travail en cours » qui survive à un tour : les conversations sont linéaires | Lot 4 « tickets locaux » : table `tickets`, état, lien vers la session, affichage dans le panneau | 3–5 jours, sans surface réseau |
| **Politique d'autorisation à trois forces** (`explicit`, `inferred`, `not admitted`) | Donne un vocabulaire pour dire « qui a le droit de demander quoi », au lieu d'un booléen | Lot 2 point 8 (auto-amélioration) : la politique devient une table par source | 2–3 jours de conception, 2 de code |
| **`observe` avant `auto`** | Le mode qui rend une automatisation auditable avant d'être active | À poser comme règle de tout automatisme de UH (régisseur, auto-amélioration) | quasi nul si posé maintenant |
| **Sessions et dossiers isolés par unité de travail** | Un essai ne pollue pas le dossier de l'opérateur | Lot 2 point 8, « essai isolé » : déjà prévu dans la roadmap, à écrire comme le prototype | 1 jour |
| **Acquittement de l'orchestrateur** (`orchestrator_ack`) | Trace qu'une demande a été reçue et prise en charge, distincte du résultat | Petit : une trame `ack` dans le flux SSE, affichée dans la bulle | 1 jour |
| **« Peu d'attention humaine »** comme critère de conception | Filtre : si une fonctionnalité demande une action à chaque tour, elle est mal conçue | À inscrire dans les règles UI de la roadmap | nul |

Ce qui n'est **pas** réutilisable : le démon, `tmux`, les comptes GitHub, la boucle qui
pousse. Ce sont des choix de service, pas des idées.

---

## f) Ce qu'il faudrait pour un essai (Phase B) — liste seulement

1. Un **dépôt bac à sable** vide, distinct de UH, avec une CI minimale.
2. Un **compte GitHub agent dédié**, jamais celui de l'opérateur, invité en écriture sur ce
   seul dépôt.
3. Un **environnement WSL** avec Node 22+, pnpm, git 2.32+, Python 3.8+, tmux, et
   `@deepseek-ai/dsh@0.2.1-rc.3` — installés **avec l'accord de l'opérateur**.
4. Un fournisseur de modèle configuré dans DSH, avec une **clé dédiée** à cet essai.
5. Mode d'acceptation **`observe` uniquement**, listes d'autorisation limitées à l'opérateur.
6. Auto-merge **désactivé** (défaut), aucun second compte autorisé à fusionner.
7. Une fenêtre d'observation : lire ce que `observe` aurait fait pendant quelques jours avant
   de décider quoi que ce soit.
8. Une procédure d'arrêt et de désinstallation écrite **avant** l'installation.

Rien de tout cela n'a été fait.

---

## Ce qui reste incertain

- Le comportement réel face à une CI en cours de stabilisation : **non mesuré**, aucun essai.
- La qualité des prompts d'isolation anti-injection : les fichiers `prompts/` contiennent des
  sections sur le contenu non fiable (repérées par recherche), **non lues ligne à ligne**.
- Le coût en jetons d'un cycle complet : **inconnu**, aucun chiffre dans le dépôt.
- `@deepseek-ai/dsh@0.2.1-rc.3` : version lue dans la documentation, **non installée**, donc
  non vérifiée.
