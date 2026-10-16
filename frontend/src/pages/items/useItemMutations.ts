import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";

// Per-field inline-edit PATCH mutations for the Items table, each with its own in-flight id set.
export function useItemMutations(setError: (message: string | null) => void) {
  const queryClient = useQueryClient();
  // Tracked separately from setPreferredSupplier.isPending/.variables - those
  // reflect only the single most-recently-invoked call, so editing item B
  // while item A's PATCH is still in flight would incorrectly re-enable A's
  // control. A Set of in-flight ids handles overlapping calls correctly.
  const [pendingSupplierItemIds, setPendingSupplierItemIds] = useState<Set<string>>(new Set());
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
    onMutate: ({ itemId }) => setPendingSupplierItemIds((prev) => new Set(prev).add(itemId)),
    onSettled: (_data, _err, { itemId }) =>
      setPendingSupplierItemIds((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["items"] }),
    onError: (e: Error) => setError(e.message),
  });

  const [pendingLeadTimeItemIds, setPendingLeadTimeItemIds] = useState<Set<string>>(new Set());
  const setLeadTime = useMutation({
    mutationFn: async ({
      itemId,
      preferredSupplierId,
      leadTimeDays: value,
    }: {
      itemId: string;
      preferredSupplierId: string | null;
      leadTimeDays: number | null;
    }) => {
      const res = await apiFetch(`/items/${itemId}`, {
        method: "PATCH",
        body: JSON.stringify({ preferred_supplier_id: preferredSupplierId, lead_time_days: value }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update lead time");
      }
      return res.json();
    },
    onMutate: ({ itemId }) => setPendingLeadTimeItemIds((prev) => new Set(prev).add(itemId)),
    onSettled: (_data, _err, { itemId }) =>
      setPendingLeadTimeItemIds((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["items"] }),
    onError: (e: Error) => setError(e.message),
  });

  const [pendingMoqItemIds, setPendingMoqItemIds] = useState<Set<string>>(new Set());
  const setMoq = useMutation({
    mutationFn: async ({
      itemId,
      preferredSupplierId,
      minOrderQty: value,
    }: {
      itemId: string;
      preferredSupplierId: string | null;
      minOrderQty: string | null;
    }) => {
      const res = await apiFetch(`/items/${itemId}`, {
        method: "PATCH",
        body: JSON.stringify({ preferred_supplier_id: preferredSupplierId, min_order_qty: value }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update MOQ");
      }
      return res.json();
    },
    onMutate: ({ itemId }) => setPendingMoqItemIds((prev) => new Set(prev).add(itemId)),
    onSettled: (_data, _err, { itemId }) =>
      setPendingMoqItemIds((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["items"] }),
    onError: (e: Error) => setError(e.message),
  });

  const [pendingSafetyStockItemIds, setPendingSafetyStockItemIds] = useState<Set<string>>(new Set());
  const setSafetyStock = useMutation({
    mutationFn: async ({
      itemId,
      preferredSupplierId,
      safetyStockQty: value,
    }: {
      itemId: string;
      preferredSupplierId: string | null;
      safetyStockQty: string | null;
    }) => {
      const res = await apiFetch(`/items/${itemId}`, {
        method: "PATCH",
        body: JSON.stringify({ preferred_supplier_id: preferredSupplierId, safety_stock_qty: value }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update safety stock");
      }
      return res.json();
    },
    onMutate: ({ itemId }) => setPendingSafetyStockItemIds((prev) => new Set(prev).add(itemId)),
    onSettled: (_data, _err, { itemId }) =>
      setPendingSafetyStockItemIds((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["items"] }),
    onError: (e: Error) => setError(e.message),
  });

  const [pendingActiveItemIds, setPendingActiveItemIds] = useState<Set<string>>(new Set());
  const setActive = useMutation({
    mutationFn: async ({
      itemId,
      preferredSupplierId,
      isActive,
    }: {
      itemId: string;
      preferredSupplierId: string | null;
      isActive: boolean;
    }) => {
      const res = await apiFetch(`/items/${itemId}`, {
        method: "PATCH",
        body: JSON.stringify({ preferred_supplier_id: preferredSupplierId, is_active: isActive }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update status");
      }
      return res.json();
    },
    onMutate: ({ itemId }) => setPendingActiveItemIds((prev) => new Set(prev).add(itemId)),
    onSettled: (_data, _err, { itemId }) =>
      setPendingActiveItemIds((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["items"] }),
    onError: (e: Error) => setError(e.message),
  });

  // Shared by the four plain-text fields (name, unit_of_measure, description,
  // barcode) - same PATCH shape for each, only the field name differs.
  const [pendingFieldItemIds, setPendingFieldItemIds] = useState<Set<string>>(new Set());
  const patchItemField = useMutation({
    mutationFn: async ({
      itemId,
      preferredSupplierId,
      field,
      value,
    }: {
      itemId: string;
      preferredSupplierId: string | null;
      field: "name" | "unit_of_measure" | "description" | "barcode";
      value: string | null;
    }) => {
      const res = await apiFetch(`/items/${itemId}`, {
        method: "PATCH",
        body: JSON.stringify({ preferred_supplier_id: preferredSupplierId, [field]: value }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update item");
      }
      return res.json();
    },
    onMutate: ({ itemId }) => setPendingFieldItemIds((prev) => new Set(prev).add(itemId)),
    onSettled: (_data, _err, { itemId }) =>
      setPendingFieldItemIds((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["items"] }),
    onError: (e: Error) => setError(e.message),
  });

  return {
    pendingSupplierItemIds,
    setPreferredSupplier,
    pendingLeadTimeItemIds,
    setLeadTime,
    pendingMoqItemIds,
    setMoq,
    pendingSafetyStockItemIds,
    setSafetyStock,
    pendingActiveItemIds,
    setActive,
    pendingFieldItemIds,
    patchItemField,
  };
}
