import { FormEvent, useState } from "react";
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
      <PageHeader title="Items" description="Raw materials, components, and finished goods in your catalog." />

      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

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
            <label className="flex items-center gap-2 self-end pb-2.5 text-sm">
              <Checkbox
                checked={isLotTracked}
                onChange={(e) => setIsLotTracked(e.target.checked)}
              />
              Lot tracked
            </label>
            <div className="flex items-end sm:col-span-2 lg:col-span-1 lg:col-start-4">
              <Button type="submit" className="w-full" loading={createItem.isPending}>
                Add item
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {items.data && items.data.length === 0 ? (
        <EmptyState
          icon={<Package />}
          title="No items yet"
          description="Add your first SKU using the form above."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>SKU</TH>
                <TH>Name</TH>
                <TH>Type</TH>
                <TH>UoM</TH>
                <TH>Lot tracked</TH>
                <TH>Preferred supplier</TH>
              </TR>
            </THead>
            <TBody>
              {items.data?.map((item) => (
                <TR key={item.id}>
                  <TD className="font-medium">{item.sku}</TD>
                  <TD>{item.name}</TD>
                  <TD>
                    <Badge variant={itemTypeVariant(item.item_type)}>{humanize(item.item_type)}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">{item.unit_of_measure}</TD>
                  <TD>
                    {item.is_lot_tracked ? (
                      <Badge variant="primary">Yes</Badge>
                    ) : (
                      <span className="text-muted-foreground">No</span>
                    )}
                  </TD>
                  <TD>
                    <div className="max-w-[220px]">
                      <Label className="sr-only">Preferred supplier</Label>
                      <Select
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
                      </Select>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}
