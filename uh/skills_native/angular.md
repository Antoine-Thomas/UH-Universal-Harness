# Angular: standalone, typed, and subscribed exactly once

> Signals or observables, `OnPush`, and an unsubscribe story you can point at.

## Instructions

You are writing or reviewing Angular 17+ (standalone components, no NgRx assumption).

Structure
- Standalone components with explicit `imports`; NgModules only for legacy boundaries.
- One feature folder per domain: component, service, model, route. Smart components (routing, services) vs presentational (inputs/outputs only).
- Services are `providedIn: 'root'` unless the state must be per component; state in a service behind signals or a subject, not in a random component.
- Lazy-load feature routes (`loadComponent` / `loadChildren`); the initial bundle is the user's first impression.

Templates and change detection
- `ChangeDetectionStrategy.OnPush` on every component that is not a root; then input immutability matters — treat inputs as immutable.
- `@for` with `track` (or `*ngFor` with `trackBy`) on every list: without it, the DOM is rebuilt.
- No function calls in a template that touch the network or allocate: they run on every change detection cycle.
- `async` pipe or `toSignal` for observables in templates; a manual `subscribe` in a component needs a teardown.
- Avoid `any` in templates; type the context of structural directives.

RxJS and subscriptions
- Unsubscribe: `takeUntilDestroyed(inject(DestroyRef))`, or the `async` pipe, or `toSignal`. A naked `subscribe` in `ngOnInit` is a leak.
- Avoid nested subscribes: `switchMap` for "cancel the previous", `concatMap` for order, `forkJoin` for parallel independent calls.
- Handle errors in the stream (`catchError`) — an unhandled error kills the subscription silently.

Forms
- Typed forms (`FormGroup<{...}>`) in strict mode; validators return typed errors, and the template shows the message next to the field.
- Reactive forms over template-driven for anything with business rules.

Review checklist
- [ ] Standalone + `OnPush` on non-root components
- [ ] Every list tracked; no expensive calls in templates
- [ ] Every subscription has a teardown path
- [ ] Errors handled in-stream, not swallowed
- [ ] Routes lazy-loaded; forms typed
