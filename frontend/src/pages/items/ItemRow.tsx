import { Fragment } from "react";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { TD, TR } from "@/components/ui/table";
import { humanize, itemTypeVariant } from "@/lib/status";
import type { ItemResponse, SupplierResponse } from "./types";
import { ItemHistoryRow } from "./ItemHistoryRow";
import type { useItemMutations } from "./useItemMutations";

export function ItemRow({
  item,
  isOwner,
  suppliers,
  mutations,
  historyExpanded,
  onToggleHistory,
}: {
  item: ItemResponse;
  isOwner: boolean;
  suppliers: SupplierResponse[] | undefined;
  mutations: ReturnType<typeof useItemMutations>;
  historyExpanded: boolean;
  onToggleHistory: (itemId: string) => void;
}) {
  const {
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
  } = mutations;
  const fieldDisabled = !isOwner || pendingFieldItemIds.has(item.id);
  return (
    <Fragment>
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
          <div className="max-w-[110px]">
            <Label className="sr-only">Min order qty</Label>
            <Input
              type="number"
              min={0}
              step="any"
              defaultValue={item.min_order_qty ?? ""}
              disabled={!isOwner || pendingMoqItemIds.has(item.id)}
              onBlur={(e) => {
                const raw = e.target.value;
                const value = raw === "" ? null : raw;
                if (value === item.min_order_qty) return;
                setMoq.mutate({
                  itemId: item.id,
                  preferredSupplierId: item.preferred_supplier_id,
                  minOrderQty: value,
                });
              }}
            />
          </div>
        </TD>
        <TD>
          <div className="max-w-[110px]">
            <Label className="sr-only">Safety stock qty</Label>
            <Input
              type="number"
              min={0}
              step="any"
              defaultValue={item.safety_stock_qty ?? ""}
              disabled={!isOwner || pendingSafetyStockItemIds.has(item.id)}
              onBlur={(e) => {
                const raw = e.target.value;
                const value = raw === "" ? null : raw;
                if (value === item.safety_stock_qty) return;
                setSafetyStock.mutate({
                  itemId: item.id,
                  preferredSupplierId: item.preferred_supplier_id,
                  safetyStockQty: value,
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
              {suppliers?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </div>
        </TD>
        <TD>
          <Button type="button" variant="ghost" size="sm" onClick={() => onToggleHistory(item.id)}>
            {historyExpanded ? "Hide" : "History"}
          </Button>
        </TD>
      </TR>
      {historyExpanded && <ItemHistoryRow itemId={item.id} />}
    </Fragment>
  );
}
