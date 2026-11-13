# HTML and CSS: semantic first, styled second

> Accessible markup, a layout that survives 320 px, and no inline script or style.

## Instructions

You are writing or reviewing HTML5 and modern CSS (no build step, no framework).

HTML
- One `h1` per page; headings in order, never for size.
- Landmarks: `header`, `nav`, `main`, `aside`, `footer`; a `main` per document.
- A control has a label: `<label for>`, or `aria-label` when the design forbids a visible one.
- Buttons do things, links navigate: `<button type="button">` for actions, `<a href>` for navigation. A clickable `div` is a bug.
- Images: `alt` describing the content, or `alt=""` when decorative. Icons need `aria-hidden="true"` unless they carry meaning.
- Tables for tabular data only, with `<th scope>`.

CSS
- Custom properties for the design tokens (colour, spacing, radius); no magic hexes scattered in rules.
- Layout with `grid` and `flex`; absolute positioning only for overlays.
- Logical properties (`margin-inline`, `padding-block`) so RTL does not need a second stylesheet.
- Responsive: start from the smallest screen; `@media (max-width: …)` to simplify, never to hide content.
- `clamp()` for fluid type instead of a dozen breakpoints.
- `:focus-visible` styled, never `outline: none` without a replacement.
- Respect `@media (prefers-reduced-motion: reduce)`: disable animation, not layout.

Security and hygiene
- No inline `style="…"` and no inline `<script>` when the CSP is `style-src 'self'` / `script-src 'self'` — they are dropped silently.
- No `!important` to win a fight; fix the specificity instead.
- Every interactive element has a visible hover and focus state.
- Touch targets ≥ 44 px on touch layouts.

Review checklist
- [ ] Semantic elements, one `h1`, ordered headings
- [ ] Every input labelled, every icon button named
- [ ] Focus visible; keyboard path covers the whole flow
- [ ] Works at 320 px wide without horizontal scroll
- [ ] Reduced-motion respected; contrast ≥ 4.5:1 for text
- [ ] No inline style/script if the CSP forbids it
