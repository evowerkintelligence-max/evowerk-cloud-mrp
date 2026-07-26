import { useQuery } from "@tanstack/react-query";
import { CalendarRange } from "lucide-react";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import {
  materialsStatusLabel,
  materialsStatusVariant,
  trimQty,
  type PlanningGridResponse,
} from "./planning/types";

function summaryLine(summary: PlanningGridResponse["summary"]): string {
  if (summary.missing > 0) {
    const noun = summary.missing === 1 ? "product needs" : "products need";
    return `${summary.missing} ${noun} materials ordered.`;
  }
  if (summary.arriving > 0) {
    const noun = summary.arriving === 1 ? "product is" : "products are";
    return `${summary.arriving} ${noun} waiting on a delivery.`;
  }
  return "Everything is covered.";
}

export function Planning() {
  const grid = useQuery<PlanningGridResponse>({
    queryKey: ["planning-grid"],
    queryFn: async () => {
      const res = await apiFetch("/planning/grid");
      if (!res.ok) throw new Error("Failed to load the planning grid");
      return res.json();
    },
  });

  return (
    <div>
      <PageHeader
        title="Planning"
        description="Type how many you plan to make. Red means materials are missing."
      />

      {grid.isError && <Alert variant="destructive">{(grid.error as Error).message}</Alert>}
      {grid.isLoading && <Skeleton className="h-40 w-full" />}

      {grid.data && grid.data.products.length === 0 && (
        <EmptyState
          icon={<CalendarRange />}
          title="Add a bill of materials to start planning"
          description="Products you build appear here automatically once they have a recipe."
        />
      )}

      {grid.data && grid.data.products.length > 0 && (
        <>
          <p className="mb-4 text-sm font-medium">{summaryLine(grid.data.summary)}</p>
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Product</TH>
                  <TH>Planned</TH>
                  <TH>In stock</TH>
                  <TH>Materials</TH>
                  <TH>Ready by</TH>
                </TR>
              </THead>
              <TBody>
                {grid.data.products.map((product) => (
                  <TR key={product.item_id}>
                    <TD>
                      <span className="font-medium">{product.item_name}</span>
                      <span className="block text-xs text-muted-foreground">{product.item_sku}</span>
                    </TD>
                    <TD>
                      <Input
                        type="number"
                        min="0"
                        step="1"
                        className="w-24 text-right"
                        aria-label={`Planned quantity for ${product.item_name}`}
                        defaultValue={trimQty(product.planned_qty)}
                      />
                    </TD>
                    <TD>{trimQty(product.on_hand)}</TD>
                    <TD>
                      <Badge variant={materialsStatusVariant(product.materials_status)}>
                        {materialsStatusLabel(product)}
                      </Badge>
                      {product.missing_materials.length > 0 && (
                        <span className="block text-xs text-muted-foreground">
                          {product.missing_materials.join(", ")}
                        </span>
                      )}
                    </TD>
                    <TD>{product.ready_by ?? "—"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
        </>
      )}
    </div>
  );
}
