# UI references for the UH web refactor

Collected 2026-11-13, UH 1.1.0. **Documentation only** — nothing in `uh/web/`
was touched.

Two references, kept because they solve the same problem UH has: a dense chat
desktop with a lot of state (sessions, tools, modes, cost) and no build step
that can be trusted to stay simple.

---

## 1. Kiro v4.0 — local mock

`C:\Users\searc\Desktop\Kiro v4.0\Kiro v4.0` — 3.4 MB, static, opens with a
double click.

### Stack [measured]

| Fact | Value |
| --- | --- |
| Framework | none: HTML + CSS + vanilla JS, zero build |
| `index.html` | 1,490 lines, 430 `id=""`, CSP in a `<meta http-equiv>` |
| `css/style.css` | 13,369 lines |
| `css/design-verre.css` | 1,955 lines (alternate "glass" skin) |
| `js/app.js` | 18,182 lines |
| `js/canvas.js` / `api.js` | 9,280 / 3,545 lines |
| Third-party | pdf.js, marked, DOMPurify, JSZip from jsDelivr, pinned with SRI |

CSP: `default-src 'self' file: blob: data:`, an explicit provider `connect-src`
(OpenAI, Anthropic, DeepSeek, Mistral, Perplexity, xAI, Z.ai, Gemini,
OpenRouter, localhost), `object-src 'none'`, `base-uri 'self'`.

### Layout [measured]

Three zones, all collapsible, no framework:

- **sidebar** — `--sidebar-width: 240px` (resizable in `app.js`,
  `initSidebarResize`), collapses to a 16 px peek via negative margin
  (`--sidebar-peek`), not `display: none`;
- **conversation** — centred column, `min-width: min(var(--chat-min), 100vw - 16px)`;
- **right panel** — `width: 280px`, tabs (`data-tab="apimodeles|models|budget|
  appearance|stockage|faq|statistiques|share"`), collapses to
  `width: 0; opacity: 0; border: none`.

Collapse is animated with `grid-template-columns: auto 0fr` →
`0.28s cubic-bezier(.4,0,.2,1)` and a matching transition on the panel width.

### Design tokens [measured]

`css/style.css:71` (`:root`, light) and `css/style.css:108` (`body.dark`),
semantic variables, two full themes:

    light                                  dark (body.dark)
    --bg #ffffff                           #1a1a1a
    --bg-sidebar #fafafa                   #212121
    --bg-msg-user #f0f0f0                  #2a2a2a
    --bg-msg-assistant #f9f9f9             #323232
    --text #1a1a1a                         #e0e0e0
    --text-secondary #888                  #999
    --border #eaeaea                       #2e2e2e
    --accent #F06D13  (same in both)       --danger #dc2626
    --link #2563eb                         #60a5fa

A second `:root` block overrides the greys warmer (`--bg-sidebar #f8f9fa`,
`--text-secondary #6f7782`, …), and the CSS leans on
`color-mix(in srgb, var(--text) 4%, transparent)` for tints instead of new
constants.

Type: system stack (`-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto`),
mono `ui-monospace, Menlo, Consolas`, Georgia for prose accents. Messages:
`0.95rem / 1.6`, `border-radius: 16px`, `padding: 14px 18px`,
`max-width: 75%`.

`design-verre.css` is a second skin (`body.design-verre`): translucency control,
dark variants (`#18181c`, `#121722`, `#121a15`), same variables.

### What is worth taking

- The **shell**: 240 / fluid / 280 with both side panels collapsible — exactly
  the anatomy UH needs once sessions, tools and modes are on screen.
- **Variables first**: a page never names a colour, it names a role.
- **No build**: it proves a chat desktop does not need a bundler.
- Collapse animations that keep the panel in the accessibility tree.

### What not to take

- 13 k lines in one stylesheet and 18 k in one script: a single file that size
  cannot be reviewed. UH's web assets should stay split per screen.
- The per-provider `connect-src`: UH's browser never talks to a provider (the
  server does), so its CSP can stay `'self'`.

---

## 2. Harness Maxxing — public demo

`https://harness-maxxing.com/demo/sessions?path=projects%2Fops%2Ftopics%2Fserver&session=b10c0de0-0000-4000-8000-000000000a1a&open=…`
(SPA: the HTML is a shell, the structure is rendered by JS.)

Sources actually read: the page text, `/demo/assets/index-D3ADgiPG.css`
(137 KB) and `/demo/overlay.css` (10 KB).

### Stack [measured, from class and token names]

Tailwind + shadcn-style tokens (`--background`, `--foreground`, `--card`,
`--popover`, `--muted`, `--sidebar`, `--radius: .625rem`), colours in **oklch**,
theme switched by `class="dark"` on `<html>`, `Inter Variable` +
`JetBrains Mono`.

### Palette [measured]

| Token | Light | Dark |
| --- | --- | --- |
| background | `oklch(100% 0 0)` | `oklch(14.1% .005 285.823)` |
| card | `oklch(100% 0 0)` | `oklch(17.8% .006 285.885)` |
| muted | `oklch(96.7% .001 286.375)` | `oklch(23.5% .006 286.033)` |
| muted-fg | `oklch(55.2% .016 285.938)` | `oklch(70.5% .015 286.067)` |
| border | `oklch(92% .004 286.32)` | `oklch(100% 0 0 / .09)` |
| sidebar | `oklch(98.5% 0 0)` | `oklch(16% .005 285.823)` |
| destructive | `oklch(57.7% .245 27.325)` | `oklch(63.7% .208 25.331)` |
| success / warning | `oklch(60% .15 155)` / `oklch(72% .16 70)` | `oklch(72% .15 155)` / `oklch(80% .15 80)` |

Chart series: `#2a78d6 #eb6834 #1baf7a #eda100 #e87ba4`. Overlays:
`#fafafa` (light) / `#18181b`, `#27272a`, `#1f2026` (dark), radii 8/10/12/14 px.

### Layout and density [measured]

- Sessions page: a **three-column grid** is the dominant pattern
  (`repeat(3, minmax(0,1fr))`, with two- and four-column variants at other
  widths), each cell a session card.
- Card header: project breadcrumb + cost + token total (`🏢 acme / blog $0.11 41k`).
- **Every assistant turn ends with one metadata line**:
  `Opus 5.5 · 14 s · $0.11 · 32k → 640 · Cache 93.8%` — model, latency, cost,
  tokens in → out, cache hit rate.
- **Tool calls** render as their own block, tool name first, arguments in mono:
  `Glob 'public/gallery/*.jpg'`, `Bash magick mogrify -resize "1600x>" …`,
  `Bash du -sh …`; the outcome is then stated in prose. Failures are not in the
  extracted page, so their styling is [unverified].
- Empty states and destructive actions use a splash/dialog layer
  (`.mx-splash`, overlay radii above).

### What is worth taking

- The **metadata line**: UH already reports model and tokens; adding latency and
  cost (when the provider prices are known) costs one line and answers "what did
  this turn cost me?" without opening a panel.
- **Tool cards** with the tool name as a badge and the arguments in mono — UH
  already emits `refused`, `error` and output per tool, so a card can carry a
  state colour (ok / refused / error) that the model cannot talk its way out of.
- **oklch tokens**: same variables as Kiro, but wide-gamut and theme-switchable
  with one class.

### What not to take

- The three-column *sessions* grid is for browsing, not for a conversation; UH's
  chat stays one column.
- Tailwind and a JS bundle: UH's web is a single static file set served by
  `uh web`, and that constraint is worth more than the build tooling.

---

## 3. Recommendations for `uh/web`

Constraints: vanilla JS, zero build, CSP `'self'`, one static directory.

1. **Token layer first** — define ~12 roles (`--bg`, `--surface`, `--border`,
   `--text`, `--muted`, `--accent`, `--danger`, `--success`, `--warning`,
   `--radius`, `--font`, `--mono`) in `:root` and in `html[data-theme="dark"]`.
   Take Harness Maxxing's *names*, Kiro's *values* (plain hex first; oklch as a
   progressive enhancement, since UH has to render on whatever the user runs).
2. **Shell**: sidebar 240 (collapsible, resizable later), conversation, right
   panel 280 with tabs (`Session`, `Tools`, `UHB`). Panels collapse without
   leaving the accessibility tree.
3. **Message anatomy**: user bubble with `--bg-msg-user`; assistant as a flat
   block; footer line `model · elapsed · tokens in/out · cost?` — the data is
   already in `ChatSession.usage_snapshot()`.
4. **Tool call card**: name badge, mono arguments, status (ok / refused /
   error), duration. Refusals use `--danger` and repeat the sandbox message,
   which is already the string the model receives.
5. **Empty state**: three actionable chips — `uh web`, `uh providers add`,
   `/workspace <path>` — instead of a paragraph.
6. **Accessibility**: `prefers-reduced-motion` disables the collapse animation;
   the sidebar handle gets a visible focus ring.

## 4. Unknowns

- Harness Maxxing's component markup (HTML) is client-rendered; only its CSS was
  read. Exact class structure per component is [unverified].
- Kiro's `canvas.js` (9,280 lines, canvas/document view) was not analysed: out
  of scope for the chat UI, revisit if UH gets a document pane.
