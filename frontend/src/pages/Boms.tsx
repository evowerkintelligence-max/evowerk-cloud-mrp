import { FormEvent, useState } from "react";
import {
  Archive,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  FilePen,
  GripVertical,
  Layers,
  Trash2,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { ItemSelect } from "@/components/ItemSelect";
import { humanize } from "@/lib/status";
import { cn } from "@/lib/utils";

interface ItemResponse {
  id: string;
  sku: string;
  name: string;
  item_type: string;
  unit_of_measure: string;
}

interface ItemUnitResponse {
  id: string;
  item_id: string;
  code: string;
  factor_to_base: string;
}

interface BomResponse {
  id: string;
  item_id: string;
  item_sku: string;
  item_name: string;
  version: number;
  status: string;
  updated_at: string;
}

interface BomLineResponse {
  id: string;
  component_item_id: string;
  component_sku: string;
  component_name: string;
  unit_of_measure: string;
  quantity: string;
  sequence: number;
  scrap_pct: string;
  unit_id: string | null;
  unit_quantity: string | null;
  unit_code: string | null;
}

interface BomDetailResponse extends BomResponse {
  lines: BomLineResponse[];
}

// BomLine.quantity is NUMERIC(12,4) server-side (8 integer digits max) -
// mirrored here so a typo gets instant browser feedback instead of a round
// trip to the 400 the backend now also enforces.
const MAX_LINE_QUANTITY = 99999999.9999;
const MAX_SCRAP_PCT = 100;

function bomStatusVariant(status: string) {
  if (status === "active") return "success" as const;
  if (status === "archived") return "neutral" as const;
  return "warning" as const; // draft
}

// Recursively explodes a line into its component's own active BOM, if it has
// one - lazily fetched only once expanded, reusing the same GET /boms/{id}
// the top-level detail view already uses.
function BomLineRow({
  line,
  depth,
  allBoms,
  onRemove,
  removePending,
  draggable,
  onDropLine,
  onMove,
  canMoveUp,
  canMoveDown,
}: {
  line: BomLineResponse;
  depth: number;
  allBoms: BomResponse[];
  onRemove?: (lineId: string) => void;
  removePending?: boolean;
  draggable?: boolean;
  onDropLine?: (draggedId: string, targetId: string) => void;
  onMove?: (lineId: string, direction: "up" | "down") => void;
  canMoveUp?: boolean;
  canMoveDown?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const queryClient = useQueryClient();
  const childBom = allBoms.find((b) => b.item_id === line.component_item_id && b.status === "active");

  function prefetchChild() {
    if (!childBom) return;
    // Warms the cache on hover so the click that follows usually finds it
    // already there instead of waiting on a fresh round trip.
    queryClient.prefetchQuery({
      queryKey: ["boms", childBom.id],
      queryFn: async () => {
        const res = await apiFetch(`/boms/${childBom.id}`);
        if (!res.ok) throw new Error("Failed to load BOM");
        return res.json();
      },
    });
  }

  const childDetail = useQuery<BomDetailResponse>({
    queryKey: ["boms", childBom?.id],
    enabled: expanded && !!childBom,
    queryFn: async () => {
      const res = await apiFetch(`/boms/${childBom!.id}`);
      if (!res.ok) throw new Error("Failed to load BOM");
      return res.json();
    },
  });

  return (
    <>
      <TR
        onDragOver={(e) => {
          if (draggable) e.preventDefault();
        }}
        onDrop={(e) => {
          if (!draggable) return;
          e.preventDefault();
          const draggedId = e.dataTransfer.getData("text/plain");
          onDropLine?.(draggedId, line.id);
        }}
      >
        {onRemove && (
          <TD
            className="w-6 cursor-move text-muted-foreground"
            draggable
            onDragStart={(e) => e.dataTransfer.setData("text/plain", line.id)}
            title="Drag to reorder"
          >
            <span className="inline-flex items-center gap-0.5">
              <GripVertical className="size-4" />
              {/* Keyboard/screen-reader alternative to the drag handle above -
                  native HTML5 drag-and-drop has no keyboard equivalent. */}
              <span className="flex flex-col">
                <button
                  type="button"
                  disabled={!canMoveUp}
                  onClick={() => onMove?.(line.id, "up")}
                  className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                  title="Move up"
                >
                  <ChevronUp className="size-3" />
                </button>
                <button
                  type="button"
                  disabled={!canMoveDown}
                  onClick={() => onMove?.(line.id, "down")}
                  className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                  title="Move down"
                >
                  <ChevronDown className="size-3" />
                </button>
              </span>
            </span>
          </TD>
        )}
        <TD className="font-medium">
          <span className="inline-flex items-center gap-1" style={{ paddingLeft: `${depth * 1.25}rem` }}>
            {childBom ? (
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                onMouseEnter={prefetchChild}
                className="text-muted-foreground hover:text-foreground"
                title={expanded ? "Collapse" : "Show this component's own BOM"}
              >
                {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
              </button>
            ) : (
              <span className="inline-block size-3.5" />
            )}
            {line.component_sku} - {line.component_name}
          </span>
        </TD>
        <TD>{line.quantity}</TD>
        <TD className="text-muted-foreground">{line.unit_of_measure}</TD>
        <TD className="text-muted-foreground">
          {line.unit_code ? `${line.unit_quantity} ${line.unit_code}` : "-"}
        </TD>
        <TD className="text-muted-foreground">{Number(line.scrap_pct) > 0 ? `${line.scrap_pct}%` : "-"}</TD>
        {onRemove && (
          <TD>
            <Button
              variant="ghost"
              size="icon"
              loading={removePending}
              onClick={() => onRemove(line.id)}
              title="Remove line"
            >
              <Trash2 className="size-4" />
            </Button>
          </TD>
        )}
      </TR>
      {expanded &&
        childBom &&
        childDetail.data?.lines.map((childLine) => (
          <BomLineRow key={childLine.id} line={childLine} depth={depth + 1} allBoms={allBoms} />
        ))}
    </>
  );
}

export function Boms() {
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

  return (
    <div>
      <PageHeader
        title="Bills of Materials"
        description="Define the components that go into a manufactured item, then activate a version to use it in planning."
      />

      {boms.data && boms.data.length > 0 && (
        <div className="mb-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[
            { label: "Total BOMs", value: boms.data.length, icon: <Layers />, tone: "primary" as const },
            {
              label: "Active",
              value: boms.data.filter((b) => b.status === "active").length,
              icon: <CheckCircle2 />,
              tone: "success" as const,
            },
            {
              label: "Draft",
              value: boms.data.filter((b) => b.status === "draft").length,
              icon: <FilePen />,
              tone: "warning" as const,
            },
            {
              label: "Archived",
              value: boms.data.filter((b) => b.status === "archived").length,
              icon: <Archive />,
              tone: "terracotta" as const,
            },
          ].map((s) => (
            <StatCard key={s.label} label={s.label} value={s.value} icon={s.icon} tone={s.tone} />
          ))}
        </div>
      )}

      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Create a draft BOM</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onCreateBom} className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
            <Field label="For item" htmlFor="bom-item">
              <ItemSelect
                id="bom-item"
                items={items.data}
                value={newBomItemId}
                onChange={setNewBomItemId}
                required
              />
            </Field>
            <Button type="submit" loading={createBom.isPending}>
              Create draft
            </Button>
          </form>
        </CardContent>
      </Card>

      {boms.data && boms.data.length === 0 ? (
        <EmptyState icon={<Layers />} title="No BOMs yet" description="Create a draft to get started." />
      ) : (
        <TableWrap className="mb-8">
          <Table>
            <THead>
              <TR>
                <TH>Item</TH>
                <TH>Version</TH>
                <TH>Status</TH>
              </TR>
            </THead>
            <TBody>
              {boms.data?.map((bom) => (
                <TR
                  key={bom.id}
                  onClick={() => setSelectedBomId(bom.id)}
                  className={cn("cursor-pointer", selectedBomId === bom.id && "bg-primary/5")}
                >
                  <TD className="font-medium">
                    {bom.item_sku} - {bom.item_name}
                  </TD>
                  <TD className="text-muted-foreground">v{bom.version}</TD>
                  <TD>
                    <Badge variant={bomStatusVariant(bom.status)}>{humanize(bom.status)}</Badge>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}

      {bomDetail.data && (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              {bomDetail.data.item_sku} · v{bomDetail.data.version}
              <Badge variant={bomStatusVariant(bomDetail.data.status)}>
                {humanize(bomDetail.data.status)}
              </Badge>
            </CardTitle>
            {bomDetail.data.status === "draft" && (
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-rose-400 hover:text-rose-300"
                  loading={deleteBom.isPending}
                  onClick={() => {
                    if (confirm(`Delete this draft BOM for ${bomDetail.data!.item_sku}? This cannot be undone.`)) {
                      deleteBom.mutate();
                    }
                  }}
                >
                  <Trash2 className="size-4" /> Delete
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => activateBom.mutate()}
                  loading={activateBom.isPending}
                >
                  Activate
                </Button>
              </div>
            )}
            {bomDetail.data.status === "active" && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => reviseBom.mutate()}
                loading={reviseBom.isPending}
              >
                Edit (new version)
              </Button>
            )}
          </CardHeader>
          <CardContent className="space-y-6">
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    {bomDetail.data.status === "draft" && <TH />}
                    <TH>Component</TH>
                    <TH>Quantity</TH>
                    <TH>UoM</TH>
                    <TH>Unit</TH>
                    <TH>Scrap %</TH>
                    {bomDetail.data.status === "draft" && <TH />}
                  </TR>
                </THead>
                <TBody>
                  {bomDetail.data.lines.map((line, index) => (
                    <BomLineRow
                      key={line.id}
                      line={line}
                      depth={0}
                      allBoms={boms.data ?? []}
                      onRemove={bomDetail.data!.status === "draft" ? (id) => removeLine.mutate(id) : undefined}
                      removePending={removeLine.isPending}
                      draggable={bomDetail.data!.status === "draft"}
                      onDropLine={onLineDrop}
                      onMove={onMoveLine}
                      canMoveUp={index > 0}
                      canMoveDown={index < bomDetail.data!.lines.length - 1}
                    />
                  ))}
                </TBody>
              </Table>
            </TableWrap>

            {bomDetail.data.status === "draft" && (
              <>
                <form
                  onSubmit={onAddLine}
                  className="grid gap-4 sm:grid-cols-[1fr_140px_140px_160px_auto] sm:items-end"
                >
                  <Field label="Component" htmlFor="component">
                    <ItemSelect
                      id="component"
                      items={items.data}
                      value={lineComponentId}
                      onChange={(id) => {
                        setLineComponentId(id);
                        setLineUnitId("");
                        setLineUnitQuantity("");
                        setShowNewUnit(false);
                        setNewUnitCode("");
                        setNewUnitFactor("");
                      }}
                      excludeId={bomDetail.data.item_id}
                      required
                    />
                  </Field>
                  <Field label={lineUnitId ? "Quantity (in unit)" : "Quantity"} htmlFor="qty">
                    <Input
                      id="qty"
                      type="number"
                      step="0.0001"
                      min="0.0001"
                      max={lineUnitId ? undefined : MAX_LINE_QUANTITY}
                      value={lineUnitId ? lineUnitQuantity : lineQuantity}
                      onChange={(e) =>
                        lineUnitId ? setLineUnitQuantity(e.target.value) : setLineQuantity(e.target.value)
                      }
                      required
                    />
                  </Field>
                  <Field label="Scrap %" htmlFor="scrap-pct">
                    <Input
                      id="scrap-pct"
                      type="number"
                      step="0.01"
                      min="0"
                      max={MAX_SCRAP_PCT}
                      value={lineScrapPct}
                      onChange={(e) => setLineScrapPct(e.target.value)}
                      title="Expected material loss for this component - MRP orders this much extra"
                    />
                  </Field>
                  <Field label="Unit" htmlFor="line-unit">
                    {showNewUnit ? (
                      <div className="flex gap-1">
                        <Input
                          placeholder="code"
                          value={newUnitCode}
                          onChange={(e) => setNewUnitCode(e.target.value)}
                        />
                        <Input
                          type="number"
                          step="0.000001"
                          min="0.000001"
                          placeholder="factor"
                          value={newUnitFactor}
                          onChange={(e) => setNewUnitFactor(e.target.value)}
                        />
                        <Button
                          type="button"
                          size="icon"
                          variant="secondary"
                          disabled={!newUnitCode || !newUnitFactor}
                          loading={createUnit.isPending}
                          onClick={() => createUnit.mutate()}
                        >
                          <Layers className="size-4" />
                        </Button>
                      </div>
                    ) : (
                      <select
                        id="line-unit"
                        className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                        disabled={!lineComponentId}
                        value={lineUnitId}
                        onChange={(e) => {
                          if (e.target.value === "__new__") {
                            setShowNewUnit(true);
                            setLineUnitId("");
                          } else {
                            setLineUnitId(e.target.value);
                          }
                        }}
                      >
                        <option value="">base unit</option>
                        {componentUnits.data?.map((u) => (
                          <option key={u.id} value={u.id}>
                            {u.code}
                          </option>
                        ))}
                        <option value="__new__">+ New unit...</option>
                      </select>
                    )}
                  </Field>
                  <Button type="submit" variant="secondary" loading={addLine.isPending}>
                    Add line
                  </Button>
                </form>

                <div>
                  <Button type="button" variant="ghost" size="sm" onClick={() => setShowBulk((v) => !v)}>
                    {showBulk ? "Hide" : "Paste from spreadsheet"}
                  </Button>
                  {showBulk && (
                    <div className="mt-2 space-y-2">
                      <textarea
                        className="w-full rounded-md border bg-background p-2 text-sm"
                        rows={4}
                        placeholder={
                          "SKU, quantity, scrap % (optional) - one per line\ne.g.\nWIDGET-1, 4\nWIDGET-2, 2, 5"
                        }
                        value={bulkText}
                        onChange={(e) => setBulkText(e.target.value)}
                      />
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        loading={bulkAddLines.isPending}
                        onClick={() => {
                          setError(null);
                          bulkAddLines.mutate();
                        }}
                      >
                        Import lines
                      </Button>
                    </div>
                  )}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
