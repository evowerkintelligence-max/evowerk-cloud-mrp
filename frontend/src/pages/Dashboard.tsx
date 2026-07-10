import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiFetch } from "../api/client";
import { useAuthStore } from "../store/authStore";

interface OrgResponse {
  id: string;
  name: string;
}

export function Dashboard() {
  const clear = useAuthStore((s) => s.clear);
  const refreshToken = useAuthStore((s) => s.refreshToken);

  const org = useQuery<OrgResponse>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load org");
      return res.json();
    },
  });

  async function logout() {
    if (refreshToken) {
      await apiFetch("/auth/logout", {
        method: "POST",
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
    }
    clear();
    window.location.href = "/login";
  }

  return (
    <div>
      <h1>Welcome{org.data ? `, ${org.data.name}` : ""}</h1>
      <p>
        <Link to="/mfa">Set up two-factor authentication</Link>
      </p>
      <p>
        <Link to="/items">Items</Link> · <Link to="/boms">Bills of Materials</Link> ·{" "}
        <Link to="/suppliers">Suppliers</Link> · <Link to="/purchase-orders">Purchase Orders</Link> ·{" "}
        <Link to="/lots">Lots</Link> · <Link to="/inventory">Inventory</Link> ·{" "}
        <Link to="/locations">Locations</Link> · <Link to="/planning">Planning</Link> ·{" "}
        <Link to="/datasets">Datasets</Link>
      </p>
      <button onClick={logout}>Log out</button>
    </div>
  );
}
