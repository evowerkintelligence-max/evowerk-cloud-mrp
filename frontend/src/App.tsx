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
import { Predictive } from "./pages/Predictive";
import { MfgForecast } from "./pages/MfgForecast";
import { Scenarios } from "./pages/Scenarios";
import { ComputerVision } from "./pages/ComputerVision";

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
      </Route>
    </Routes>
  );
}
