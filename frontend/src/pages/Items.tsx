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
  is_lot_tracked: boolean;
  preferred_supplier_id: string | null;
}

interface SupplierResponse {
  id: string;
  name: string;
}

export function Items() {
  const queryClient = useQueryClient();
  const [sku, setSku] = useState("");
  const [name, setName] = useState("");
  const [itemType, setItemType] = useState("component");
  const [unitOfMeasure, setUnitOfMeasure] = useState("ea");
  const [isLotTracked, setIsLotTracked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  const suppliers = useQuery<SupplierResponse[]>({
    queryKey: ["suppliers"],
    queryFn: async () => {
      const res = await apiFetch("/suppliers");
      if (!res.ok) throw new Error("Failed to load suppliers");
      return res.json();
    },
  });

  const setPreferredSupplier = useMutation({
    mutationFn: async ({ itemId, supplierId }: { itemId: string; supplierId: string | null }) => {
      const res = await apiFetch(`/items/${itemId}`, {
        method: "PATCH",
        body: JSON.stringify({ preferred_supplier_id: supplierId }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update preferred supplier");
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["items"] }),
    onError: (e: Error) => setError(e.message),
  });

  const createItem = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/items", {
        method: "POST",
        body: JSON.stringify({
          sku,
          name,
          item_type: itemType,
          unit_of_measure: unitOfMeasure,
          is_lot_tracked: isLotTracked,
        }),
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
        <label>
          <input
            type="checkbox"
            checked={isLotTracked}
            onChange={(e) => setIsLotTracked(e.target.checked)}
          />
          Lot tracked
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
            <th>Lot tracked</th>
            <th>Preferred supplier</th>
          </tr>
        </thead>
        <tbody>
          {items.data?.map((item) => (
            <tr key={item.id}>
              <td>{item.sku}</td>
              <td>{item.name}</td>
              <td>{item.item_type}</td>
              <td>{item.unit_of_measure}</td>
              <td>{item.is_lot_tracked ? "Yes" : "No"}</td>
              <td>
                <select
                  value={item.preferred_supplier_id ?? ""}
                  disabled={setPreferredSupplier.isPending}
                  onChange={(e) =>
                    setPreferredSupplier.mutate({
                      itemId: item.id,
                      supplierId: e.target.value || null,
                    })
                  }
                >
                  <option value="">None</option>
                  {suppliers.data?.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
