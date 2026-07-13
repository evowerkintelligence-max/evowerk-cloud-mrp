# Extractable Components

Catalog of layout and basic components suitable for extraction as reusable SuperDesign
DraftComponents. Full source lives in `components.md` (basics) and `layouts.md`
(layout) — this file only lists extractable props/state and hardcoded elements.

---

## Layout components

### AppShell
- Source: `frontend/src/components/AppShell.tsx`
- Category: layout
- Description: Authenticated app frame — collapsible-section sidebar, topbar with breadcrumb/search/alerts, mobile tab strip, routed content area.
- Extractable state/nav props: nav sections data (currently hardcoded import of `NAV_SECTIONS`/`ALL_NAV_ITEMS` from `@/lib/nav` — could become a `sections` prop); collapsed-section state (currently `localStorage`-persisted internally, key `evowerk.nav.collapsed`); command-palette open/close state; active-route match (`findNavMatch`) for breadcrumb; org name/initials (from a `useQuery` to `/orgs/me` — could become an `org` prop); logout handler (currently calls `apiFetch("/auth/logout")` + `useAuthStore.clear()` + `navigate("/login")` directly — should be a callback prop).
- Hardcoded elements: brand lockup text "Evowerk" / "Cloud MRP"; sidebar width `248px`; topbar height `56px` (`h-14`); "Search…" placeholder + `Ctrl K` kbd hint; alerts bell routes to hardcoded `/alerts`; outer container `max-w-[1680px]`, main content `max-w-6xl`.

### AuthLayout
- Source: `frontend/src/components/AuthLayout.tsx`
- Category: layout
- Description: Two-pane auth screen — left pitch/brand panel (desktop only), right centered form card.
- Extractable state/nav props: `title`, `subtitle`, `children`, `footer` are already clean props. The 3-item `PITCH` array (icon/title/text) is hardcoded module-level data — good candidate to lift to a `pitchItems` prop.
- Hardcoded elements: brand lockup text; headline copy "The operating layer for modern manufacturing."; subhead copy; footer copyright line with `© {year} Evowerk · Multi-tenant · Audit-logged · MFA-ready`; the 3 pitch bullets' icons (`Boxes`, `Gauge`, `TrendingUp`) and copy.

### PageHeader
- Source: `frontend/src/components/PageHeader.tsx`
- Category: layout
- Description: Standard top-of-page title block (eyebrow + h1 + description + actions), used by nearly every routed page.
- Extractable state/nav props: fully prop-driven already — `title`, `description`, `actions`, `eyebrow`. No internal state.
- Hardcoded elements: none beyond layout spacing/typography classes.

### PageTabs
- Source: `frontend/src/components/PageTabs.tsx`
- Category: layout
- Description: Segmented-control tab switcher (generic over string-literal union `T`).
- Extractable state/nav props: `tabs: {value,label}[]`, `active`, `onChange` — fully controlled, no internal state; consumer owns the active-tab state (e.g. `Warehouse.tsx` toggling `"inventory" | "locations"`).
- Hardcoded elements: none.

### Nav data (`@/lib/nav.ts`)
- Source: `frontend/src/lib/nav.ts`
- Category: layout (data, not a component)
- Description: Single source of truth for sidebar/topbar/palette navigation — 6 sections, 30 items, each with `to`/`label`/`icon`/`end`.
- Extractable state/nav props: this *is* the prop payload `AppShell`/`CommandPalette` should ideally receive rather than importing directly — worth exposing as a top-level `navSections` config input to a DraftComponent version of `AppShell`.
- Hardcoded elements: every route path, label, section grouping, and icon choice is hardcoded here (by design — it's the nav's source of truth).

---

## Basic components

### Button
- Source: `frontend/src/components/ui/button.tsx`
- Category: basic
- Description: cva-variant button (5 variants × 4 sizes) with built-in loading spinner.
- Extractable state/nav props: `variant`, `size`, `loading`, `disabled`, all native button attrs.
- Hardcoded elements: exact shadow/glow values per variant (inset highlight + drop shadow); active-state `translate-y-px` press effect; icon auto-sizing via `[&_svg]:size-4`.

### Card (+ CardHeader/CardTitle/CardDescription/CardContent)
- Source: `frontend/src/components/ui/card.tsx`
- Category: basic
- Description: Composable panel container, the base surface for nearly every content block in the app.
- Extractable state/nav props: none — pure presentational, `className` passthrough only.
- Hardcoded elements: fixed padding (`p-6`, `p-6 pt-0`), fixed `gap-1.5` header stack, fixed title typography (`text-lg font-semibold`).

### Dialog
- Source: `frontend/src/components/ui/dialog.tsx`
- Category: basic
- Description: Controlled modal with backdrop-click-to-close (no portal, no focus trap, no ESC handling — simpler than `CommandPalette`).
- Extractable state/nav props: `open`, `onClose`, `title`, `children`, `className` — fully controlled by parent.
- Hardcoded elements: `max-w-lg` width cap; `max-h-[70vh]` scrollable body; close button is always a `Button variant="ghost" size="icon"` with an `X` icon, no way to hide/replace it.

### Skeleton
- Source: `frontend/src/components/ui/skeleton.tsx`
- Category: basic
- Description: Shimmering loading-state block.
- Extractable state/nav props: none — size fully controlled via `className`.
- Hardcoded elements: shimmer gradient stops/opacity, 2s linear infinite animation timing (from `tailwind.config.js`).

### Table primitives (Table, TableWrap, THead, TBody, TR, TH, TD)
- Source: `frontend/src/components/ui/table.tsx`
- Category: basic
- Description: Full table system; `TableWrap` is the scroll/border/shadow container every `<Table>` sits inside.
- Extractable state/nav props: none — pure structural wrappers around native table elements.
- Hardcoded elements: header cell styling (`text-[11px] uppercase tracking-[0.08em]`); row hover tint; `TD` always applies `.tabular` numeric font-variant (assumes most cell content is numeric-ish).

### Alert
- Source: `frontend/src/components/ui/alert.tsx`
- Category: basic
- Description: Inline banner with 4 semantic variants, each auto-pairing an icon.
- Extractable state/nav props: `variant` (`info`/`success`/`warning`/`destructive`), `children`.
- Hardcoded elements: the variant→icon mapping (`Info`/`CheckCircle2`/`AlertTriangle`/`XCircle`) is fixed, not swappable via props; uses raw Tailwind color families (`sky`/`emerald`/`amber`/`rose`) rather than the CSS-variable token layer.

### Badge
- Source: `frontend/src/components/ui/badge.tsx`
- Category: basic
- Description: Small status/type pill, 9 color variants.
- Extractable state/nav props: `variant`.
- Hardcoded elements: the 9-variant color palette is fixed inline (not theme-token-driven for most variants — direct Tailwind color utilities).

### Checkbox / Input / Label / Select
- Source: `frontend/src/components/ui/{checkbox,input,label,select}.tsx`
- Category: basic
- Description: Themed native form controls (no headless-UI abstraction — plain `<input>`/`<select>`/`<label>` restyled).
- Extractable state/nav props: pass through native HTML attrs (`value`, `onChange`, `disabled`, `required`, etc.) — fully controlled by consumer forms.
- Hardcoded elements: `Select` always renders a `ChevronDown` overlay icon (not optional); `Checkbox` accent color is hardcoded to `accent-[hsl(var(--primary))]`.

### EmptyState
- Source: `frontend/src/components/EmptyState.tsx`
- Category: basic
- Description: Dashed-border placeholder for empty lists/tables/results.
- Extractable state/nav props: `icon`, `title`, `description`, `className`.
- Hardcoded elements: fixed vertical padding (`py-14`), fixed icon-badge size (`size-12`) and border style.

### StatCard
- Source: `frontend/src/components/StatCard.tsx`
- Category: basic
- Description: KPI tile (label + big value + optional icon + hint), used on Dashboard and every analytics page.
- Extractable state/nav props: `label`, `value`, `icon`, `hint`.
- Hardcoded elements: hover glow blur/position (`-right-8 -top-8 size-24 blur-2xl`); fixed value typography (`text-3xl font-semibold`); icon badge always `size-8` primary-tinted.

### CommandPalette
- Source: `frontend/src/components/CommandPalette.tsx` *(untracked — new)*
- Category: basic (behaviorally a layout/nav component, but self-contained enough to extract standalone)
- Description: Ctrl/Cmd-K searchable navigation overlay with keyboard nav (arrows/enter/escape).
- Extractable state/nav props: `open`, `onClose` are the only external props; internally owns `query`, `selected` index, and derives filtered `results` from the imported `ALL_NAV_ITEMS` — that data source should become a `items: {to,label,icon,section}[]` prop for reuse outside this nav's exact shape.
- Hardcoded elements: placeholder text "Go to page…"; "No pages match" empty message; `ESC` kbd hint; result list max-height `50vh`; overlay position `pt-[14vh]`.

### BrandMark
- Source: `frontend/src/components/BrandMark.tsx`
- Category: basic
- Description: Inline SVG logo (ring + zig-zag waveform), monochrome/`currentColor`.
- Extractable state/nav props: `className`, `title`.
- Hardcoded elements: entire SVG path geometry (`viewBox="0 0 100 100"`, circle + polyline points) is fixed brand artwork, not parametrized.

### Field
- Source: `frontend/src/components/Field.tsx`
- Category: basic
- Description: Form-field wrapper (label + control + hint), used in essentially every create/edit form.
- Extractable state/nav props: `label`, `htmlFor`, `hint`, `className`, `children`.
- Hardcoded elements: fixed vertical spacing (`space-y-2`) between label/control/hint.

### ItemSelect
- Source: `frontend/src/components/ItemSelect.tsx`
- Category: basic (domain-specific — lower priority for generic extraction)
- Description: Item-picker select with inline "+ New item…" quick-create sub-form.
- Extractable state/nav props: `items`, `value`, `onChange`, `excludeId`, `required` are the public contract; internally owns `adding`/`sku`/`name` quick-create form state and a `useMutation` POST to `/items` — the API endpoint and payload shape are hardcoded, not parametrized, so this is really an Items-domain component rather than a generic combobox.
- Hardcoded elements: `/items` POST endpoint; `sku`/`name` field set; "+ New item..." option label; the `items` query-key invalidated on success is hardcoded to `["items"]`.
