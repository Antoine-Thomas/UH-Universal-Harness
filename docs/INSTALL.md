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
