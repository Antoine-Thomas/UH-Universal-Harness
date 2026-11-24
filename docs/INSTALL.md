# Installing and upgrading UH

UH is a Python package (`uh-harness`) with a console entry point (`uh`). Nothing
else is required: no administrator rights, no service, no registry key, no startup
entry. Everything it stores lives in `$UH_HOME` (`~/.uh` by default) and the only
thing written outside it is the Desktop shortcut, if you ask for one.

## Install

```bash
python -m pip install uh-harness
uh doctor            # python, httpx, git, a local model or a provider key, the store
uh web               # the cockpit, at http://127.0.0.1:8765
```

From a checkout, for development:

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uh --version
```

## Upgrade

```bash
uh upgrade             # clean pass: stops UH processes, purges the pip cache,
                       # reinstalls with --force-reinstall --no-cache-dir,
                       # refreshes the Desktop shortcut
uh upgrade --no-clean  # plain `pip install --upgrade`, touches nothing else
uh upgrade --dry-run   # prints the command and changes nothing
```

Two rules that explain most support questions:

- **`python -m pip` installs for the `python` on your `PATH`.** Use the same
  interpreter you launch UH with: `uh --version` and
  `python -c "import uh; print(uh.__file__)"` must agree.
- **`--user`** is only for a user-level install outside a virtualenv.

## Stop UH and its second brain (without touching Wazuh)

Closing the browser window does **not** stop `uh web`, and a `uhb.exe` started by a
previous instance survives on its own. Use **Quitter UH** (the power button in the
Configuration header), `uh web --quit-existing`, or this script. It is deliberately
narrow: it only looks at the owners of the UH ports and at the children whose
command line says `uhb`, and it skips anything whose path mentions `wazuh`/`ossec`
(a SIEM agent must never be killed by a UH cleanup).

```powershell
# Owners of the ports UH uses (8765-8775), then their uhb children.
$ports  = 8765..8775
$owners = foreach ($p in $ports) {
  Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue |
    ForEach-Object { $_.OwningProcess }
}
foreach ($id in ($owners | Sort-Object -Unique)) {
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
  if (-not $proc) { continue }
  if ([string]$proc.ExecutablePath -match 'wazuh|ossec') { Write-Host "skip (wazuh) $id"; continue }
  if ([string]$proc.CommandLine -notmatch '(?i)(\buh(\.exe)?\b|-m\s+uh\b|uhb)') { Write-Host "skip (not UH) $id"; continue }
  Get-CimInstance Win32_Process -Filter "ParentProcessId=$id" |
    Where-Object { [string]$_.CommandLine -match '(?i)uhb' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
  Write-Host "stopped $id ($($proc.ExecutablePath))"
}
Remove-Item (Join-Path $HOME '.uh\uhb.pid') -ErrorAction SilentlyContinue
```

Never use `Get-Process pythonw | Stop-Process -Force`: it kills the Hermes gateway,
any other Python tray app, and it is how a "cleanup" becomes an incident.

### Which copy is running?

Three places answer it, and they must agree:

- `uh doctor` prints an `install` line: `dépôt (éditable) : …` or `paquet installé :
  …`, and warns when the command you just ran comes from site-packages while the
  current folder is a checkout;
- `/api/status` carries `install: {package, root, editable, python}`;
- the sidebar footer shows `dépôt · 1.7.1` or `paquet installé · 1.7.1`, with the
  full path and the interpreter in its tooltip.

If the footer says `paquet installé` while you are working in a checkout, the fixes
you just built are not the code your browser is running.

### Two installs, two products

`uh web` prints its version and the file it serves. If
`Get-Command uh` answers `...\Python3xx\Scripts\uh.exe` while you work from a
checkout, the shortcut runs the **installed** package, not your code. Make the
installed entry point point at the checkout:

```powershell
python -m pip install -e . --no-deps    # from the checkout
python -c "import uh; print(uh.__file__)"   # must be inside the checkout
```

A second `uh web` on another port is no longer a silent fallback: it says which
instance already holds 8765 and exits 0 (`/healthz` is how it knows).

### Le raccourci qui lance le dépôt (option `--from-checkout`)

L'autre route, sans toucher au Python du système : le lanceur **épingle**
l'interpréteur du dépôt au lieu de chercher `uh` dans le PATH.

```powershell
# depuis le dépôt, avec l'interpréteur qui a servi à l'installer
uh install-shortcut --from-checkout
```

Le fichier généré (`$UH_HOME\uh-web.ps1`) contient alors deux lignes qui disent
tout ce qu'il fera :

```powershell
$uhPython = 'C:\...\UH-Universal-Harness\.venv\Scripts\python.exe'
& $uhPython -m uh.cli web --app @args
```

Il n'y a plus de `Get-Command uh` : soit cet interpréteur existe et il démarre,
soit le lanceur affiche son chemin et s'arrête. C'est ce qui garantit que la
fenêtre montre le code du dossier — le pied de page affiche `dépôt` avec le
chemin du paquet et le commit (`/api/setup` : `origin`, `commit`, `dirty`),
et non `paquet installé · <version>`.

**Réversible en une commande**, sans rien réinstaller :

```powershell
uh install-shortcut          # même fichier, résolution par le PATH à nouveau
```

Le raccourci du Bureau ne bouge pas, aucun réglage système n'est modifié, rien
ne démarre au démarrage de la session.

**Vérifier** ce que le lanceur exécute vraiment :

```powershell
Select-String -Path "$HOME\.uh\uh-web.ps1" -Pattern 'uhPython|uh.Source'
$env:PYTHONPATH = ''
C:\ ; <l'interpréteur affiché ci-dessus> -c "import uh; print(uh.__file__)"
```

Lancer cette vérification **hors du dépôt** est essentiel : depuis la racine,
Python ajoute le dossier courant à `sys.path` et importe le `uh` d'à côté quel
que soit l'interpréteur. Mesuré le 24/11 sur ce poste — les deux interpréteurs
donnent la même réponse depuis le dépôt, et deux réponses différentes depuis
`C:\Users\searc` :

```
.venv\Scripts\python.exe            -> C:\...\UH-Universal-Harness\uh\__init__.py
Python310\python.exe (le PATH)      -> C:\...\Python310\lib\site-packages\uh\__init__.py
```

Le test `tests/test_shortcut.py::test_the_pinned_interpreter_loads_this_checkout`
fait exactement cette mesure, hors du dépôt, et échoue si l'interpréteur annoncé
par le lanceur n'est pas celui qui charge le dépôt.

## Clean manual upgrade (Windows)

`uh upgrade` does this itself. Run the sequence by hand when it cannot: pip answers
**`WinError 32`** (the file is used by another process), the entry point refuses to
start, or the UI keeps serving an old version after an upgrade.

Open **PowerShell** (an administrator shell is not required unless UH itself was
installed machine-wide) and run, in this order:

```powershell
# 1. Stop every UH process. They hold uh.exe and the web port, which is exactly
#    what blocks pip ("WinError 32"). Matched on our module and our exe only -
#    another python.exe is somebody else's program.
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq 'uh.exe' -or $_.CommandLine -like '*-m uh.cli*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# 2. Drop pip's cache: it can hand back an old wheel instead of the new version.
python -m pip cache purge

# 3. Uninstall the old version, so no half-replaced file survives the reinstall.
python -m pip uninstall uh-harness -y

# 4. Reinstall the latest release without touching the cache.
python -m pip install --force-reinstall --no-cache-dir uh-harness

# 5. Rebuild the Desktop shortcut: it points at a generated script
#    (~/.uh/uh-web.ps1) that carries a version line, and an old one keeps the old
#    flags (no --app, no "stop the previous uh web").
uh install-shortcut

# 6. Prove it: the version must be the one you expect, and the import must come
#    from the install you just wrote.
uh --version
python -c "import uh, sys; print(uh.__version__, uh.__file__, sys.version.split()[0])"
```

Then start it again with the Desktop shortcut (or `uh web`).

### If it is still wrong

- **`uh` is not recognised** after the reinstall: the `Scripts` directory of that
  interpreter is not on `PATH`. Launch it as `python -m uh.cli` to confirm the
  package is there, then fix `PATH` (or reinstall with `--user`).
- **The version is right but the UI looks old**: the page is stamped (`app.js?v=…`)
  and compares itself with `/api/status`; a mismatch shows a banner naming both
  versions. Close the window and relaunch — a running `uh web` keeps serving its
  own version.
- **The port is busy**: `uh web` moves to the next free port and says so; with an
  explicit `--port` it refuses with one line instead of dying with `exit 3`.
- **`WinError 32` comes back**: something outside UH holds the file (an IDE, an
  antivirus scan, a terminal with the exe open). Close it, or reboot and reinstall
  in the same session.
- **`Ignoring incompletely removed distribution ~h_harness-…`**: an older install
  was interrupted and pip ignores that folder. Delete the `~h_harness-*` directory
  from `site-packages` before reinstalling, or the version you read is not the
  version you run.

## Where to connect things

Two tabs, two jobs - and only one of them connects anything:

| Tab | What it is for | Examples |
| --- | --- | --- |
| **Connexions** (`/?panel=conn`) | every external connection | cloud storage (S3, Google Drive), MCP servers (Positron, VS Code, a custom tool) |
| **Données** (`/?panel=data`) | local maintenance | backup, skills, installed tools, repair UH |

There is **no** MCP button in `Données`, and no connection form anywhere else. If a
guide or a model sends you to `Données` for a connection, it is wrong. `+ Connecter
un MCP` in Connexions takes a `.json`/YAML file or a command/URL, tests it for five
seconds, and only then enables "Enregistrer".

## Pip conflicts (a shared interpreter)

Another project on the same interpreter can pin a dependency differently, and pip then
prints a wall of "conflicting dependencies". UH and UHB declare neither `pydantic` nor any
version of it: what you are reading is other packages' opinions about each other. Measure
before believing the message — the diagnostic ships **in the repository** (not in the wheel,
which only installs `uh*`) and must run on the interpreter that launches `uh`:

```powershell
git clone https://github.com/Antoine-Thomas/UH-Universal-Harness
cd UH-Universal-Harness
python scripts/diagnose_pydantic.py          # who constrains what, and the range that fits
python scripts/diagnose_pydantic.py --clean  # plus two throwaway venvs, to prove a clean install
```

Measured on 2026-11-17 on a Python 3.10 shared with an AI stack: eleven **active**
constraints on `pydantic`, of which three explain the conflict. `mcp` and `mcp-types`
require `>=2.12.0`; `google-genai` requires `>=2.12.5,<3`. Two names often blamed are not on
that list: `mixpanel` declares no dependency on `pydantic` at all, and `gradio` constrains
it only through its **`mcp` extra** (`<=2.12.5,>=2.11.10`), which a plain
`pip install gradio` does not install. `litellm` accepts `>=2.11.0,<3`.

For a shared AI environment, the range that satisfies everything measured is:

```powershell
pip install "tomlkit<0.15.0" "importlib-metadata<9.0" "openai<3.0.0" "pydantic>=2.12.5,<3"
```

`2.12.5` is the **minimum measured for that environment**, not a version UH asks for: a
clean venv resolves `pydantic 2.14.3` by itself, and UH runs from `1.10` to `2.14` because
it never imports it directly. Do not add a `pydantic` bound to UH's metadata to repair
somebody else's environment.

### A conflict Pydantic will not fix

`pip check` on a shared machine also reports pairs unrelated to Pydantic: `google-antigravity
0.1.15` requires `mcp<2.0` while `mcp 2.1.1` is installed. Upgrading `pydantic` leaves that
line untouched — it needs a decision about `mcp`, not about `pydantic`, and the answer may be
that another tool resists the downgrade.

These are ceilings, not pins: pip still upgrades freely below them. A conflict message is a
*warning* — `uh doctor` and one real turn are the proof. If it persists, a clean environment
settles it:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install uh-harness
```

### The UI is older than the code you read about

`pip install uh-harness` gives the **latest published release**, not `main`. The
`Connexions` and `IA locale` tabs landed after 1.6.1, so a machine still on 1.6.1
shows the previous tab strip (`Clés API · Fournisseurs · Modèles …`) even after a
clean reinstall - and no setting in the UI changes that. Two ways out:

```bash
# a) run main, which is what the screenshots and docs describe
python -m pip install --force-reinstall --no-cache-dir \
  "git+https://github.com/Antoine-Thomas/UH-Universal-Harness.git@main"

# b) stay on the release, and read CHANGELOG.md for what is not in it yet
python -m pip install --force-reinstall --no-cache-dir uh-harness
```

The shortcut launches `…\Scripts\uh.exe`, so **the interpreter is the install**:
`where uh` and `python -c "import uh; print(uh.__file__)"` tell you which one your
shortcut really starts. A second Python with an older wheel can hold the port while
the one you upgrade stays idle.

### After entering an API key: what healthy looks like

- `uh doctor` answers without an error;
- no `HTTP 500` and no traceback in the window or in `~/.uh/uh-web.log` in the
  thirty seconds that follow the save;
- the panel shows a **masked hint only** (`sk-…abcd`), never the key - there is no
  "show the key" button by design;
- the value lands in `keys.toml` (LLM providers) or `mcp-secrets.toml` (MCP
  servers), owner-only, and **never** in `config.toml`;
- `Tester la connexion` answers in a few seconds with OK, `Key refused` (401),
  `Quota` (429) or `Timeout` - an unhandled 500 is a bug, not a state;
- if a 500 does happen: open an issue with the `uh doctor` output, the traceback,
  and **only the masked hint**. Never paste the key.

### macOS and Linux

The sequence is the same without step 1, and without the shortcut:

```bash
pkill -f "uh.cli" || true
python -m pip cache purge
python -m pip uninstall uh-harness -y
python -m pip install --force-reinstall --no-cache-dir uh-harness
uh --version
```

There is no `uh install-shortcut` outside Windows: point your own launcher at
`uh web`.

## Uninstall

```bash
python -m pip uninstall uh-harness -y
```

Your data stays in `$UH_HOME` (`~/.uh`: the store, dreams, skills, keys). Delete it
by hand when you want it gone — UH never deletes it for you.
