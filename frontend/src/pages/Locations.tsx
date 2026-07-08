import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

interface LocationResponse {
  id: string;
  code: string;
  name: string;
  is_default: boolean;
}

export function Locations() {
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const locations = useQuery<LocationResponse[]>({
    queryKey: ["locations"],
    queryFn: async () => {
      const res = await apiFetch("/locations");
      if (!res.ok) throw new Error("Failed to load locations");
      return res.json();
    },
  });

  const createLocation = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/locations", {
        method: "POST",
        body: JSON.stringify({ code, name }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create location");
      }
      return res.json();
    },
    onSuccess: () => {
      setCode("");
      setName("");
      queryClient.invalidateQueries({ queryKey: ["locations"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createLocation.mutate();
  }

  return (
    <div>
      <p>
        <Link to="/">Back to dashboard</Link> · <Link to="/inventory">Inventory</Link>
      </p>
      <h1>Locations</h1>
      <p>Warehouses and storage locations that hold on-hand stock.</p>

      {error && <p role="alert">{error}</p>}

      <form onSubmit={onSubmit}>
        <label>
          Code
          <input value={code} onChange={(e) => setCode(e.target.value)} required />
        </label>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <button type="submit" disabled={createLocation.isPending}>
          Add location
        </button>
      </form>

      <table>
        <thead>
          <tr>
            <th>Code</th>
            <th>Name</th>
            <th>Default</th>
          </tr>
        </thead>
        <tbody>
          {locations.data?.map((loc) => (
            <tr key={loc.id}>
              <td>{loc.code}</td>
              <td>{loc.name}</td>
              <td>{loc.is_default ? "Yes" : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
