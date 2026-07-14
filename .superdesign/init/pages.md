# Pages — Dependency Trees

Import trees for the app's key pages, traced recursively through local (relative and
`@/`-aliased) imports only. `node_modules` packages are listed as leaves (name only, no
further expansion). `@/lib/utils` (`cn`) and `@/lib/status` (`humanize`, status→badge
variant mappers) are common leaves omitted from repeat sub-trees after first mention for
brevity where noted.

---

## Dashboard (`/`)

`frontend/src/pages/Dashboard.tsx`

```
Dashboard.tsx
├─ react (useState — n/a, uses useQuery only)
├─ @tanstack/react-query (useQuery)
├─ react-router-dom (Link)
├─ lucide-react (ArrowUpRight, BarChart3, Boxes, CalendarRange, Database, FileText, Layers, LayoutGrid, Package, ShieldCheck, ShoppingCart, Sparkles, Sunrise, Truck, Warehouse)
├─ ../api/client (apiFetch)
│   └─ ../store/authStore (useAuthStore) [zustand]
├─ @/components/PageHeader (PageHeader)
├─ @/components/StatCard (StatCard)
│   └─ @/components/ui/card (Card)
│       └─ @/lib/utils (cn)
├─ @/components/ui/card (Card, CardContent, CardDescription, CardHeader, CardTitle)
├─ @/components/ui/button (Button)
│   └─ class-variance-authority (cva), lucide-react (Loader2), @/lib/utils (cn)
├─ @/components/ui/badge (Badge)
│   └─ class-variance-authority (cva), @/lib/utils (cn)
├─ @/components/ui/skeleton (Skeleton)
│   └─ @/lib/utils (cn)
├─ @/lib/status (humanize, inventoryStatusVariant)
│   └─ @/components/ui/badge (type BadgeProps)
└─ @/lib/utils (cn)
    └─ clsx, tailwind-merge
```

---

## Items (`/items`)

`frontend/src/pages/Items.tsx`

```
Items.tsx
├─ react (Fragment, FormEvent, useState)
├─ react-router-dom (Link)
├─ lucide-react (Package)
├─ @tanstack/react-query (useMutation, useQuery, useQueryClient)
├─ ../api/client (apiFetch) → ../store/authStore (useAuthStore)
├─ @/components/PageHeader (PageHeader)
├─ @/components/Field (Field)
│   └─ @/components/ui/label (Label), @/lib/utils (cn)
├─ @/components/EmptyState (EmptyState)
│   └─ @/lib/utils (cn)
├─ @/components/ui/card (Card, CardContent, CardHeader, CardTitle)
├─ @/components/ui/input (Input)
├─ @/components/ui/select (Select) → lucide-react (ChevronDown)
├─ @/components/ui/button (Button)
├─ @/components/ui/checkbox (Checkbox)
├─ @/components/ui/label (Label)
├─ @/components/ui/badge (Badge)
├─ @/components/ui/alert (Alert) → lucide-react (AlertTriangle, CheckCircle2, Info, XCircle)
├─ @/components/ui/table (Table, TBody, TD, TH, THead, TR, TableWrap)
└─ @/lib/status (humanize, itemTypeVariant)
```

---

## Boms (`/boms`)

`frontend/src/pages/Boms.tsx`

```
Boms.tsx
├─ react (FormEvent, useState)
├─ lucide-react (ChevronDown, ChevronRight, Layers, Trash2)
├─ @tanstack/react-query (useMutation, useQuery, useQueryClient)
├─ ../api/client (apiFetch)
├─ @/components/PageHeader (PageHeader)
├─ @/components/Field (Field)
├─ @/components/EmptyState (EmptyState)
├─ @/components/ui/card (Card, CardContent, CardHeader, CardTitle)
├─ @/components/ui/input (Input)
├─ @/components/ui/button (Button)
├─ @/components/ui/badge (Badge)
├─ @/components/ui/alert (Alert)
├─ @/components/ui/table (Table, TBody, TD, TH, THead, TR, TableWrap)
├─ @/components/ItemSelect (ItemSelect)
│   ├─ @tanstack/react-query (useMutation, useQueryClient)
│   ├─ @/api/client (apiFetch)
│   ├─ @/components/ui/select (Select)
│   ├─ @/components/ui/input (Input)
│   └─ @/components/ui/button (Button)
├─ @/lib/status (humanize)
└─ @/lib/utils (cn)
```

---

## Warehouse (`/warehouse`) — composes Inventory + Locations panels

`frontend/src/pages/Warehouse.tsx`

```
Warehouse.tsx
├─ react (useState)
├─ @/components/PageHeader (PageHeader)
├─ @/components/PageTabs (PageTabs) → @/lib/utils (cn)
├─ ./Inventory (InventoryPanel)
│   frontend/src/pages/Inventory.tsx
│   ├─ react (FormEvent, useState)
│   ├─ lucide-react (Warehouse icon)
│   ├─ @tanstack/react-query (useMutation, useQuery, useQueryClient)
│   ├─ ../api/client (apiFetch)
│   ├─ @/components/Field (Field)
│   ├─ @/components/EmptyState (EmptyState)
│   ├─ @/components/ui/card (Card, CardContent, CardHeader, CardTitle)
│   ├─ @/components/ui/input (Input)
│   ├─ @/components/ui/select (Select)
│   ├─ @/components/ui/button (Button)
│   ├─ @/components/ui/badge (Badge)
│   ├─ @/components/ui/alert (Alert)
│   ├─ @/components/ui/table (Table, TBody, TD, TH, THead, TR, TableWrap)
│   ├─ @/lib/status (humanize, inventoryStatusVariant)
│   └─ @/lib/utils (cn)
└─ ./Locations (LocationsPanel)
    frontend/src/pages/Locations.tsx
    ├─ react (FormEvent, useState)
    ├─ lucide-react (MapPin)
    ├─ @tanstack/react-query (useMutation, useQuery, useQueryClient)
    ├─ ../api/client (apiFetch)
    ├─ @/components/Field (Field)
    ├─ @/components/EmptyState (EmptyState)
    ├─ @/components/ui/card (Card, CardContent, CardHeader, CardTitle)
    ├─ @/components/ui/input (Input)
    ├─ @/components/ui/button (Button)
    ├─ @/components/ui/badge (Badge)
    ├─ @/components/ui/alert (Alert)
    └─ @/components/ui/table (Table, TBody, TD, TH, THead, TR, TableWrap)
```

---

## Planning (`/planning`)

`frontend/src/pages/Planning.tsx`

```
Planning.tsx
├─ react (FormEvent, useState)
├─ react-router-dom (Link)
├─ @tanstack/react-query (useMutation, useQuery, useQueryClient)
├─ ../api/client (apiFetch)
├─ @/lib/date (today, plusDays)
├─ @/components/PageHeader (PageHeader)
├─ @/components/Field (Field)
├─ @/components/ui/card (Card, CardContent, CardDescription, CardHeader, CardTitle)
├─ @/components/ui/input (Input)
├─ @/components/ui/select (Select)
├─ @/components/ui/button (Button)
├─ @/components/ui/badge (Badge)
├─ @/components/ui/alert (Alert)
└─ @/components/ui/table (Table, TBody, TD, TH, THead, TR, TableWrap)
```

(Related but not directly routed: `frontend/src/pages/GeneratePos.tsx` — a generate-POs
flow with the same import shape plus `@/components/ui/checkbox`.)

---

## analytics/Oee (`/analytics/oee`)

`frontend/src/pages/analytics/Oee.tsx`

```
Oee.tsx
├─ @/components/ui/badge (Badge)
├─ @/components/ui/alert (Alert)
├─ @/components/ui/card (Card, CardContent, CardHeader, CardTitle)
├─ @/components/ui/table (Table, TBody, TD, TH, THead, TR, TableWrap)
├─ @/components/StatCard (StatCard)
├─ ./shared (from frontend/src/pages/analytics/shared.tsx)
│   AXIS_LABEL, AXIS_LINE, C, EChart, EngineScreen, SPLIT_LINE, TOOLTIP, fmt, money, oeeColor, pct, rankedBarOption
│   shared.tsx itself imports:
│   ├─ react (useEffect, useMemo, useRef, useState, type ReactNode)
│   ├─ @tanstack/react-query (useMutation, useQuery, useQueryClient)
│   ├─ echarts (* as echarts)
│   ├─ ../../api/client (apiFetch)
│   ├─ @/components/PageHeader (PageHeader)
│   ├─ @/components/Field (Field)
│   ├─ @/components/ui/card (Card, CardContent, CardHeader, CardTitle)
│   ├─ @/components/ui/input (Input)
│   ├─ @/components/ui/select (Select)
│   ├─ @/components/ui/button (Button)
│   ├─ @/components/ui/alert (Alert)
│   └─ @/components/ui/checkbox (Checkbox)
│   (shared.tsx defines: EChart — thin ECharts React wrapper; EngineScreen — dataset-engine
│   picker + loading/empty wrapper; useDatasets — dataset list query hook; useQueryBuilder /
│   QueryBuilderFields — ad-hoc filter builder; C / TOOLTIP / AXIS_LABEL / AXIS_LINE /
│   SPLIT_LINE — chart theming constants; fmt / pct / money / oeeColor / rankedBarOption /
│   gradientAreaSeries / ColumnChecklist — formatting + chart-option helpers reused by every
│   analytics page)
└─ @/lib/utils (cn)
```

All of `analytics/PlantSummary.tsx`, `Downtime.tsx`, `Throughput.tsx`, `Scrap.tsx`,
`Shifts.tsx`, `Spc.tsx` follow this identical shape: `ui/card` + `ui/table` +
`ui/badge`/`ui/alert` + `StatCard` + a subset of `./shared` exports.

---

## bi/Dashboards (`/bi`)

`frontend/src/pages/bi/Dashboards.tsx`

```
Dashboards.tsx
├─ react (useState)
├─ react-router-dom (Link, useNavigate)
├─ @tanstack/react-query (useMutation, useQuery, useQueryClient)
├─ lucide-react (LayoutGrid, Plus)
├─ ../../api/client (apiFetch)
├─ @/components/PageHeader (PageHeader)
├─ @/components/ui/alert (Alert)
├─ @/components/ui/button (Button)
├─ @/components/ui/card (Card, CardContent, CardTitle)
├─ @/components/Field (Field)
├─ @/components/ui/input (Input)
└─ @/components/ui/skeleton (Skeleton)
```

Related, same folder: `bi/ChartBuilder.tsx` (dataset → chart-type/config form) and
`bi/DashboardDetail.tsx` — the latter additionally pulls:
```
DashboardDetail.tsx
├─ react-router-dom (useNavigate, useParams)
├─ lucide-react (ArrowDown, ArrowLeft, ArrowUp, Trash2)
├─ ../../api/client (apiFetch)
├─ @/components/PageHeader, ui/alert, ui/button, ui/card, ui/skeleton
├─ ../analytics/shared (EChart)
└─ ./shared (chartOption, toCategoryValueRows, type ChartType)
    frontend/src/pages/bi/shared.tsx imports:
    ├─ echarts (* as echarts)
    └─ ../analytics/shared (AXIS_LABEL, AXIS_LINE, C, SPLIT_LINE, TOOLTIP, gradientAreaSeries)
```

---

## workflows/Workflows (`/workflows`)

`frontend/src/pages/workflows/Workflows.tsx`

```
Workflows.tsx
├─ react (useState)
├─ react-router-dom (Link, useNavigate)
├─ lucide-react (Workflow as WorkflowIcon)
├─ @tanstack/react-query (useMutation, useQuery, useQueryClient)
├─ ../../api/client (apiFetch)
├─ @/components/PageHeader (PageHeader)
├─ @/components/EmptyState (EmptyState)
├─ @/components/ui/card (Card)
├─ @/components/ui/button (Button)
└─ @/components/ui/table (Table, TBody, TD, TH, THead, TR, TableWrap)
```

Sibling pages `workflows/WorkflowBuilder.tsx` (node/step editor) and
`workflows/RunHistory.tsx` (execution log table, imports `useQuery` + `apiFetch` +
`PageHeader`/`ui/table`) share the same primitive dependency set.

---

## Alerts (`/alerts`)

`frontend/src/pages/Alerts.tsx`

```
Alerts.tsx
├─ react (useMemo, useState)
├─ @tanstack/react-query (useMutation, useQuery, useQueryClient)
├─ @/api/client (apiFetch)
├─ @/components/PageHeader (PageHeader)
├─ @/components/Field (Field)
├─ @/components/ui/alert (Alert)
├─ @/components/ui/badge (Badge)
├─ @/components/ui/button (Button)
├─ @/components/ui/card (Card, CardContent, CardHeader, CardTitle)
├─ @/components/ui/checkbox (Checkbox)
├─ @/components/ui/input (Input)
├─ @/components/ui/select (Select)
├─ @/components/ui/table (Table, TBody, TD, TH, THead, TR, TableWrap)
├─ @/pages/analytics/shared (useDatasets) — see Oee tree above for shared.tsx's own deps
└─ @/lib/utils (cn)
```

---

## Ask (`/ask`)

`frontend/src/pages/Ask.tsx`

```
Ask.tsx
├─ react (FormEvent, useEffect, useRef, useState)
├─ @tanstack/react-query (useMutation)
├─ echarts (type EChartsOption)
├─ lucide-react (Send, Sparkles)
├─ ../api/client (apiFetch)
├─ ./analytics/shared (AXIS_LABEL, AXIS_LINE, C, EChart, SPLIT_LINE, TOOLTIP, useDatasets)
├─ @/components/PageHeader (PageHeader)
├─ @/components/ui/card (Card, CardContent)
├─ @/components/ui/button (Button)
├─ @/components/ui/input (Input)
├─ @/components/ui/select (Select)
├─ @/components/ui/badge (Badge)
├─ @/components/ui/alert (Alert)
├─ @/components/ui/table (Table, TBody, TD, TH, THead, TR, TableWrap)
└─ @/lib/utils (cn)
```

---

## Shared leaf dependencies (referenced across most trees above)

- `@/api/client` (`apiFetch`) → `@/store/authStore` (`useAuthStore`, a `zustand` store holding `accessToken`/`refreshToken` and `clear()`).
- `@/lib/utils` (`cn`) → `clsx` + `tailwind-merge`.
- `@/lib/status` (`humanize`, `inventoryStatusVariant`, `itemTypeVariant`) → `@/components/ui/badge` (`type BadgeProps`).
- `@/lib/date` (`today`, `plusDays`) — plain date helpers, no further deps.
- `@/lib/queryClient` (`queryClient`) — `@tanstack/react-query`'s `QueryClient` instance, used only at `main.tsx`.
- `@/lib/sse` (`subscribeToSSE`) → `@/api/client` (`apiFetch`); used by `Realtime.tsx`.
