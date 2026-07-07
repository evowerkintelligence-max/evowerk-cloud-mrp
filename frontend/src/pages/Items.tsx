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

export function Items() {
  const queryClient = useQueryClient();
  const [sku, setSku] = useState("");
  const [name, setName] = useState("");
  const [itemType, setItemType] = useState("component");
  const [unitOfMeasure, setUnitOfMeasure] = useState("ea");
  const [error, setError] = useState<string | null>(null);

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  const createItem = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/items", {
        method: "POST",
        body: JSON.stringify({ sku, name, item_type: itemType, unit_of_measure: unitOfMeasure }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create item");
      }
      return res.json();
    },
    onSuccess: () => {
      setSku("");
      setName("");
      queryClient.invalidateQueries({ queryKey: ["items"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createItem.mutate();
  }

  return (
    <div>
      <p>
        <Link to="/">Back to dashboard</Link>
      </p>
      <h1>Items</h1>

      <form onSubmit={onSubmit}>
        <label>
          SKU
          <input value={sku} onChange={(e) => setSku(e.target.value)} required />
        </label>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label>
          Type
          <select value={itemType} onChange={(e) => setItemType(e.target.value)}>
            <option value="raw_material">Raw material</option>
            <option value="component">Component</option>
            <option value="finished_good">Finished good</option>
          </select>
        </label>
        <label>
          Unit of measure
          <input value={unitOfMeasure} onChange={(e) => setUnitOfMeasure(e.target.value)} required />
        </label>
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={createItem.isPending}>
          Add item
        </button>
      </form>

      <table>
        <thead>
          <tr>
            <th>SKU</th>
            <th>Name</th>
            <th>Type</th>
            <th>UoM</th>
          </tr>
        </thead>
        <tbody>
          {items.data?.map((item) => (
            <tr key={item.id}>
              <td>{item.sku}</td>
              <td>{item.name}</td>
              <td>{item.item_type}</td>
              <td>{item.unit_of_measure}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
