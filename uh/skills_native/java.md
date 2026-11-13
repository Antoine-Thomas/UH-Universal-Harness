# Java: explicit, immutable, and boring on purpose

> Records, streams where they help, and exceptions that mean something.

## Instructions

You are writing or reviewing Java 17+ (records, sealed types, modern switch).

Design
- Immutable by default: `final` fields, `record` for data carriers, no setters.
- `Optional` is a return type, never a field and never a parameter. `orElseThrow` with a message beats `get()`.
- Interfaces for capability, abstract classes only for shared implementation.
- Sealed interfaces + pattern matching `switch` for closed hierarchies.
- Constructor validation: reject invalid objects at construction, not at use.

Idioms
- Streams for transformations, loops for control flow that branches and mutates. A stream with three nested lambdas is a loop in disguise.
- `equals`/`hashCode` together, or a `record` that generates them; a class in a `HashSet` without both is a bug.
- `List.copyOf` / `Map.of` for constants; never return an internal mutable collection.
- `StringBuilder` in loops; `String.format` for messages; `text blocks` for SQL or JSON fixtures.
- Checked exceptions for recoverable domain failures, unchecked for programming errors; never `catch (Exception e) { e.printStackTrace(); }`.

Concurrency
- Immutable objects shared freely; mutable state behind `synchronized` or a `java.util.concurrent` structure.
- `ExecutorService` with an explicit pool size and a shutdown path; never `new Thread()` in application code.
- `CompletableFuture` for composition, with `exceptionally`/`handle` always provided.

Tests
- JUnit 5, one behaviour per test, named `methodUnderTest_condition_expected`.
- AssertJ or plain assertions; no `System.out` verification.
- Test the failure path and the boundary (empty, one, many, null where allowed).

Review checklist
- [ ] Records/final fields; no public mutable state
- [ ] `Optional` only as a return type
- [ ] `equals`/`hashCode` consistent
- [ ] No swallowed exceptions, no `printStackTrace`
- [ ] Executors shut down; no raw threads
- [ ] Boundary and failure cases tested
