## What this changes

<!-- One paragraph. Link the issue it closes, if any. -->

## Checklist

- [ ] `pytest -q` is green locally (and with `PYTHONUTF8=0` if I touched anything
      that reads a child process's output)
- [ ] `ruff check uh tests` and `mypy` are green
- [ ] `pre-commit run --all-files` is green
- [ ] No new runtime dependency (or it is justified in the description and in
      `THIRD_PARTY_NOTICES.md`)
- [ ] No secret anywhere: no key, no token, no real `keys.toml`, no credential in
      tests or examples
- [ ] The behaviour is tested, or the test that would have caught the bug is
      added for a fix
- [ ] Documentation updated when the public surface changed (`README.md`,
      `docs/`, `CHANGELOG.md`)

## Notes for the reviewer

<!-- Trade-offs, decisions taken, what you deliberately did not do. -->
