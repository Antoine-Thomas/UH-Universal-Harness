# React: state you can explain, effects you can remove

> Hooks rules, derived state, and the a11y contract a component library never gives you.

## Instructions

You are writing or reviewing React 18+ (function components, no state library assumed).

Components
- One component, one reason to re-render; extract when JSX passes ~40 lines.
- Props are read-only: never mutate them, never mutate state in place (spread or a reducer).
- Derive instead of duplicating: if it can be computed from props/state, compute it during render (`const full = `${first} ${last}``), not in an effect.
- `key` must be stable and unique: an index is a bug as soon as the list reorders.
- Avoid `useEffect` for data flow. Effects synchronise with something outside React; a `setState` inside an effect that only mirrors a prop should be deleted.
- Memoise only when measured: `useMemo`/`useCallback` with a wrong dependency array is worse than no memo.

Hooks
- Dependency arrays must contain every value read inside; a missing one is a stale closure, an extra one a loop.
- Custom hooks for reusable behaviour, prefixed `use`; they may not be called conditionally.
- Async in an effect needs a cleanup guard (`let cancelled = false; return () => { cancelled = true; }`) or an `AbortController`.
- State updates that depend on the previous value use the functional form: `setCount(c => c + 1)`.

Accessibility and UX
- A `div` with `onClick` is not a button: use `<button>`, or add `role`, `tabIndex`, and Enter/Space handling.
- Focus management on modal open/close; `aria-live` for async status; labels for every input.
- Loading and error states are part of the component contract, not an afterthought.
- Never block the main thread with a synchronous heavy computation: `useDeferredValue` or a worker.

Tests
- React Testing Library, queries by role/label (not by class), user-event for interaction.
- Test the contract: what the user sees and can do. A snapshot of a large tree is a change detector, not a test.

Review checklist
- [ ] Stable keys; no index as key on reorderable lists
- [ ] No derived state duplicated in `useState`
- [ ] Dependency arrays complete; effects have cleanup
- [ ] Interactive elements are real buttons/links
- [ ] Loading and error states rendered
- [ ] Tests query by role, not by implementation detail
