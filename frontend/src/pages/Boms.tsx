import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

interface ItemResponse {
  id: string;
  sku: string;
  name: string;
  item_type: string;
  unit_of_measure: string;
}

interface BomResponse {
  id: string;
  item_id: string;
  item_sku: string;
  item_name: string;
  version: number;
  status: string;
}

interface BomLineResponse {
  id: string;
  component_item_id: string;
  component_sku: string;
  component_name: string;
  unit_of_measure: string;
  quantity: string;
  sequence: number;
}

interface BomDetailResponse extends BomResponse {
  lines: BomLineResponse[];
}

export function Boms() {
  const queryClient = useQueryClient();
  const [selectedBomId, setSelectedBomId] = useState<string | null>(null);
  const [newBomItemId, setNewBomItemId] = useState("");
  const [lineComponentId, setLineComponentId] = useState("");
  const [lineQuantity, setLineQuantity] = useState("1");
  const [error, setError] = useState<string | null>(null);

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  const boms = useQuery<BomResponse[]>({
    queryKey: ["boms"],
    queryFn: async () => {
      const res = await apiFetch("/boms");
      if (!res.ok) throw new Error("Failed to load BOMs");
      return res.json();
    },
  });

  const bomDetail = useQuery<BomDetailResponse>({
    queryKey: ["boms", selectedBomId],
    enabled: !!selectedBomId,
    queryFn: async () => {
      const res = await apiFetch(`/boms/${selectedBomId}`);
      if (!res.ok) throw new Error("Failed to load BOM");
      return res.json();
    },
  });

  const createBom = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/boms", {
        method: "POST",
        body: JSON.stringify({ item_id: newBomItemId }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create BOM");
      }
      return res.json() as Promise<BomResponse>;
    },
    onSuccess: (bom) => {
      queryClient.invalidateQueries({ queryKey: ["boms"] });
      setSelectedBomId(bom.id);
    },
    onError: (e: Error) => setError(e.message),
  });

  const addLine = useMutation({
    mutationFn: async () => {
      if (!bomDetail.data) throw new Error("No BOM selected");
      const existingLines = bomDetail.data.lines.map((l) => ({
        component_item_id: l.component_item_id,
        quantity: l.quantity,
        sequence: l.sequence,
      }));
      const res = await apiFetch(`/boms/${selectedBomId}`, {
        method: "PATCH",
        body: JSON.stringify([
          ...existingLines,
          { component_item_id: lineComponentId, quantity: lineQuantity, sequence: existingLines.length },
        ]),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update lines");
      }
      return res.json();
    },
    onSuccess: () => {
      setLineComponentId("");
      setLineQuantity("1");
      queryClient.invalidateQueries({ queryKey: ["boms", selectedBomId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const activateBom = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/boms/${selectedBomId}/activate`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to activate BOM");
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["boms"] });
      queryClient.invalidateQueries({ queryKey: ["boms", selectedBomId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onCreateBom(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createBom.mutate();
  }

  function onAddLine(e: FormEvent) {
    e.preventDefault();
    setError(null);
    addLine.mutate();
  }

  return (
    <div>
      <p>
        <Link to="/">Back to dashboard</Link> · <Link to="/items">Manage items</Link>
      </p>
      <h1>Bills of Materials</h1>

      <form onSubmit={onCreateBom}>
        <label>
          New BOM for item
          <select value={newBomItemId} onChange={(e) => setNewBomItemId(e.target.value)} required>
            <option value="" disabled>
              Select an item
            </option>
            {items.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.sku} - {item.name}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={createBom.isPending}>
          Create draft
        </button>
      </form>

      {error && <p role="alert">{error}</p>}

      <table>
        <thead>
          <tr>
            <th>Item</th>
            <th>Version</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {boms.data?.map((bom) => (
            <tr key={bom.id} onClick={() => setSelectedBomId(bom.id)} style={{ cursor: "pointer" }}>
              <td>
                {bom.item_sku} - {bom.item_name}
              </td>
              <td>v{bom.version}</td>
              <td>{bom.status}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {bomDetail.data && (
        <section>
          <h2>
            {bomDetail.data.item_sku} v{bomDetail.data.version} ({bomDetail.data.status})
          </h2>

          <table>
            <thead>
              <tr>
                <th>Component</th>
                <th>Quantity</th>
                <th>UoM</th>
              </tr>
            </thead>
            <tbody>
              {bomDetail.data.lines.map((line) => (
                <tr key={line.id}>
                  <td>
                    {line.component_sku} - {line.component_name}
                  </td>
                  <td>{line.quantity}</td>
                  <td>{line.unit_of_measure}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {bomDetail.data.status === "draft" && (
            <>
              <form onSubmit={onAddLine}>
                <label>
                  Component
                  <select
                    value={lineComponentId}
                    onChange={(e) => setLineComponentId(e.target.value)}
                    required
                  >
                    <option value="" disabled>
                      Select a component
                    </option>
                    {items.data
                      ?.filter((item) => item.id !== bomDetail.data!.item_id)
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.sku} - {item.name}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  Quantity
                  <input
                    type="number"
                    step="0.0001"
                    min="0.0001"
                    value={lineQuantity}
                    onChange={(e) => setLineQuantity(e.target.value)}
                    required
                  />
                </label>
                <button type="submit" disabled={addLine.isPending}>
                  Add line
                </button>
              </form>

              <button onClick={() => activateBom.mutate()} disabled={activateBom.isPending}>
                Activate
              </button>
            </>
          )}
        </section>
      )}
    </div>
  );
}
