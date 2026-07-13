# Evowerk Cloud MRP — Design System

## Product context

Evowerk Cloud MRP is a multi-tenant manufacturing resource planning web app: BOMs, procurement,
lot-tracked inventory, warehouse, planning/MRP, plant analytics (OEE, downtime, throughput, scrap,
shifts, SPC), datasets/BI/SQL, workflows, AutoML, and alerts. Users are plant managers, planners,
and ops/quality staff who live in this app for hours a day — density and legibility matter as much
as beauty. It currently ships a dark-first, fairly flat "developer tool" aesthetic (Inter, teal
primary, subtle grid backdrop, thin borders). The goal of this redesign: make it feel **lively,
modern, and aesthetically beautiful** — more depth, more color, more motion — without sacrificing
the data-dense legibility a plant-ops dashboard needs.

Key pages & architecture: React 18 + TypeScript + Vite SPA, react-router-dom routes, all wrapped in
one `AppShell` (fixed sidebar nav + topbar) except auth pages (own `AuthLayout` split-panel). Custom
shadcn-style component kit (cva + tailwind-merge), Tailwind CSS, lucide-react icons, echarts for
charts, @tanstack/react-query for data.

## Visual direction (open palette — approved)

Move away from the flat single-teal-accent look toward a **vibrant, energetic SaaS-dashboard**
aesthetic: layered depth (soft elevation + glow, not just 1px borders), a richer multi-color accent
system used purposefully (not decoratively) to differentiate categories of information, and subtle
motion (hover lifts, fade/scale-ins, animated gradient glows) that makes the UI feel alive without
being distracting in a data-heavy context.

### Color system

Keep the token *architecture* (HSL-channel CSS variables consumed via `hsl(var(--x))`, `:root` +
`.dark`, dark-first with `<html class="dark">`) but evolve the values toward a richer, livelier
palette:

- **Primary**: vivid electric cyan-blue (keep in the cyan/blue family the brand mark already implies,
  but push saturation/brightness up for more energy) — e.g. `hsl(198 100% 56%)` dark-mode primary.
- **Accent**: vivid violet/purple as the secondary brand color, used for gradients and highlight
  moments (already present as `--accent`, lean into it more) — e.g. `hsl(262 90% 68%)`.
- Introduce a **brand gradient** (cyan → blue → violet, already sketched in `.brand-gradient` in
  `index.css`) as a first-class visual device: hero stat highlights, active nav indicator, primary
  CTA glows, chart accent lines.
- Keep semantic colors (`--success` emerald, `--warning` amber, `--destructive` rose, plus the
  existing sky/indigo/teal badge tones) — these map to real inventory/status meaning
  (stockout/low/excess/on-hold/expiring) and must stay legible and consistent.
- Background: keep near-black dark surface (`224 42% 4%` family) but add more visible depth via
  layered translucent gradients/glows (radial cyan/violet glows at low opacity), not flat color.
- Cards/surfaces: allow soft glassmorphism (translucent background + backdrop-blur + subtle inner
  highlight border) for elevated/hero surfaces (e.g. Morning Brief card, stat cards), while list/table
  surfaces stay more solid for data legibility.

### Typography

- Keep **Inter** as the only typeface (already loaded via `@fontsource/inter`, weights 400/500/600/700).
  Do not introduce a second display/serif font — legibility for dense tabular data matters more than
  novelty.
- Numbers stay tabular (`.tabular` / `font-variant-numeric: tabular-nums`).
- Headings can go slightly bolder/larger for more visual energy (e.g. page titles at 700 weight
  instead of 600), but body/table text stays at current sizes for density.

### Spacing, radius, shadow

- Keep the existing spacing rhythm (Tailwind default scale) and `--radius: 0.625rem` base (rounded-lg
  cards). Slightly larger radii are OK for hero/feature surfaces (rounded-xl/2xl) if it reads as more
  modern, but data tables/rows should stay closer to the current tighter radius.
- Elevate the shadow system: current `shadow-card` (flat 1px) is fine for tables; hero cards and
  interactive elements should get a livelier elevation — soft colored glow shadows (already have
  `shadow-glow`, `shadow-glow-sm`, `shadow-overlay` in `tailwind.config.js` — use them more), plus
  hover states that lift (`hover:-translate-y-0.5`, already used on Dashboard explore cards) and grow
  the glow.

### Motion

- Keep existing `fade-in`, `scale-in`, `shimmer` keyframes/animations as the motion vocabulary.
- Add liveliness via: staggered fade-in on stat cards/list rows, hover lift + glow intensification on
  cards/buttons, an animated (slowly shifting) gradient on hero surfaces or the active-nav indicator,
  smooth color transitions already present (`transition-colors duration-150`) kept or slightly
  lengthened for hero moments only.
- Respect `prefers-reduced-motion` (already handled globally in `index.css`).

### Components / patterns to reuse as-is (do not reinvent)

Button, Card, Badge, Alert, Table, Dialog, Skeleton, Input/Select/Label/Checkbox primitives, and
`PageHeader` / `PageTabs` / `StatCard` / `EmptyState` patterns — same structure, same props, restyled
via the token/shadow/motion changes above. `AppShell` sidebar nav structure (grouped sections,
collapsible, active-item indicator) stays; livelier active-state indicator (gradient bar/glow instead
of flat 2px bar) is welcome.

### Hard constraints

- Font family: Inter only.
- Keep dark-first (`class="dark"` on `<html>`), light theme (`:root`) values should evolve in the same
  direction but are secondary priority.
- Keep semantic status colors recognizable (success=green family, warning=amber, danger=rose) — do
  not repurpose these hues for decoration.
- Keep data tables dense and legible — no glassmorphism/heavy blur on table rows, only on hero/card
  surfaces.
- Brand mark (`BrandMark.tsx`, the ring + waveform SVG) must be reused as-is (monochrome
  `currentColor`), not replaced with a new logo.
