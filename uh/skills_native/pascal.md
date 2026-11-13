# Pascal (Free Pascal / Delphi): explicit memory, explicit intent

> Units, managed types, and a resource model you can read in the code.

## Instructions

You are writing or reviewing Object Pascal (Free Pascal 3.x or Delphi 11+, no framework assumed).

Structure
- One unit per responsibility, `interface` exposing the minimum, `implementation` hiding the rest.
- Naming: `TSomething` for classes, `ISomething` for interfaces, `ESomething` for exceptions, `Something` for records and variables (no Hungarian, no single letters).
- `{$mode objfpc}{$H+}` in FPC sources; `{$IFDEF}` guards only where the platforms genuinely differ.
- Records for value data, classes for behaviour; `TList<T>` / `TStringList` over manual arrays unless performance is measured.

Memory and resources
- `try ... finally` around every acquired resource, always, in the same function that acquired it.
- Objects: `FreeAndNil` in the `finally`; interfaces are reference-counted on FPC/Delphi — do not mix ownership models in one field.
- `TComponent` on a form is owned by the form; created elsewhere, it is yours to free.
- Strings and dynamic arrays are managed: assigning them copies (refcount + copy-on-write), do not `New`/`Dispose` them.
- `with` hides scope: refuse it in review.

Correctness
- Range checks on (`{$R+}`) in debug builds; an index out of bounds must raise, not corrupt.
- `ShortString`/`AnsiString`/`UnicodeString` conversions: be explicit, and use `UTF8Encode`/`UTF8Decode` at the boundary.
- `Case` over an enum without `else` must still compile with `{$WARN SYMBOL_PLATFORM}` visible; a missing branch is a bug, not a default.
- Exceptions: raise a specific class with a message that names the operation and the value.

Tests and build
- FPCUnit (or DUnitX on Delphi) with a test per behaviour; `heaptrc` / `-gh` in the test build to prove there is no leak.
- A build script (`.lpi`/`dpr` + `fpc @cfg`) that compiles the same way in CI as on the machine.

Review checklist
- [ ] Every allocated object freed in a `finally`
- [ ] No `with`, no global mutable state
- [ ] Range checks in debug; explicit string encodings
- [ ] Specific exception classes with actionable messages
- [ ] Leak check in the test build
