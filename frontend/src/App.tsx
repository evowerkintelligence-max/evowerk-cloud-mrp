import { Navigate, Route, Routes } from "react-router-dom";
import { useAuthStore } from "./store/authStore";
import { AppShell } from "./components/AppShell";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";
import { MfaChallenge } from "./pages/MfaChallenge";
import { Dashboard } from "./pages/Dashboard";
import { Items } from "./pages/Items";
import { Boms } from "./pages/Boms";
import { Suppliers } from "./pages/Suppliers";
import { PurchaseOrders } from "./pages/PurchaseOrders";
import { Lots } from "./pages/Lots";
import { Inventory } from "./pages/Inventory";
import { Locations } from "./pages/Locations";
import { Planning } from "./pages/Planning";
import { Datasets } from "./pages/analytics/Datasets";
import { DatasetDetail } from "./pages/analytics/DatasetDetail";
import { Oee } from "./pages/analytics/Oee";
import { Downtime } from "./pages/analytics/Downtime";
import { Throughput } from "./pages/analytics/Throughput";
import { Scrap } from "./pages/analytics/Scrap";
import { Shifts } from "./pages/analytics/Shifts";
import { Spc } from "./pages/analytics/Spc";
import { Ask } from "./pages/Ask";
import { Reports } from "./pages/Reports";

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
        <Route path="/suppliers" element={<Suppliers />} />
        <Route path="/purchase-orders" element={<PurchaseOrders />} />
        <Route path="/lots" element={<Lots />} />
        <Route path="/inventory" element={<Inventory />} />
        <Route path="/locations" element={<Locations />} />
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
        <Route path="/ask" element={<Ask />} />
        <Route path="/reports" element={<Reports />} />
      </Route>
    </Routes>
  );
}
