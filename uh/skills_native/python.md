# Python, the way a maintainer writes it

> Typed, testable, packaged: the review checklist a senior Python dev applies before saying LGTM.

## Instructions

You are reviewing or writing production Python (3.10+, no framework assumed).

Style and structure
- Type every public function signature; `from __future__ import annotations` first.
- `pathlib.Path` over `os.path`; f-strings over `%` and `.format`.
- `dataclasses` for plain records; `NamedTuple` for tuples with names; a class only when behaviour belongs with the data.
- Explicit `__all__` in library modules; no star imports; imports at the top, local imports only to break a cycle or defer a heavy dependency.
- Keep functions under ~40 lines and with one reason to change. Early returns over nested `if`.

Correctness traps
- Mutable default arguments (`def f(x=[])`): use `None` then build inside.
- `except Exception: pass` hides everything; catch the narrow exception, and always `raise ... from exc` when re-raising.
- Never compare to `None` with `==`; `is` for singletons, `==` for values.
- Floats for money are a bug: `Decimal` or integer cents.
- `datetime.now()` without tz is a bug in anything that leaves the process: `datetime.now(tz=timezone.utc)`.
- Dict iteration order is insertion order (3.7+): rely on it, but sort when the output is compared.
- `is` on strings/ints works by interning accident, never by design.

Concurrency
- `asyncio` is not parallelism: one blocking call (`requests`, `time.sleep`, heavy CPU) freezes the loop. Use `httpx.AsyncClient`, `asyncio.to_thread`, or a process pool.
- Await inside `try/finally` for cleanup; cancel a task with `task.cancel()` and handle `CancelledError` by re-raising.
- Shared mutable state across threads needs a `Lock`; the GIL is not a correctness guarantee.

Packaging and tests
- `pyproject.toml` with `[project]`; pin the minimum versions you actually test.
- `pytest` with fixtures, `tmp_path` for the filesystem, `monkeypatch` for the environment; no test touches the real home directory.
- Assert on values, not on `print` output received through capsys, unless stdout is the contract.

Security
- `subprocess.run([...], shell=False)`; never build a shell string from user input.
- `pickle`, `eval`, `exec`, `yaml.load` (without SafeLoader): refuse them in review.
- A path from a model or a user goes through `Path.resolve()` and a containment check before it is opened.

Review checklist
- [ ] Every public function typed and documented with its failure modes
- [ ] No bare `except`, no swallowed exception
- [ ] No mutable default, no naive datetime, no float money
- [ ] Timeouts on every network call; no blocking call inside `async def`
- [ ] Subprocess without a shell; no pickle/eval on untrusted input
- [ ] A test that fails before the fix exists
