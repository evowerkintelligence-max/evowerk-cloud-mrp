import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { Boxes, Layers, Package, PackageCheck } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { StatCard, STAT_TONES } from "@/components/StatCard";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TH, THead, TR, TableWrap } from "@/components/ui/table";
import type { DatasetResponse, ImportResult, ItemResponse, OrgMeResponse, SupplierResponse } from "./items/types";
import { ItemRow } from "./items/ItemRow";
import { useItemMutations } from "./items/useItemMutations";

export function Items() {
  const queryClient = useQueryClient();
  const [sku, setSku] = useState("");
  const [name, setName] = useState("");
  const [itemType, setItemType] = useState("component");
  const [unitOfMeasure, setUnitOfMeasure] = useState("ea");
  const [isLotTracked, setIsLotTracked] = useState(false);
  const [leadTimeDays, setLeadTimeDays] = useState("");
  const [minOrderQty, setMinOrderQty] = useState("");
  const [safetyStockQty, setSafetyStockQty] = useState("");
  const [description, setDescription] = useState("");
  const [barcode, setBarcode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [expandedHistoryIds, setExpandedHistoryIds] = useState<Set<string>>(new Set());
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [showManualAdd, setShowManualAdd] = useState(false);

  const org = useQuery<OrgMeResponse>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load organization");
      return res.json();
    },
  });
  const isOwner = org.data?.role === "owner";

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

  const datasets = useQuery<DatasetResponse[]>({
    queryKey: ["datasets"],
    queryFn: async () => {
      const res = await apiFetch("/datasets");
      if (!res.ok) throw new Error("Failed to load datasets");
      return res.json();
    },
  });

  const itemMutations = useItemMutations(setError);

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
          lead_time_days: leadTimeDays === "" ? null : Number(leadTimeDays),
          min_order_qty: minOrderQty === "" ? null : minOrderQty,
          safety_stock_qty: safetyStockQty === "" ? null : safetyStockQty,
          description: description === "" ? null : description,
          barcode: barcode === "" ? null : barcode,
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
      setLeadTimeDays("");
      setMinOrderQty("");
      setSafetyStockQty("");
      setDescription("");
      setBarcode("");
      setShowManualAdd(false);
      queryClient.invalidateQueries({ queryKey: ["items"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const importFromDataset = useMutation({
    mutationFn: async () => {
      if (!selectedDatasetId) throw new Error("Choose a dataset first");
      const res = await apiFetch("/items/import", {
        method: "POST",
        body: JSON.stringify({ dataset_id: selectedDatasetId }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to import from dataset");
      }
      return (await res.json()) as ImportResult;
    },
    onSuccess: (result) => {
      setImportResult(result);
      queryClient.invalidateQueries({ queryKey: ["items"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  async function exportCsv() {
    setError(null);
    try {
      const res = await apiFetch("/items/export");
      if (!res.ok) throw new Error("Failed to export items");
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = "items.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to export items");
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createItem.mutate();
  }

  function toggleHistory(itemId: string) {
    setExpandedHistoryIds((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  }

  return (
    <div>
      <PageHeader title="Items" description="Raw materials, components, and finished goods in your catalog." />

      {items.data && items.data.length > 0 && (
        <div className="mb-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[
            { label: "Total items", value: items.data.length, icon: <Package /> },
            {
              label: "Raw materials",
              value: items.data.filter((i) => i.item_type === "raw_material").length,
              icon: <Boxes />,
            },
            {
              label: "Components",
              value: items.data.filter((i) => i.item_type === "component").length,
              icon: <Layers />,
            },
            {
              label: "Finished goods",
              value: items.data.filter((i) => i.item_type === "finished_good").length,
              icon: <PackageCheck />,
            },
          ].map((s, i) => (
            <StatCard
              key={s.label}
              label={s.label}
              value={s.value}
              icon={s.icon}
              tone={STAT_TONES[i % STAT_TONES.length]}
            />
          ))}
        </div>
      )}

      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      {isOwner ? (
        <div className="mb-8">
          {!showManualAdd && (
            <Button type="button" variant="outline" size="sm" onClick={() => setShowManualAdd(true)}>
              + Add item manually
            </Button>
          )}
          {showManualAdd && (
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle>Add an item</CardTitle>
                <Button type="button" variant="ghost" size="sm" onClick={() => setShowManualAdd(false)}>
                  Cancel
                </Button>
              </CardHeader>
              <CardContent>
                <p className="mb-4 text-sm text-muted-foreground">
                  Prefer bulk changes? <Link to="/datasets" className="underline">Upload a CSV or Excel file</Link>{" "}
                  and import it below instead.
                </p>
                <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  <Field label="SKU" htmlFor="sku">
                    <Input id="sku" value={sku} onChange={(e) => setSku(e.target.value)} required />
                  </Field>
                  <Field label="Name" htmlFor="name">
                    <Input id="name" value={name} onChange={(e) => setName(e.target.value)} required />
                  </Field>
                  <Field label="Type" htmlFor="type">
                    <Select id="type" value={itemType} onChange={(e) => setItemType(e.target.value)}>
                      <option value="raw_material">Raw material</option>
                      <option value="component">Component</option>
                      <option value="finished_good">Finished good</option>
                    </Select>
                  </Field>
                  <Field label="Unit of measure" htmlFor="uom">
                    <Input
                      id="uom"
                      value={unitOfMeasure}
                      onChange={(e) => setUnitOfMeasure(e.target.value)}
                      required
                    />
                  </Field>
                  <Field label="Lead time (days)" htmlFor="leadTimeDays">
                    <Input
                      id="leadTimeDays"
                      type="number"
                      min={0}
                      value={leadTimeDays}
                      onChange={(e) => setLeadTimeDays(e.target.value)}
                    />
                  </Field>
                  <Field label="Min order qty" htmlFor="minOrderQty">
                    <Input
                      id="minOrderQty"
                      type="number"
                      min={0}
                      step="any"
                      value={minOrderQty}
                      onChange={(e) => setMinOrderQty(e.target.value)}
                    />
                  </Field>
                  <Field label="Safety stock qty" htmlFor="safetyStockQty">
                    <Input
                      id="safetyStockQty"
                      type="number"
                      min={0}
                      step="any"
                      value={safetyStockQty}
                      onChange={(e) => setSafetyStockQty(e.target.value)}
                    />
                  </Field>
                  <Field label="Description" htmlFor="description">
                    <Input
                      id="description"
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                    />
                  </Field>
                  <Field label="Barcode" htmlFor="barcode">
                    <Input id="barcode" value={barcode} onChange={(e) => setBarcode(e.target.value)} />
                  </Field>
                  <label className="flex items-center gap-2 self-end pb-2.5 text-sm">
                    <Checkbox
                      checked={isLotTracked}
                      onChange={(e) => setIsLotTracked(e.target.checked)}
                    />
                    Lot tracked
                  </label>
                  <div className="flex items-end sm:col-span-2 lg:col-span-1">
                    <Button type="submit" className="w-full" loading={createItem.isPending}>
                      Add item
                    </Button>
                  </div>
                </form>
              </CardContent>
            </Card>
          )}
        </div>
      ) : (
        <Alert className="mb-8">Only organization owners can add or edit items. You can still view the catalog and export it below.</Alert>
      )}

      {items.data && items.data.length === 0 ? (
        <EmptyState
          icon={<Package />}
          title="No items yet"
          description="Import a dataset, or add your first SKU manually using the button above."
        />
      ) : (
        <>
          <Card className="mb-4 flex flex-wrap items-center gap-4 p-4">
            <Input
              placeholder="Search by SKU or name..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="max-w-xs"
            />
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
              Show inactive
            </label>
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <Button type="button" variant="outline" size="sm" onClick={exportCsv}>
                Export CSV
              </Button>
              {isOwner &&
                (datasets.data && datasets.data.length > 0 ? (
                  <>
                    <div className="w-48">
                      <Label className="sr-only">Dataset to import</Label>
                      <Select
                        value={selectedDatasetId}
                        onChange={(e) => setSelectedDatasetId(e.target.value)}
                      >
                        <option value="">Choose a dataset...</option>
                        {datasets.data.map((d) => (
                          <option key={d.id} value={d.id}>
                            {d.name}
                          </option>
                        ))}
                      </Select>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!selectedDatasetId}
                      loading={importFromDataset.isPending}
                      onClick={() => importFromDataset.mutate()}
                    >
                      Import from Dataset
                    </Button>
                  </>
                ) : (
                  <span className="text-sm text-muted-foreground">
                    No datasets yet -{" "}
                    <Link to="/datasets" className="underline">
                      upload a CSV or Excel file
                    </Link>{" "}
                    first, then import it here.
                  </span>
                ))}
            </div>
          </Card>

          {importResult && (
            <Alert className="mb-4">
              {importResult.created.length} item(s) imported
              {importResult.errors.length > 0 && `, ${importResult.errors.length} row(s) failed:`}
              {importResult.errors.length > 0 && (
                <ul className="mt-1 list-disc pl-5 text-sm">
                  {importResult.errors.map((e, i) => (
                    <li key={i}>
                      Row {e.row}
                      {e.sku ? ` (${e.sku})` : ""}: {e.message}
                    </li>
                  ))}
                </ul>
              )}
            </Alert>
          )}

          {(() => {
            const filtered = (items.data ?? []).filter((item) => {
              if (!showInactive && !item.is_active) return false;
              const q = search.trim().toLowerCase();
              if (!q) return true;
              return item.sku.toLowerCase().includes(q) || item.name.toLowerCase().includes(q);
            });

            if (filtered.length === 0) {
              return (
                <EmptyState
                  icon={<Package />}
                  title="No matching items"
                  description="Try a different search, or enable &ldquo;Show inactive&rdquo;."
                />
              );
            }

            return (
              <TableWrap>
                <Table>
                  <THead>
                    <TR>
                      <TH>SKU</TH>
                      <TH>Name</TH>
                      <TH>Type</TH>
                      <TH>UoM</TH>
                      <TH>Lot tracked</TH>
                      <TH>Lead time (days)</TH>
                      <TH>Min order qty</TH>
                      <TH>Safety stock qty</TH>
                      <TH>Description</TH>
                      <TH>Barcode</TH>
                      <TH>Status</TH>
                      <TH>Preferred supplier</TH>
                      <TH>History</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {filtered.map((item) => (
                      <ItemRow
                        key={item.id}
                        item={item}
                        isOwner={isOwner}
                        suppliers={suppliers.data}
                        mutations={itemMutations}
                        historyExpanded={expandedHistoryIds.has(item.id)}
                        onToggleHistory={toggleHistory}
                      />
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
            );
          })()}
        </>
      )}
    </div>
  );
}
