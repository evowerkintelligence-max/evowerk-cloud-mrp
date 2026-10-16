import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import type { ItemResponse, ItemUnitResponse, BomResponse, BomLineResponse, BomDetailResponse } from "./types";
import { MAX_LINE_QUANTITY, MAX_SCRAP_PCT } from "./types";

// State, queries, mutations and handlers for the Boms page - the page itself is pure render.
export function useBomEditor() {
  const queryClient = useQueryClient();
  const [selectedBomId, setSelectedBomId] = useState<string | null>(null);
  const [newBomItemId, setNewBomItemId] = useState("");
  const [lineComponentId, setLineComponentId] = useState("");
  const [lineQuantity, setLineQuantity] = useState("1");
  const [lineScrapPct, setLineScrapPct] = useState("0");
  const [lineUnitId, setLineUnitId] = useState("");
  const [lineUnitQuantity, setLineUnitQuantity] = useState("");
  const [showNewUnit, setShowNewUnit] = useState(false);
  const [newUnitCode, setNewUnitCode] = useState("");
  const [newUnitFactor, setNewUnitFactor] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showBulk, setShowBulk] = useState(false);
  const [bulkText, setBulkText] = useState("");

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  const componentUnits = useQuery<ItemUnitResponse[]>({
    queryKey: ["item-units", lineComponentId],
    enabled: !!lineComponentId && lineComponentId !== "__new__",
    queryFn: async () => {
      const res = await apiFetch(`/items/${lineComponentId}/units`);
      if (!res.ok) throw new Error("Failed to load units");
      return res.json();
    },
  });

  const createUnit = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/items/${lineComponentId}/units`, {
        method: "POST",
        body: JSON.stringify({ code: newUnitCode, factor_to_base: newUnitFactor }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create unit");
      }
      return res.json() as Promise<ItemUnitResponse>;
    },
    onSuccess: (unit) => {
      queryClient.invalidateQueries({ queryKey: ["item-units", lineComponentId] });
      setLineUnitId(unit.id);
      setShowNewUnit(false);
      setNewUnitCode("");
      setNewUnitFactor("");
    },
    onError: (e: Error) => setError(e.message),
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

  function patchLines(
    lines: {
      component_item_id: string;
      quantity: string;
      sequence: number;
      scrap_pct: string;
      unit_id: string | null;
      unit_quantity: string | null;
    }[],
  ) {
    if (!bomDetail.data) throw new Error("No BOM selected");
    return apiFetch(`/boms/${selectedBomId}`, {
      method: "PATCH",
      body: JSON.stringify({ lines, expected_updated_at: bomDetail.data.updated_at }),
    });
  }

  const addLine = useMutation({
    mutationFn: async () => {
      if (!bomDetail.data) throw new Error("No BOM selected");
      const existingLines = bomDetail.data.lines.map((l) => ({
        component_item_id: l.component_item_id,
        quantity: l.quantity,
        scrap_pct: l.scrap_pct,
        unit_id: l.unit_id,
        unit_quantity: l.unit_quantity,
      }));
      // Renumber contiguously (not just append at existingLines.length) - a
      // prior remove can leave gaps, and `sequence` has no uniqueness guard
      // server-side, so appending at a stale length can collide.
      const merged = [
        ...existingLines,
        {
          component_item_id: lineComponentId,
          quantity: lineUnitId ? "0" : lineQuantity,
          scrap_pct: lineScrapPct || "0",
          unit_id: lineUnitId || null,
          unit_quantity: lineUnitId ? lineUnitQuantity : null,
        },
      ].map((l, i) => ({ ...l, sequence: i }));
      const res = await patchLines(merged);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update lines");
      }
      return res.json();
    },
    onSuccess: () => {
      setLineComponentId("");
      setLineQuantity("1");
      setLineScrapPct("0");
      setLineUnitId("");
      setLineUnitQuantity("");
      queryClient.invalidateQueries({ queryKey: ["boms", selectedBomId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const removeLine = useMutation({
    mutationFn: async (lineId: string) => {
      if (!bomDetail.data) throw new Error("No BOM selected");
      const remaining = bomDetail.data.lines
        .filter((l) => l.id !== lineId)
        .map((l, i) => ({
          component_item_id: l.component_item_id,
          quantity: l.quantity,
          scrap_pct: l.scrap_pct,
          unit_id: l.unit_id,
          unit_quantity: l.unit_quantity,
          sequence: i,
        }));
      if (remaining.length === 0) throw new Error("A BOM needs at least one line - delete the draft instead");
      const res = await patchLines(remaining);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to remove line");
      }
      return res.json();
    },
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["boms", selectedBomId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const reorderLines = useMutation({
    mutationFn: async (lines: BomLineResponse[]) => {
      const res = await patchLines(
        lines.map((l, i) => ({
          component_item_id: l.component_item_id,
          quantity: l.quantity,
          scrap_pct: l.scrap_pct,
          unit_id: l.unit_id,
          unit_quantity: l.unit_quantity,
          sequence: i,
        })),
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to reorder lines");
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["boms", selectedBomId] }),
    onError: (e: Error) => setError(e.message),
  });

  function onLineDrop(draggedId: string, targetId: string) {
    if (!bomDetail.data || draggedId === targetId) return;
    const lines = [...bomDetail.data.lines];
    const fromIndex = lines.findIndex((l) => l.id === draggedId);
    const toIndex = lines.findIndex((l) => l.id === targetId);
    if (fromIndex === -1 || toIndex === -1) return;
    const [moved] = lines.splice(fromIndex, 1);
    lines.splice(toIndex, 0, moved);
    setError(null);
    reorderLines.mutate(lines);
  }

  function onMoveLine(lineId: string, direction: "up" | "down") {
    if (!bomDetail.data) return;
    const lines = [...bomDetail.data.lines];
    const index = lines.findIndex((l) => l.id === lineId);
    const swapWith = direction === "up" ? index - 1 : index + 1;
    if (index === -1 || swapWith < 0 || swapWith >= lines.length) return;
    [lines[index], lines[swapWith]] = [lines[swapWith], lines[index]];
    setError(null);
    reorderLines.mutate(lines);
  }

  const bulkAddLines = useMutation({
    mutationFn: async () => {
      if (!bomDetail.data) throw new Error("No BOM selected");
      const bySku = new Map((items.data ?? []).map((i) => [i.sku.toLowerCase(), i]));
      const parsed: {
        component_item_id: string;
        quantity: string;
        scrap_pct: string;
        unit_id: string | null;
        unit_quantity: string | null;
        sequence: number;
      }[] = [];
      const notFound: string[] = [];
      const badQuantity: string[] = [];
      const badScrapPct: string[] = [];
      // Reject non-numeric/non-positive quantities here rather than letting
      // FastAPI's 422 validation error reach the catch below - that error's
      // `detail` is a list of objects, not a string, so surfacing it via
      // `body.detail` would show the user a useless "[object Object]".
      for (const row of bulkText.split("\n").map((r) => r.trim()).filter(Boolean)) {
        const [skuRaw, qtyRaw, scrapRaw] = row.split(/[,\t]/).map((v) => v?.trim());
        const item = skuRaw ? bySku.get(skuRaw.toLowerCase()) : undefined;
        if (!item) {
          notFound.push(skuRaw || row);
          continue;
        }
        const quantity = qtyRaw || "1";
        const quantityNum = Number(quantity);
        if (!/^\d+(\.\d+)?$/.test(quantity) || quantityNum <= 0 || quantityNum > MAX_LINE_QUANTITY) {
          badQuantity.push(row);
          continue;
        }
        const scrapPct = scrapRaw || "0";
        const scrapPctNum = Number(scrapPct);
        if (!/^\d+(\.\d+)?$/.test(scrapPct) || scrapPctNum < 0 || scrapPctNum > MAX_SCRAP_PCT) {
          badScrapPct.push(row);
          continue;
        }
        parsed.push({
          component_item_id: item.id,
          quantity,
          scrap_pct: scrapPct,
          unit_id: null,
          unit_quantity: null,
          sequence: 0,
        });
      }
      if (notFound.length > 0) throw new Error(`SKU(s) not found: ${notFound.join(", ")}`);
      if (badQuantity.length > 0) {
        throw new Error(`Quantity must be a positive number - check: ${badQuantity.join(" | ")}`);
      }
      if (badScrapPct.length > 0) {
        throw new Error(`Scrap % must be between 0 and ${MAX_SCRAP_PCT} - check: ${badScrapPct.join(" | ")}`);
      }
      if (parsed.length === 0) throw new Error('Paste rows as "SKU, quantity" - one per line');

      const existingLines = bomDetail.data.lines.map((l) => ({
        component_item_id: l.component_item_id,
        quantity: l.quantity,
        scrap_pct: l.scrap_pct,
        unit_id: l.unit_id,
        unit_quantity: l.unit_quantity,
        sequence: l.sequence,
      }));
      const merged = [...existingLines, ...parsed].map((l, i) => ({ ...l, sequence: i }));
      const res = await patchLines(merged);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to import lines");
      }
      return res.json();
    },
    onSuccess: () => {
      setBulkText("");
      setShowBulk(false);
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

  const reviseBom = useMutation({
    mutationFn: async () => {
      if (!bomDetail.data) throw new Error("No BOM selected");
      const res = await apiFetch(`/boms/${bomDetail.data.id}/revise`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to revise BOM");
      }
      return res.json() as Promise<BomDetailResponse>;
    },
    onSuccess: (newDraft) => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["boms"] });
      setSelectedBomId(newDraft.id);
    },
    onError: (e: Error) => setError(e.message),
  });

  const deleteBom = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/boms/${selectedBomId}`, { method: "DELETE" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to delete BOM");
      }
    },
    onSuccess: () => {
      setError(null);
      setSelectedBomId(null);
      queryClient.invalidateQueries({ queryKey: ["boms"] });
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

  return {
    selectedBomId,
    setSelectedBomId,
    newBomItemId,
    setNewBomItemId,
    lineComponentId,
    setLineComponentId,
    lineQuantity,
    setLineQuantity,
    lineScrapPct,
    setLineScrapPct,
    lineUnitId,
    setLineUnitId,
    lineUnitQuantity,
    setLineUnitQuantity,
    showNewUnit,
    setShowNewUnit,
    newUnitCode,
    setNewUnitCode,
    newUnitFactor,
    setNewUnitFactor,
    error,
    setError,
    showBulk,
    setShowBulk,
    bulkText,
    setBulkText,
    items,
    componentUnits,
    createUnit,
    boms,
    bomDetail,
    createBom,
    addLine,
    removeLine,
    onLineDrop,
    onMoveLine,
    bulkAddLines,
    activateBom,
    reviseBom,
    deleteBom,
    onCreateBom,
    onAddLine,
  };
}
