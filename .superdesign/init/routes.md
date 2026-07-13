# Routes

Router: `react-router-dom` v6 `<Routes>`/`<Route>` (not `createBrowserRouter`). Mounted at
`frontend/src/main.tsx` inside `<BrowserRouter>`, which wraps `<App />`
(`frontend/src/App.tsx`). Auth guard is a local `RequireAuth` wrapper component reading
`useAuthStore`.

## `main.tsx` (router mount)

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "./index.css";
import { App } from "./App";
import { queryClient } from "./lib/queryClient";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
```

## `App.tsx` (full route config)

```tsx
import { Navigate, Route, Routes } from "react-router-dom";
import { useAuthStore } from "./store/authStore";
import { AppShell } from "./components/AppShell";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";
import { MfaChallenge } from "./pages/MfaChallenge";
import { Dashboard } from "./pages/Dashboard";
import { Items } from "./pages/Items";
import { Boms } from "./pages/Boms";
import { Procurement } from "./pages/Procurement";
import { Lots } from "./pages/Lots";
import { Warehouse } from "./pages/Warehouse";
import { Planning } from "./pages/Planning";
import { Datasets } from "./pages/analytics/Datasets";
import { DatasetDetail } from "./pages/analytics/DatasetDetail";
import { SqlEditor } from "./pages/SqlEditor";
import { Simulation } from "./pages/Simulation";
import { Oee } from "./pages/analytics/Oee";
import { PlantSummary } from "./pages/analytics/PlantSummary";
import { Downtime } from "./pages/analytics/Downtime";
import { Throughput } from "./pages/analytics/Throughput";
import { Scrap } from "./pages/analytics/Scrap";
import { Shifts } from "./pages/analytics/Shifts";
import { Spc } from "./pages/analytics/Spc";
import { Ask } from "./pages/Ask";
import { Reports } from "./pages/Reports";
import { Dashboards as BiDashboards } from "./pages/bi/Dashboards";
import { ChartBuilder } from "./pages/bi/ChartBuilder";
import { DashboardDetail as BiDashboardDetail } from "./pages/bi/DashboardDetail";
import { Workflows } from "./pages/workflows/Workflows";
import { WorkflowBuilder } from "./pages/workflows/WorkflowBuilder";
import { ReportBuilder } from "./pages/ReportBuilder";
import { AutoML } from "./pages/AutoML";
import { AutoMLRun } from "./pages/automl/AutoMLRun";
import { Kpis } from "./pages/Kpis";
import { Integrations } from "./pages/Integrations";
import { Realtime } from "./pages/Realtime";
import { RcaCases } from "./pages/RcaCases";
import { RunHistory } from "./pages/workflows/RunHistory";
import { Predictive } from "./pages/Predictive";
import { MfgForecast } from "./pages/MfgForecast";
import { Scenarios } from "./pages/Scenarios";
import { ComputerVision } from "./pages/ComputerVision";
import { Alerts } from "./pages/Alerts";

function RequireAuth({ children }: { children: JSX.Element }) {
  const accessToken = useAuthStore((s) => s.accessToken);
  if (!accessToken) return <Navigate to="/login" replace />;
  return children;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route
        path="/mfa"
        element={
          <RequireAuth>
            <MfaChallenge />
          </RequireAuth>
        }
      />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route path="/" element={<Dashboard />} />
        <Route path="/items" element={<Items />} />
        <Route path="/boms" element={<Boms />} />
        <Route path="/procurement" element={<Procurement />} />
        <Route path="/lots" element={<Lots />} />
        <Route path="/warehouse" element={<Warehouse />} />
        <Route path="/planning" element={<Planning />} />
        <Route path="/analytics" element={<Navigate to="/analytics/oee" replace />} />
        <Route path="/analytics/oee" element={<Oee />} />
        <Route path="/analytics/plant-summary" element={<PlantSummary />} />
        <Route path="/analytics/downtime" element={<Downtime />} />
        <Route path="/analytics/throughput" element={<Throughput />} />
        <Route path="/analytics/scrap" element={<Scrap />} />
        <Route path="/analytics/shifts" element={<Shifts />} />
        <Route path="/analytics/spc" element={<Spc />} />
        <Route path="/datasets" element={<Datasets />} />
        <Route path="/datasets/:id" element={<DatasetDetail />} />
        <Route path="/sql-editor" element={<SqlEditor />} />
        <Route path="/simulation" element={<Simulation />} />
        <Route path="/ask" element={<Ask />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/bi" element={<BiDashboards />} />
        <Route path="/bi/new" element={<ChartBuilder />} />
        <Route path="/bi/:id" element={<BiDashboardDetail />} />
        <Route path="/workflows" element={<Workflows />} />
        <Route path="/workflows/:id" element={<WorkflowBuilder />} />
        <Route path="/runs" element={<RunHistory />} />
        <Route path="/report-builder" element={<ReportBuilder />} />
        <Route path="/automl" element={<AutoML />} />
        <Route path="/automl/:id" element={<AutoMLRun />} />
        <Route path="/kpis" element={<Kpis />} />
        <Route path="/integrations" element={<Integrations />} />
        <Route path="/realtime" element={<Realtime />} />
        <Route path="/rca-cases" element={<RcaCases />} />
        <Route path="/predictive" element={<Predictive />} />
        <Route path="/mfg-forecast" element={<MfgForecast />} />
        <Route path="/scenarios" element={<Scenarios />} />
        <Route path="/computer-vision" element={<ComputerVision />} />
        <Route path="/alerts" element={<Alerts />} />
      </Route>
    </Routes>
  );
}
```

## Route table

| Path | Component | File | Layout |
|---|---|---|---|
| `/login` | `Login` | `src/pages/Login.tsx` | `AuthLayout` (used inside the page) |
| `/register` | `Register` | `src/pages/Register.tsx` | `AuthLayout` |
| `/mfa` | `MfaChallenge` | `src/pages/MfaChallenge.tsx` | `AuthLayout` (behind `RequireAuth`) |
| `/` | `Dashboard` | `src/pages/Dashboard.tsx` | `AppShell` |
| `/items` | `Items` | `src/pages/Items.tsx` | `AppShell` |
| `/boms` | `Boms` | `src/pages/Boms.tsx` | `AppShell` |
| `/procurement` | `Procurement` | `src/pages/Procurement.tsx` | `AppShell` |
| `/lots` | `Lots` | `src/pages/Lots.tsx` | `AppShell` |
| `/warehouse` | `Warehouse` | `src/pages/Warehouse.tsx` | `AppShell` (tabs: Inventory / Locations panels) |
| `/planning` | `Planning` | `src/pages/Planning.tsx` | `AppShell` |
| `/analytics` | redirect → `/analytics/oee` | — | — |
| `/analytics/oee` | `Oee` | `src/pages/analytics/Oee.tsx` | `AppShell` |
| `/analytics/plant-summary` | `PlantSummary` | `src/pages/analytics/PlantSummary.tsx` | `AppShell` |
| `/analytics/downtime` | `Downtime` | `src/pages/analytics/Downtime.tsx` | `AppShell` |
| `/analytics/throughput` | `Throughput` | `src/pages/analytics/Throughput.tsx` | `AppShell` |
| `/analytics/scrap` | `Scrap` | `src/pages/analytics/Scrap.tsx` | `AppShell` |
| `/analytics/shifts` | `Shifts` | `src/pages/analytics/Shifts.tsx` | `AppShell` |
| `/analytics/spc` | `Spc` | `src/pages/analytics/Spc.tsx` | `AppShell` |
| `/datasets` | `Datasets` | `src/pages/analytics/Datasets.tsx` | `AppShell` |
| `/datasets/:id` | `DatasetDetail` | `src/pages/analytics/DatasetDetail.tsx` | `AppShell` |
| `/sql-editor` | `SqlEditor` | `src/pages/SqlEditor.tsx` | `AppShell` |
| `/simulation` | `Simulation` | `src/pages/Simulation.tsx` | `AppShell` |
| `/ask` | `Ask` | `src/pages/Ask.tsx` | `AppShell` |
| `/reports` | `Reports` | `src/pages/Reports.tsx` | `AppShell` |
| `/bi` | `Dashboards` (aliased `BiDashboards`) | `src/pages/bi/Dashboards.tsx` | `AppShell` |
| `/bi/new` | `ChartBuilder` | `src/pages/bi/ChartBuilder.tsx` | `AppShell` |
| `/bi/:id` | `DashboardDetail` (aliased `BiDashboardDetail`) | `src/pages/bi/DashboardDetail.tsx` | `AppShell` |
| `/workflows` | `Workflows` | `src/pages/workflows/Workflows.tsx` | `AppShell` |
| `/workflows/:id` | `WorkflowBuilder` | `src/pages/workflows/WorkflowBuilder.tsx` | `AppShell` |
| `/runs` | `RunHistory` | `src/pages/workflows/RunHistory.tsx` | `AppShell` |
| `/report-builder` | `ReportBuilder` | `src/pages/ReportBuilder.tsx` | `AppShell` |
| `/automl` | `AutoML` | `src/pages/AutoML.tsx` | `AppShell` |
| `/automl/:id` | `AutoMLRun` | `src/pages/automl/AutoMLRun.tsx` | `AppShell` |
| `/kpis` | `Kpis` | `src/pages/Kpis.tsx` | `AppShell` |
| `/integrations` | `Integrations` | `src/pages/Integrations.tsx` | `AppShell` |
| `/realtime` | `Realtime` | `src/pages/Realtime.tsx` | `AppShell` |
| `/rca-cases` | `RcaCases` | `src/pages/RcaCases.tsx` | `AppShell` |
| `/predictive` | `Predictive` | `src/pages/Predictive.tsx` | `AppShell` |
| `/mfg-forecast` | `MfgForecast` | `src/pages/MfgForecast.tsx` | `AppShell` |
| `/scenarios` | `Scenarios` | `src/pages/Scenarios.tsx` | `AppShell` |
| `/computer-vision` | `ComputerVision` | `src/pages/ComputerVision.tsx` | `AppShell` |
| `/alerts` | `Alerts` | `src/pages/Alerts.tsx` | `AppShell` |

Note: `src/pages/Suppliers.tsx`, `src/pages/Inventory.tsx` (exports `InventoryPanel`, used
by `Warehouse.tsx`), and `src/pages/Locations.tsx` (exports `LocationsPanel`, also used by
`Warehouse.tsx`), and `src/pages/GeneratePos.tsx` exist on disk but are **not** directly
routed in `App.tsx` — they're either sub-panels composed into a routed page or currently
unreferenced by the router.

## Page summaries

- **Dashboard** (`/`) — Welcome header with date eyebrow and org name; 4 `StatCard`s (Items, BOMs, Suppliers, Purchase Orders) from parallel `useQuery` counts; a "Morning Brief" card listing inventory-status lines and lot alerts (stockout/low/excess/slow-moving, on-hold/expiring lots) as colored left-border link rows; an "Explore" grid of link cards to the app's major sections.
- **Items** — Item catalog: filterable/searchable table with create/edit modal form, type badges, active/inactive toggle.
- **Boms** — Bill-of-materials list with expandable rows showing components (via `ItemSelect`), create/edit forms.
- **Procurement** — Composes supplier and purchase-order management (POs, generate-POs flow).
- **Lots** — Lot-tracked stock table: lot number, location, hold status, expiry, create/adjust forms.
- **Warehouse** — Tabbed (`PageTabs`) container switching between `InventoryPanel` (on-hand stock by location) and `LocationsPanel` (storage location CRUD).
- **Planning** — Demand forecast + MRP run controls, generates purchase-order suggestions with date helpers (`today`, `plusDays`).
- **analytics/Oee, PlantSummary, Downtime, Throughput, Scrap, Shifts, Spc** — Plant-analytics dashboards sharing `src/pages/analytics/shared.tsx` (ECharts wrapper `EChart`, `EngineScreen` loading/empty state, chart color tokens `C`, axis/tooltip style constants, formatters `fmt`/`pct`/`money`/`oeeColor`, `rankedBarOption` helper, `useDatasets` query hook).
- **Datasets / DatasetDetail** — Dataset upload/browse and per-dataset column/preview detail view.
- **SqlEditor** — Ad-hoc SQL query runner against uploaded datasets, with result table and query history.
- **Simulation** — What-if simulation runner over dataset-derived parameters.
- **Ask** — Natural-language Q&A chat over datasets, rendering ECharts answers inline.
- **Reports** — Scheduled/on-demand PDF/CSV report list and export triggers.
- **bi/Dashboards, ChartBuilder, DashboardDetail** — BI dashboard list, a chart-builder form (dataset → chart type/config), and a dashboard detail view arranging saved charts (uses `bi/shared.tsx` for chart-option builders).
- **workflows/Workflows, WorkflowBuilder, RunHistory** — Workflow definition list, a builder/editor, and execution run history.
- **ReportBuilder** — Custom report layout/query builder distinct from the simpler `Reports` page.
- **AutoML / automl/AutoMLRun** — AutoML job list and a per-run detail view with metrics charts and leaderboard tables.
- **Kpis** — Custom KPI definitions computed over datasets, with a create/edit dialog.
- **Integrations** — Third-party integration connections (API keys/webhooks) with add/edit dialog.
- **Realtime** — Live SSE-fed metrics/event feed (`subscribeToSSE` from `@/lib/sse`).
- **RcaCases** — Root-cause-analysis case tracking tied to dataset queries.
- **Predictive** — Predictive-maintenance model outputs/alerts over datasets.
- **MfgForecast** — Manufacturing demand forecast charts/tables.
- **Scenarios** — Saved what-if scenario definitions built via a shared query-builder (`useQueryBuilder`, `QueryBuilderFields` from `analytics/shared`).
- **ComputerVision** — Image-upload inference workflow (defect/quality detection).
- **Alerts** — Alert rule list/create, backed by dataset queries.
- **Login / Register / MfaChallenge** — Auth screens inside `AuthLayout`; `Login`/`Register` write to `useAuthStore`, `MfaChallenge` verifies a TOTP-style code.
