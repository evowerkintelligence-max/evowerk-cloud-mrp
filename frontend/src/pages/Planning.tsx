import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CalendarRange } from "lucide-react";
import { apiFetch } from "../api/client";
import { useCurrentUser } from "@/lib/currentUser";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { ImportForecastsCsvSection } from "./planning/ImportForecastsCsv";
import { PlannedQtyInput } from "./planning/PlannedQtyInput";
import { ProductDetail } from "./planning/ProductDetail";
import { ToBuyPanel } from "./planning/ToBuyPanel";
import {
  materialsStatusLabel,
  materialsStatusVariant,
  readyByIsLate,
  readyByLabel,
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
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);
  const currentUser = useCurrentUser();
  const canEdit = currentUser.data?.role === "owner" || currentUser.data?.role === "manager";
  // POST /mrp/generate-pos is gated to owner only on the backend (stricter
  // than the manager-allowed quantity edit above), so this check is deliberately
  // narrower than canEdit.
  const canOrder = currentUser.data?.role === "owner";
  // POST /forecasts/import is also owner-only. Kept as its own check rather
  // than reused from canOrder: the two happen to share a role today, but
  // nothing ties an ordering permission to an import permission, and the
  // 403 a non-owner got on submit is the exact bug this button now avoids
  // showing them in the first place.
  const canImport = currentUser.data?.role === "owner";
  const grid = useQuery<PlanningGridResponse>({
    queryKey: ["planning-grid"],
    queryFn: async () => {
      const res = await apiFetch("/planning/grid");
      if (!res.ok) throw new Error("Failed to load the planning grid");
      return res.json();
    },
  });

  const items = useQuery<{ id: string; sku: string }[]>({
    queryKey: ["items"],
    enabled: showImport,
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  return (
    <div>
      <PageHeader
        title="Planning"
        description="Type how many you plan to make. Green is in stock, amber is on the way, red is missing."
        actions={
          canImport && (
            <Button variant="secondary" size="sm" onClick={() => setShowImport((v) => !v)}>
              Import from CSV
            </Button>
          )
        }
      />

      {canImport && showImport && <ImportForecastsCsvSection items={items.data ?? []} />}

      {grid.isError && <Alert variant="destructive">{(grid.error as Error).message}</Alert>}
      {error && <Alert variant="destructive">{error}</Alert>}
      {grid.isLoading && <Skeleton className="h-40 w-full" />}

      {/* Outside the products block on purpose: a job that has gone quiet is
          worth saying even for a shop whose grid has no rows yet. */}
      {grid.data && grid.data.summary.jobs_needing_attention > 0 && (
        <p className="mb-4 text-sm font-medium text-destructive">
          <Link to="/production" className="underline">
            {grid.data.summary.jobs_needing_attention}{" "}
            {grid.data.summary.jobs_needing_attention === 1 ? "job needs" : "jobs need"} attention
          </Link>
        </p>
      )}

      {grid.data && grid.data.products.length === 0 && (
        <EmptyState
          icon={<CalendarRange />}
          title="Add a bill of materials to start planning"
          description="Products you build appear here automatically once they have a recipe."
        />
      )}

      {grid.data && grid.data.products.length > 0 && (
        <>
          <p className="mb-1 text-xs text-muted-foreground">
            This covers {grid.data.horizon_start} to {grid.data.horizon_end}.
          </p>
          <p className="mb-4 text-sm font-medium">{summaryLine(grid.data.summary)}</p>
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Product</TH>
                  <TH>Planned</TH>
                  <TH>On hand</TH>
                  <TH>Materials</TH>
                  <TH>Ready by</TH>
                  <TH className="text-right">Detail</TH>
                </TR>
              </THead>
              <TBody>
                {grid.data.products.map((product) => {
                  const isExpanded = expanded === product.item_id;
                  return (
                    <Fragment key={product.item_id}>
                      <TR>
                        <TD>
                          <span className="font-medium">{product.item_name}</span>
                          <span className="block text-xs text-muted-foreground">{product.item_sku}</span>
                        </TD>
                        <TD>
                          <PlannedQtyInput
                            product={product}
                            onError={setError}
                            onSaved={() => setError(null)}
                            readOnly={!canEdit}
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
                        <TD className={readyByIsLate(product) ? "font-medium text-destructive" : undefined}>
                          {readyByLabel(product)}
                        </TD>
                        <TD className="text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-expanded={isExpanded}
                            aria-label={`${isExpanded ? "Hide" : "Show"} working for ${product.item_name}`}
                            onClick={() => setExpanded(isExpanded ? null : product.item_id)}
                          >
                            {isExpanded ? "Hide working" : "Show working"}
                          </Button>
                        </TD>
                      </TR>
                      {isExpanded && (
                        <TR>
                          <TD colSpan={6}>
                            <ProductDetail lines={product.detail} />
                          </TD>
                        </TR>
                      )}
                    </Fragment>
                  );
                })}
              </TBody>
            </Table>
          </TableWrap>
          <ToBuyPanel rows={grid.data.to_buy} canOrder={canOrder} />
        </>
      )}
    </div>
  );
}
