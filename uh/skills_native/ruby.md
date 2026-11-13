# Ruby: expressive, small, and frozen

> Idiomatic blocks and objects, with the traps that bite in production.

## Instructions

You are writing or reviewing Ruby 3.x (no Rails assumption; mention it only when relevant).

Idioms
- `# frozen_string_literal: true` at the top of every file: strings are shared, mutation is explicit.
- Blocks for iteration and resource handling (`File.open do |f|`), `each` over `for`.
- Enumerable over manual loops: `map`, `select`, `reduce`, `each_with_object`. `each` returning the receiver when a `map` was meant is a classic bug.
- `attr_reader` over public instance variables; `private` for everything not part of the API.
- `Struct` or `Data.define` for value objects; `Hash` with symbol keys for options.
- Duck typing with `respond_to?` only when you must; prefer a shared interface or a module.

Traps
- `nil` propagates silently: use `&.` deliberately, and `fetch`/`dig` with defaults where absence is expected.
- `==` compares values, `equal?` compares identity; `eql?`+`hash` must agree if the object is a Hash key.
- Mutable default arguments (`def f(list = [])`) share one array across calls.
- Bang methods (`sort!`, `gsub!`) mutate in place; a non-bang version returning a new object is the safer default.
- Global state (`$var`, class variables `@@var`) leaks across requests in a long-lived process.
- `rescue Exception` catches `Interrupt` and `SystemExit`: rescue `StandardError`, and never swallow without logging.

Structure and tests
- One class per file, namespaced modules for grouping; `require_relative` inside the gem, `require` for gems.
- RSpec or Minitest with one expectation per behaviour; `let` for lazy fixtures, `before` for side effects, avoid shared mutable state.
- `bundle exec` in CI; `rubocop` for style, not for arguments.

Review checklist
- [ ] Frozen string literal; no global/class variables
- [ ] `rescue StandardError` with a logged reason
- [ ] No mutation of shared defaults
- [ ] Public API documented by its method signatures
- [ ] Tests cover nil and empty inputs
