# TypeScript: the compiler is the first reviewer

> Strict mode, discriminated unions, and the difference between `unknown` and `any`.

## Instructions

You are writing or reviewing TypeScript (5.x, `strict: true`, no framework assumed).

Configuration you expect
- `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitOverride`.
- `any` is a bug you are about to debug in production: use `unknown` and narrow.
- Type-only imports (`import type`) so the build does not emit runtime cycles.

Modelling
- Discriminated unions over optional fields: `{ kind: "ok"; value: T } | { kind: "error"; error: E }`.
- `readonly` on data that must not be mutated; `ReadonlyArray<T>` in public APIs.
- Branded types for identifiers (`type UserId = string & { readonly __brand: "UserId" }`) when two strings must not mix.
- Prefer `interface` for object shapes that may be extended, `type` for unions and mapped types.
- Generics must constrain: `<T extends object>`, not `<T>` with a cast inside.
- Avoid `enum`: they emit runtime code and do not tree-shake; a union of literals is enough.

Traps
- `as` is an assertion, not a conversion: it silences the compiler, it does not validate.
- Optional chaining hides bugs when it silently short-circuits on a missing field you assumed required.
- `Object.keys` returns `string[]`: use a typed helper or `satisfies` with a const object.
- Index signatures (`[key: string]: T`) make every lookup `T` — with `noUncheckedIndexedAccess` they become `T | undefined`, which is the truth.
- `JSON.parse` returns `any`: parse into `unknown`, then validate with a guard function.

Tests and tooling
- `tsc --noEmit` in CI; the emitted build is a second, weaker check.
- Tests type-check too: no `as any` in a test to make an assertion pass.
- Runtime validation at the boundary (a zod-like schema or hand-written guards), because types vanish at runtime.

Review checklist
- [ ] No `any`, no non-null `!` on values that can be null
- [ ] Unions modelled with a discriminant, not booleans
- [ ] `as` used only where the invariant is proven and commented
- [ ] External data narrowed by a guard before use
- [ ] `tsc --noEmit` clean
