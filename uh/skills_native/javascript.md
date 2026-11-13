# JavaScript that survives review

> Modules, event loop, and the security rules a browser-based app cannot ignore.

## Instructions

You are writing or reviewing plain JavaScript for the browser or Node (ES2022, no framework assumed).

Language
- `const` by default, `let` when reassigning, never `var`.
- Strict mode everywhere (`"use strict"` or ESM). Never rely on sloppy-mode coercion.
- `===` always; `Object.is` for `NaN` and `-0` edge cases.
- Destructure and default: `const { a = 0, b } = options ?? {}`.
- Optional chaining `?.` and `??` over nested `&&` chains; `??` not `||` when `0`/`""` are valid.
- Pure functions for formatting and comparison; side effects isolated in one place per module.
- Never `arguments`; rest parameters instead.

Async and the event loop
- `async` functions return a promise that MUST be awaited or `.catch`-ed; a floating promise is an unhandled rejection.
- `Promise.all` for independent work, `for await` for ordered streams; `Promise.allSettled` when partial failure is acceptable.
- Microtasks (promises) run before timers: never assume `setTimeout(fn, 0)` runs first.
- Sequential awaits in a loop are a performance bug when the calls are independent.

Security (the part reviewers skip)
- `innerHTML` with anything untrusted is XSS: use `textContent`, or an explicit escaping function.
- No `eval`, `new Function`, `document.write`, or `javascript:` URLs.
- Build URLs with `new URL()` and `searchParams`, not string concatenation.
- `fetch` to a same-origin endpoint: send credentials explicitly, check `response.ok` (a 500 does not reject), and parse with a fallback for empty bodies.
- localStorage is not a secret store: no tokens unless the threat model says so.

Structure
- One module, one responsibility; ESM imports with explicit extensions where required.
- Avoid globals; state lives in an object passed in, or in the module's own closure.
- Defensive parsing of anything from the network: `Array.isArray`, `typeof x === "object" && x !== null`.
- Errors carry context: `throw new Error(`save failed: ${status} ${detail}`)`.

Review checklist
- [ ] No `var`, no `==`, no floating promises
- [ ] No `innerHTML` with untrusted data; no eval/new Function
- [ ] `response.ok` checked; timeouts or AbortSignal on every fetch
- [ ] Independent awaits parallelised
- [ ] Network payloads validated before use
- [ ] Errors include what failed and what to do
