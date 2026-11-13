# Rust: make the compiler do the reviewing

> Ownership first, `Result` everywhere, `unwrap` only where a panic is the correct answer.

## Instructions

You are writing or reviewing Rust (2021 edition) for a library or a binary.

Design
- Model states with types, not flags: an enum with data beats `Option` + a comment.
- `Result<T, E>` for anything that can fail; a custom error enum with `thiserror`, or `Box<dyn Error>` only at the top.
- `?` for propagation; never `unwrap()` in a library, never `expect()` without a message that explains the invariant.
- Borrow when you can (`&str`, `&[T]`), own when you must (`String`, `Vec<T>`); accept `impl AsRef<str>` in public APIs.
- Lifetimes: annotate only when the compiler asks; if you need three, the ownership is probably wrong.
- `Clone` is not a design tool: clone at a boundary, not to satisfy the borrow checker in a loop.

Idioms
- Iterators over index loops: `iter().filter(..).map(..).collect()`.
- `if let` / `let else` for the single-pattern case; `match` must be exhaustive without a catch-all `_` when the enum is yours.
- `Option::ok_or_else` to convert a missing value into an error with context.
- `#[must_use]` on functions whose result matters.
- `unsafe` only with a `// SAFETY:` comment stating every invariant the block relies on.

Concurrency
- `Arc<Mutex<T>>` for shared mutation; `RwLock` only when reads dominate, measured.
- Prefer channels (`mpsc`, `crossbeam`) to shared state when the flow is a pipeline.
- `Send`/`Sync` errors are the design telling you something.

Tests
- `#[cfg(test)] mod tests` next to the code; `cargo clippy -- -D warnings` and `cargo fmt --check` in CI.
- Test the error paths: a function returning `Result` without a failing test is half-tested.
- Property tests (`proptest`) for parsers and round-trips.

Review checklist
- [ ] No `unwrap`/`expect` in library code
- [ ] Errors typed and propagated with `?`
- [ ] Borrows where ownership is not needed
- [ ] Every `unsafe` block carries a `// SAFETY:` argument
- [ ] clippy clean with `-D warnings`
- [ ] Error paths tested
