# Third-party notices

UH is MIT-licensed. It ships **no vendored code** and **no third-party
JavaScript**: the web interface is three static files written for this project
(`uh/web/static/index.html`, `app.js`, `style.css`), served under a CSP that
forbids inline script.

## Runtime dependency

| Package | Licence | Role |
| --- | --- | --- |
| [httpx](https://www.python-httpx.org/) | BSD-3-Clause | the only mandatory runtime dependency: Ollama, OmniRouter, the free-provider router and the web tests |

## Optional extras

| Package | Extra | Licence | Role |
| --- | --- | --- | --- |
| [rich](https://rich.readthedocs.io/) | `[ui]` | MIT | coloured console rendering; plain text is used when absent |
| [fastapi](https://fastapi.tiangolo.com/) | `[web]` | MIT | HTTP layer of `uh web` |
| [uvicorn](https://www.uvicorn.org/) | `[web]` | BSD-3-Clause | ASGI server for `uh web` |
| [tomli](https://github.com/hukkin/tomli) | conditionally, Python < 3.11 | MIT | TOML parsing backport (`tomllib` is stdlib from 3.11) |

## Development dependencies (not shipped)

`pytest` (MIT), `build` (MIT), `ruff` (MIT), `mypy` (MIT), `pre-commit` (MIT).
They are declared in the `dev` extra and never installed by a user of the
package.

## External marks and images

- The README badges are **remote images** served by shields.io and by GitHub; no
  third-party file is copied into this repository.
- The screenshot in `docs/assets/webui.png` was produced from this project's own
  interface; no third-party UI asset appears in it.

## Licences of the ideas, not of the code

UH was inspired by the public feature lists of Harness Maxxing, OpenClaw, Hermes
Agent and UHB. No source file of those projects is copied, translated or
vendored here: the free-provider router and the web interface are original
implementations (see `docs/ROUTER.md` and `docs/WEB_DESIGN.md`). Harness Maxxing
is under a Fair Source Licence, which is precisely why its code was **not**
reused.
