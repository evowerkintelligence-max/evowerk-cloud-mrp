import { Component, lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuthHydrated, useAuthStore } from "./store/authStore";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { AppShell } from "./components/AppShell";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";
import { Login } from "./pages/Login";
import { ForgotPassword } from "./pages/ForgotPassword";
import { ResetPassword } from "./pages/ResetPassword";
import { VerifyEmail } from "./pages/VerifyEmail";
import { OAuthCallback } from "./pages/OAuthCallback";
import { OAuthMfaVerify } from "./pages/OAuthMfaVerify";
import { MfaChallenge } from "./pages/MfaChallenge";

// Routed pages are lazy-loaded so one page's broken/missing dependency (e.g.
// @xyflow/react going missing under Workflows) 500s only that route in dev,
// instead of failing the App.tsx module graph and blanking every page.
// React.lazy needs a default export - this adapts a named one without
// repeating `.then((m) => ({ default: m.X }))` at every call site below.
function lazyPage(loader: () => Promise<Record<string, unknown>>, name: string) {
  return lazy(async () => ({ default: (await loader())[name] as ComponentType<any> }));
}

const Dashboard = lazyPage(() => import("./pages/Dashboard"), "Dashboard");
const Items = lazyPage(() => import("./pages/Items"), "Items");
const Boms = lazyPage(() => import("./pages/Boms"), "Boms");
const Procurement = lazyPage(() => import("./pages/Procurement"), "Procurement");
const Lots = lazyPage(() => import("./pages/Lots"), "Lots");
const Warehouse = lazyPage(() => import("./pages/Warehouse"), "Warehouse");
const Planning = lazyPage(() => import("./pages/Planning"), "Planning");
const Datasets = lazyPage(() => import("./pages/analytics/Datasets"), "Datasets");
const DatasetDetail = lazyPage(() => import("./pages/analytics/DatasetDetail"), "DatasetDetail");
const SqlEditor = lazyPage(() => import("./pages/SqlEditor"), "SqlEditor");
const Simulation = lazyPage(() => import("./pages/Simulation"), "Simulation");
const Oee = lazyPage(() => import("./pages/analytics/Oee"), "Oee");
const PaceTracking = lazyPage(() => import("./pages/analytics/PaceTracking"), "PaceTracking");
const PlantSummary = lazyPage(() => import("./pages/analytics/PlantSummary"), "PlantSummary");
const Downtime = lazyPage(() => import("./pages/analytics/Downtime"), "Downtime");
const DowntimeTracking = lazyPage(() => import("./pages/analytics/DowntimeTracking"), "DowntimeTracking");
const Throughput = lazyPage(() => import("./pages/analytics/Throughput"), "Throughput");
const Scrap = lazyPage(() => import("./pages/analytics/Scrap"), "Scrap");
const Shifts = lazyPage(() => import("./pages/analytics/Shifts"), "Shifts");
const Spc = lazyPage(() => import("./pages/analytics/Spc"), "Spc");
const Ask = lazyPage(() => import("./pages/Ask"), "Ask");
const BiDashboards = lazyPage(() => import("./pages/bi/Dashboards"), "Dashboards");
const ChartBuilder = lazyPage(() => import("./pages/bi/ChartBuilder"), "ChartBuilder");
const BiDashboardDetail = lazyPage(() => import("./pages/bi/DashboardDetail"), "DashboardDetail");
const Workflows = lazyPage(() => import("./pages/workflows/Workflows"), "Workflows");
const WorkflowBuilder = lazyPage(() => import("./pages/workflows/WorkflowBuilder"), "WorkflowBuilder");
const ReportBuilder = lazyPage(() => import("./pages/ReportBuilder"), "ReportBuilder");
const AutoML = lazyPage(() => import("./pages/AutoML"), "AutoML");
const AutoMLRun = lazyPage(() => import("./pages/automl/AutoMLRun"), "AutoMLRun");
const TemplatesHome = lazyPage(() => import("./pages/automl/TemplatesHome"), "TemplatesHome");
const AutoMLTemplateResultsView = lazyPage(() => import("./pages/automl/templates/ResultsView"), "ResultsView");
const Kpis = lazyPage(() => import("./pages/Kpis"), "Kpis");
const KpiDashboards = lazyPage(() => import("./pages/KpiDashboards"), "KpiDashboards");
const Integrations = lazyPage(() => import("./pages/Integrations"), "Integrations");
const Realtime = lazyPage(() => import("./pages/Realtime"), "Realtime");
const RcaCases = lazyPage(() => import("./pages/RcaCases"), "RcaCases");
const RunHistory = lazyPage(() => import("./pages/workflows/RunHistory"), "RunHistory");
const Predictive = lazyPage(() => import("./pages/Predictive"), "Predictive");
const MfgForecast = lazyPage(() => import("./pages/MfgForecast"), "MfgForecast");
const ComputerVision = lazyPage(() => import("./pages/ComputerVision"), "ComputerVision");
const Alerts = lazyPage(() => import("./pages/Alerts"), "Alerts");
const Customers = lazyPage(() => import("./pages/admin/Customers"), "Customers");

function PageFallback() {
  return (
    <div className="space-y-4 p-8">
      <Skeleton className="h-10 w-64" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

// Suspense only covers the *loading* state of a lazy import - if the chunk
// itself fails (a missing dependency, a syntax error), the error still
// throws past it and needs a boundary or it takes the whole tree down.
// Keyed by pathname in App() below so navigating away from a broken route
// clears the error instead of it sticking for the rest of the session.
class RouteErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return (
        <div className="p-8">
          <Alert variant="destructive">This page failed to load. Try refreshing.</Alert>
        </div>
      );
    }
    return this.props.children;
  }
}

function RequireAuth({ children }: { children: JSX.Element }) {
  const accessToken = useAuthStore((s) => s.accessToken);
  const hydrated = useAuthHydrated();
  if (!hydrated) return null;
  if (!accessToken) return <Navigate to="/login" replace />;
  return children;
}

interface EntitlementResponse {
  enabled_features: string[];
  is_staff: boolean;
}

function useEntitlements() {
  return useQuery<EntitlementResponse>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load org");
      return res.json();
    },
  });
}

function RequireFeature({ feature, children }: { feature: string; children: JSX.Element }) {
  const org = useEntitlements();
  if (org.isLoading) return null;
  if (!org.data?.enabled_features.includes(feature)) {
    return (
      <div className="p-8">
        <EmptyState title="Not included in your plan" description="Contact EvoWerk to add this feature to your account." />
      </div>
    );
  }
  return children;
}

export function RequireStaff({ children }: { children: JSX.Element }) {
  const org = useEntitlements();
  if (org.isLoading) return null;
  if (!org.data?.is_staff) return <Navigate to="/" replace />;
  return children;
}

export function App() {
  const { pathname } = useLocation();
  return (
    <RouteErrorBoundary key={pathname}>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route path="/verify-email" element={<VerifyEmail />} />
          <Route path="/oauth/callback" element={<OAuthCallback />} />
          <Route path="/oauth/mfa-verify" element={<OAuthMfaVerify />} />
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
            <Route path="/boms" element={<RequireFeature feature="boms"><Boms /></RequireFeature>} />
            <Route path="/procurement" element={<RequireFeature feature="procurement"><Procurement /></RequireFeature>} />
            <Route path="/lots" element={<RequireFeature feature="lots"><Lots /></RequireFeature>} />
            <Route path="/warehouse" element={<RequireFeature feature="warehouse"><Warehouse /></RequireFeature>} />
            <Route path="/planning" element={<RequireFeature feature="planning"><Planning /></RequireFeature>} />
            <Route path="/analytics" element={<Navigate to="/analytics/oee" replace />} />
            <Route path="/analytics/oee" element={<RequireFeature feature="oee"><Oee /></RequireFeature>} />
            <Route path="/analytics/pace-tracking" element={<RequireFeature feature="pace_tracking"><PaceTracking /></RequireFeature>} />
            <Route path="/analytics/plant-summary" element={<RequireFeature feature="plant_summary"><PlantSummary /></RequireFeature>} />
            <Route path="/analytics/downtime" element={<RequireFeature feature="downtime"><Downtime /></RequireFeature>} />
            <Route path="/analytics/downtime-tracking" element={<RequireFeature feature="downtime_tracking"><DowntimeTracking /></RequireFeature>} />
            <Route path="/analytics/throughput" element={<RequireFeature feature="throughput"><Throughput /></RequireFeature>} />
            <Route path="/analytics/scrap" element={<RequireFeature feature="scrap"><Scrap /></RequireFeature>} />
            <Route path="/analytics/shifts" element={<RequireFeature feature="shifts"><Shifts /></RequireFeature>} />
            <Route path="/analytics/spc" element={<RequireFeature feature="spc"><Spc /></RequireFeature>} />
            <Route path="/datasets" element={<Datasets />} />
            <Route path="/datasets/:id" element={<DatasetDetail />} />
            <Route path="/sql-editor" element={<SqlEditor />} />
            <Route path="/simulation" element={<RequireFeature feature="simulation"><Simulation /></RequireFeature>} />
            <Route path="/ask" element={<RequireFeature feature="data_chat"><Ask /></RequireFeature>} />
            <Route path="/bi" element={<BiDashboards />} />
            <Route path="/bi/new" element={<ChartBuilder />} />
            <Route path="/bi/:id" element={<BiDashboardDetail />} />
            <Route path="/workflows" element={<Workflows />} />
            <Route path="/workflows/:id" element={<WorkflowBuilder />} />
            <Route path="/runs" element={<RunHistory />} />
            <Route path="/report-builder" element={<RequireFeature feature="reports"><ReportBuilder /></RequireFeature>} />
            <Route path="/automl" element={<RequireFeature feature="automl"><TemplatesHome /></RequireFeature>} />
            <Route path="/automl/runs/:id" element={<RequireFeature feature="automl"><AutoMLTemplateResultsView /></RequireFeature>} />
            <Route path="/automl/advanced" element={<RequireFeature feature="automl"><AutoML /></RequireFeature>} />
            <Route path="/automl/advanced/new" element={<RequireFeature feature="automl"><AutoMLRun /></RequireFeature>} />
            <Route path="/automl/advanced/:id" element={<RequireFeature feature="automl"><AutoMLRun /></RequireFeature>} />
            <Route path="/kpis" element={<RequireFeature feature="kpis"><Kpis /></RequireFeature>} />
            <Route path="/kpis/dashboards" element={<RequireFeature feature="kpi_dashboards"><KpiDashboards /></RequireFeature>} />
            <Route path="/integrations" element={<Integrations />} />
            <Route path="/realtime" element={<Realtime />} />
            <Route path="/rca-cases" element={<RequireFeature feature="rca_cases"><RcaCases /></RequireFeature>} />
            <Route path="/predictive" element={<RequireFeature feature="predictive"><Predictive /></RequireFeature>} />
            <Route path="/mfg-forecast" element={<RequireFeature feature="mfg_forecast"><MfgForecast /></RequireFeature>} />
            <Route path="/computer-vision" element={<RequireFeature feature="computer_vision"><ComputerVision /></RequireFeature>} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/admin/customers" element={<RequireStaff><Customers /></RequireStaff>} />
          </Route>
        </Routes>
      </Suspense>
    </RouteErrorBoundary>
  );
}
