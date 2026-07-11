import { Fragment, FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { Package } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize, itemTypeVariant } from "@/lib/status";

interface ItemResponse {
  id: string;
  sku: string;
  name: string;
  item_type: string;
  unit_of_measure: string;
  is_lot_tracked: boolean;
  preferred_supplier_id: string | null;
  lead_time_days: number | null;
  is_active: boolean;
  description: string | null;
  barcode: string | null;
}

interface SupplierResponse {
  id: string;
  name: string;
}

interface DatasetResponse {
  id: string;
  name: string;
}

interface OrgMeResponse {
  id: string;
  name: string;
  role: string;
}

interface AuditLogEntry {
  action: string;
  actor_email: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

interface ImportResult {
  created: { row: number; sku: string }[];
  errors: { row: number; sku: string | null; message: string }[];
}

const TABLE_COLUMN_COUNT = 11;

function ItemHistoryRow({ itemId }: { itemId: string }) {
  const history = useQuery<AuditLogEntry[]>({
    queryKey: ["item-audit-log", itemId],
    queryFn: async () => {
      const res = await apiFetch(`/items/${itemId}/audit-log`);
      if (!res.ok) throw new Error("Failed to load history");
      return res.json();
    },
  });

  return (
    <TR>
      <TD colSpan={TABLE_COLUMN_COUNT} className="bg-secondary/20">
        {history.isLoading ? (
          <span className="text-sm text-muted-foreground">Loading history...</span>
        ) : history.data && history.data.length > 0 ? (
          <ul className="space-y-1 text-sm">
            {history.data.map((entry, i) => (
              <li key={i} className="text-muted-foreground">
                <span className="font-medium text-foreground">
                  {humanize(entry.action.split(".").pop() ?? entry.action)}
                </span>
                {" by "}
                {entry.actor_email ?? "unknown"} — {new Date(entry.created_at).toLocaleString()}
              </li>
            ))}
          </ul>
        ) : (
          <span className="text-sm text-muted-foreground">No history yet.</span>
        )}
      </TD>
    </TR>
  );
}

export function Items() {
  const queryClient = useQueryClient();
  const [sku, setSku] = useState("");
  const [name, setName] = useState("");
  const [itemType, setItemType] = useState("component");
  const [unitOfMeasure, setUnitOfMeasure] = useState("ea");
  const [isLotTracked, setIsLotTracked] = useState(false);
  const [leadTimeDays, setLeadTimeDays] = useState("");
  const [description, setDescription] = useState("");
  const [barcode, setBarcode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [expandedHistoryIds, setExpandedHistoryIds] = useState<Set<string>>(new Set());
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [importResult, setImportResult] = useState<ImportResult | null>(null);

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
      setDescription("");
      setBarcode("");
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

      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      {isOwner ? (
        <Card className="mb-8">
          <CardHeader>
            <CardTitle>Add an item</CardTitle>
          </CardHeader>
          <CardContent>
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
      ) : (
        <Alert className="mb-8">Only organization owners can add or edit items. You can still view the catalog and export it below.</Alert>
      )}

      {items.data && items.data.length === 0 ? (
        <EmptyState
          icon={<Package />}
          title="No items yet"
          description="Add your first SKU using the form above."
        />
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-4">
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
          </div>

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
                      <TH>Description</TH>
                      <TH>Barcode</TH>
                      <TH>Status</TH>
                      <TH>Preferred supplier</TH>
                      <TH>History</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {filtered.map((item) => {
                      const fieldDisabled = !isOwner || pendingFieldItemIds.has(item.id);
                      return (
                        <Fragment key={item.id}>
                          <TR className={item.is_active ? undefined : "opacity-60"}>
                            <TD className="font-medium">{item.sku}</TD>
                            <TD>
                              <div className="max-w-[160px]">
                                <Label className="sr-only">Name</Label>
                                <Input
                                  defaultValue={item.name}
                                  disabled={fieldDisabled}
                                  onBlur={(e) => {
                                    const value = e.target.value;
                                    if (!value || value === item.name) return;
                                    patchItemField.mutate({
                                      itemId: item.id,
                                      preferredSupplierId: item.preferred_supplier_id,
                                      field: "name",
                                      value,
                                    });
                                  }}
                                />
                              </div>
                            </TD>
                            <TD>
                              <Badge variant={itemTypeVariant(item.item_type)}>{humanize(item.item_type)}</Badge>
                            </TD>
                            <TD>
                              <div className="max-w-[80px]">
                                <Label className="sr-only">Unit of measure</Label>
                                <Input
                                  defaultValue={item.unit_of_measure}
                                  disabled={fieldDisabled}
                                  onBlur={(e) => {
                                    const value = e.target.value;
                                    if (!value || value === item.unit_of_measure) return;
                                    patchItemField.mutate({
                                      itemId: item.id,
                                      preferredSupplierId: item.preferred_supplier_id,
                                      field: "unit_of_measure",
                                      value,
                                    });
                                  }}
                                />
                              </div>
                            </TD>
                            <TD>
                              {item.is_lot_tracked ? (
                                <Badge variant="primary">Yes</Badge>
                              ) : (
                                <span className="text-muted-foreground">No</span>
                              )}
                            </TD>
                            <TD>
                              <div className="max-w-[110px]">
                                <Label className="sr-only">Lead time (days)</Label>
                                <Input
                                  type="number"
                                  min={0}
                                  defaultValue={item.lead_time_days ?? ""}
                                  disabled={!isOwner || pendingLeadTimeItemIds.has(item.id)}
                                  onBlur={(e) => {
                                    const raw = e.target.value;
                                    const value = raw === "" ? null : Number(raw);
                                    if (value === item.lead_time_days) return;
                                    setLeadTime.mutate({
                                      itemId: item.id,
                                      preferredSupplierId: item.preferred_supplier_id,
                                      leadTimeDays: value,
                                    });
                                  }}
                                />
                              </div>
                            </TD>
                            <TD>
                              <div className="max-w-[160px]">
                                <Label className="sr-only">Description</Label>
                                <Input
                                  defaultValue={item.description ?? ""}
                                  disabled={fieldDisabled}
                                  onBlur={(e) => {
                                    const raw = e.target.value;
                                    const value = raw === "" ? null : raw;
                                    if (value === item.description) return;
                                    patchItemField.mutate({
                                      itemId: item.id,
                                      preferredSupplierId: item.preferred_supplier_id,
                                      field: "description",
                                      value,
                                    });
                                  }}
                                />
                              </div>
                            </TD>
                            <TD>
                              <div className="max-w-[120px]">
                                <Label className="sr-only">Barcode</Label>
                                <Input
                                  defaultValue={item.barcode ?? ""}
                                  disabled={fieldDisabled}
                                  onBlur={(e) => {
                                    const raw = e.target.value;
                                    const value = raw === "" ? null : raw;
                                    if (value === item.barcode) return;
                                    patchItemField.mutate({
                                      itemId: item.id,
                                      preferredSupplierId: item.preferred_supplier_id,
                                      field: "barcode",
                                      value,
                                    });
                                  }}
                                />
                              </div>
                            </TD>
                            <TD>
                              <div className="flex items-center gap-2">
                                <Badge variant={item.is_active ? "success" : "neutral"}>
                                  {item.is_active ? "Active" : "Inactive"}
                                </Badge>
                                {isOwner && (
                                  <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    disabled={pendingActiveItemIds.has(item.id)}
                                    onClick={() =>
                                      setActive.mutate({
                                        itemId: item.id,
                                        preferredSupplierId: item.preferred_supplier_id,
                                        isActive: !item.is_active,
                                      })
                                    }
                                  >
                                    {item.is_active ? "Deactivate" : "Activate"}
                                  </Button>
                                )}
                              </div>
                            </TD>
                            <TD>
                              <div className="max-w-[220px]">
                                <Label className="sr-only">Preferred supplier</Label>
                                <Select
                                  value={item.preferred_supplier_id ?? ""}
                                  disabled={!isOwner || pendingSupplierItemIds.has(item.id)}
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
                                </Select>
                              </div>
                            </TD>
                            <TD>
                              <Button type="button" variant="ghost" size="sm" onClick={() => toggleHistory(item.id)}>
                                {expandedHistoryIds.has(item.id) ? "Hide" : "History"}
                              </Button>
                            </TD>
                          </TR>
                          {expandedHistoryIds.has(item.id) && <ItemHistoryRow itemId={item.id} />}
                        </Fragment>
                      );
                    })}
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
