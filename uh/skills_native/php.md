# PHP: strict, prepared, escaped

> Modern PHP 8, `strict_types`, and the three rules that stop most incidents.

## Instructions

You are writing or reviewing PHP 8.1+ (Composer autoload, no framework assumed).

Language
- `declare(strict_types=1);` on every file. Without it, `"1" == 1` is true and types are a suggestion.
- Typed properties and parameters, `readonly` for value objects, enums instead of constants for closed sets.
- `match` instead of `switch` when every branch returns a value; it is strict and exhaustive by default.
- Null handling with `??`, `?->`, and explicit `null` returns documented in the signature (`?Type`).
- Named arguments for functions with more than three parameters.

Security (non-negotiable)
- SQL: prepared statements with bound parameters, always. String interpolation into a query is an incident waiting for a date.
- Output: `htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8')` in HTML context; `json_encode` with `JSON_THROW_ON_ERROR` in JSON context.
- Passwords: `password_hash` / `password_verify`, never `md5`/`sha1`, never a custom scheme.
- File paths from input: `realpath()` + a prefix check against the allowed root.
- Never suppress with `@`; handle the error or let it surface.
- `unserialize` on untrusted data is remote code execution: use `json_decode`.

Structure
- PSR-12 formatting, PSR-4 autoloading, one class per file.
- Dependency injection through the constructor; no service locator, no `global`.
- Exceptions for failure; `error_log` for diagnostics; no `var_dump` left in a commit.
- Keep controllers thin: validation, call, render. The logic lives in a service.

Tests
- PHPUnit with data providers for the boundaries; test the escaping helpers with hostile strings (`<script>`, quotes, accents).
- A test that hits the database uses a transaction that rolls back.

Review checklist
- [ ] `strict_types` declared
- [ ] Every query prepared
- [ ] Every output escaped for its context
- [ ] No `@`, no `unserialize`, no `eval`
- [ ] Passwords through `password_hash`
- [ ] Hostile-input tests for escaping
