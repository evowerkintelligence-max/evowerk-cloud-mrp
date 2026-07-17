# Visual Redesign: Design Tokens, Hybrid KPI Layout, Motion

Status: Approved for planning
Date: 2026-07-17

## Problem

Sales feedback (2026-07-17): the product "looks dead," like a 2012-era admin
panel, next to modern SaaS competitors. The frontend already has a real
design system (`frontend/src/index.css` + `frontend/tailwind.config.js`,
HSL-token-driven, dark-mode-first, shadcn-style `components/ui/` primitives)
and some pages (Dashboard, `StatCard`) already show gradient blobs, glow, and
blur. The complaint isn't an absence of a design system — it's that the
existing one reads as flat and safe (single cyan/violet accent pair, no
motion beyond a couple of hover states) and that polish is inconsistent
across ~40 routes.

Validated through interactive mockup review (browser-based visual
companion, `.superpowers/brainstorm/277-1784249062/`): plain
gradient/glow/neon treatments were rejected as "ugly" and generic-AI-SaaS.
The direction that landed was a hybrid of Notion/Attio's quiet, paper-calm
density and Framer's oversized-number confidence, on a neutral (not
warm-tinted) near-black dark base, with a wider accent palette than the
current cyan+violet pair.

## Scope

In scope: retune the existing HSL token layer (`index.css`) for both
`.dark` (primary) and `:root` (light) themes; extend the palette from 2
accent hues to a 5-hue system with defined semantic roles; rework
`StatCard` (and any other primitive rendering a headline metric) to support
an oversized-primary-metric layout; add the motion primitives (number
count-up, chart entrance animation, page/tab transition, live-pulse badge)
as reusable utilities/hooks, not one-off inline animations; apply globally
by touching only the token layer and shared `components/ui/` + top-level
shared components (`StatCard`, `AppShell`, `PageHeader`, `PageTabs`,
`Card`, `Button`, `Badge`, `Table`) — not by hand-editing all ~40 page
files, since every page already composes these.

Out of scope: introducing a component library dependency (still
hand-rolled, per existing convention); a user-facing theme picker beyond
the existing light/dark toggle path; redesigning page-specific layouts
that aren't just composed from the shared primitives (e.g. `SqlEditor`,
`WorkflowBuilder`'s canvas) — those get the new tokens "for free" via
`hsl(var(--x))` references but no bespoke layout work in this pass;
animating every table row individually (would hurt perf on the larger
data-grid pages like `Boms`, `Items`).

## Color system

### Semantic roles (unchanged in meaning, retuned in value)

| Token | Role | Current (dark) | New (dark) |
|---|---|---|---|
| `--primary` | Brand accent — buttons, links, primary highlights, focus ring | cyan `189 94% 55%` | sage/teal green, ~`166 45% 52%` |
| `--brand` | Wordmark / logo-adjacent accent (currently a separate blue) | blue `201 88% 56%` | same hue family as `--primary` so logo and interactive brand color read as one system |
| `--warning` | Warning status (breach thresholds, slow lines, low stock) | amber `38 95% 58%` | amber/gold, retuned less neon, ~`42 88% 60%` |
| `--destructive` | Critical status (scrap alarms, failed jobs, errors) | red `0 72% 55%` | crimson, hue shifted warmer, ~`352 70% 58%` |
| `--accent` | Info / secondary emphasis (unchanged role, was already violet) | violet `258 90% 70%` | violet, kept, minor saturation tune ~`258 75% 68%` |
| `--terracotta` (new) | Neutral 5th hue for chart series / category color-coding, not tied to a status meaning | — | new token, ~`22 55% 55%` |
| `--success` | Existing "good/running" green — kept distinct from `--primary` since primary is now also green-family; verify contrast/distinguishability between the two in implementation, adjust `--success` hue toward a more saturated pure green (~`152`) if they read too similar | `152 62% 45%` | keep hue, may need saturation/lightness nudge away from `--primary` |

Light theme (`:root`) gets the equivalent retuned values at light-appropriate
lightness/saturation (paper base `~40 20% 97%` off-white rather than pure
white, ink foreground rather than pure black — matching the "Notion quiet"
paper direction validated in mockups).

`.brand-gradient` / `.brand-gradient-animate` (currently hardcoded
cyan→blue→violet stops in `index.css`, not driven by the CSS vars) get
their hardcoded stops updated to the new `--primary`/`--brand` hue family so
the animated gradient doesn't clash with the retuned brand color.

### Why keep semantic role names stable

`--primary`, `--warning`, etc. are referenced throughout `components/ui/`
and page-level Tailwind classes (`text-primary`, `bg-warning/15`, ...).
Retuning the HSL value behind each token is a one-file change; renaming
tokens would require touching every call site for no visual benefit.

## Layout: hybrid KPI card

`StatCard` (`frontend/src/components/StatCard.tsx`) currently renders every
metric at equal weight: a small uppercase label, an optional icon, and a
`text-3xl` value. The new pattern introduces a size variant:

- **`size="hero"`** (new, default for the single most important metric on a
  page, e.g. OEE on the plant overview): value renders large
  (`text-4xl`/`text-5xl` range, tabular-nums, tight tracking), with a
  supporting label below it instead of above, and a delta
  (`+2.1%`/`-0.1%`) inline next to the value in the tone color rather than
  buried in a `hint` line.
- **`size="default"`** (existing behavior, unchanged): current compact
  tile, used for secondary metrics accompanying a hero card.

No new component — `StatCard` gains a `size` prop with the existing
`tone`/`TONE` map reused unchanged (tone drives icon/glow/blob color as
today; the 5-hue palette extension means `TONE` gains `info` and
`terracotta` entries alongside the existing `primary`/`accent`/
`success`/`warning`).

Pages that currently render a row of N equal `StatCard`s pick one metric to
promote to `size="hero"` per section (page-level decision, not automatic) —
this pass updates the shared component; picking which metric is "hero" per
page is a follow-up pass over the ~40 pages, not blocked on this spec.

## Motion

Four additions, each a small reusable primitive rather than inline
one-offs:

1. **Micro-interactions** — extend the existing hover-lift/glow pattern
   (`StatCard`'s `hover:-translate-y-0.5` + tone glow shadow) to `Card` and
   `Button` primitives generally, so it's a base-component behavior instead
   of something only `StatCard` happens to do.
2. **Number & chart animation** — a small `useCountUp(value, duration)`
   hook (interpolates a numeric value on mount/change, respecting
   `prefers-reduced-motion` same as the existing keyframe rule in
   `index.css`) used by `StatCard`'s value; ECharts entrance animation
   (`animationDuration`/`animationEasing` options ECharts already supports)
   enabled on chart initialization instead of static render.
3. **Page/tab transitions** — a thin wrapper around the existing
   `fade-in`/`scale-in` Tailwind keyframes (already defined in
   `tailwind.config.js`, currently likely under-used) applied at the route
   level in `App.tsx` and in `PageTabs` tab-switch, so navigating and
   switching tabs cross-fades instead of hard-cutting.
4. **Live-data pulse** — a `.live-pulse` utility (subtle opacity/glow
   keyframe, reusing the `--primary` tone) applied to "Live" badges and
   real-time values (Realtime page, live OEE/throughput figures), same
   pattern as the `shimmer` keyframe already defined but for a slow
   breathing pulse rather than a loading shimmer.

All motion respects the existing `@media (prefers-reduced-motion: reduce)`
block in `index.css`, which already zeroes animation/transition duration
globally — new keyframes/hooks must not bypass it (the count-up hook needs
an explicit check since JS-driven interpolation isn't covered by the CSS
media query).

## Rollout

Single pass, not phased by page:

1. Retune `index.css` tokens (both themes) + `brand-gradient` stops.
2. Extend Tailwind `colors` config with a new `terracotta` entry. `accent`
   keeps its existing name (per the "keep names stable" rationale above) —
   no `info` rename.
3. Add `size` variant to `StatCard`, extend `TONE` map.
4. Add the four motion primitives (count-up hook, chart animation config,
   route/tab transition wrapper, live-pulse utility class).
5. Apply hover-lift/glow to base `Card`/`Button`.

Because steps 1-5 only touch the token layer and shared components, all
~40 routes inherit the new look without individual page edits. Promoting a
specific metric to `size="hero"` per page is explicitly a follow-up, not
part of this spec.

## Testing

Visual/manual verification only (no meaningful unit-testable logic beyond
the `useCountUp` hook, which gets a small `test_*`/`*.test.ts`-style check
for: interpolates from 0 to target, snaps immediately under
`prefers-reduced-motion`). Primary verification is running the dev server
and reviewing the Dashboard, an analytics page (e.g. `Oee`), and a
data-table page (e.g. `Boms`) in both themes.
