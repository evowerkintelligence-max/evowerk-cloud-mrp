import { Navigate, Route, Routes } from "react-router-dom";
import { useAuthStore } from "./store/authStore";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";
import { MfaChallenge } from "./pages/MfaChallenge";
import { Dashboard } from "./pages/Dashboard";
import { Items } from "./pages/Items";
import { Boms } from "./pages/Boms";

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
        path="/"
        element={
          <RequireAuth>
            <Dashboard />
          </RequireAuth>
        }
      />
      <Route
        path="/items"
        element={
          <RequireAuth>
            <Items />
          </RequireAuth>
        }
      />
      <Route
        path="/boms"
        element={
          <RequireAuth>
            <Boms />
          </RequireAuth>
        }
      />
    </Routes>
  );
}
