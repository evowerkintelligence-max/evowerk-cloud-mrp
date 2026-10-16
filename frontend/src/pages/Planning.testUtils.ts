import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Planning } from "./Planning";

export const GRID = {
  horizon_start: "2026-07-26",
  horizon_end: "2026-10-24",
  products: [
    {
      item_id: "p-1",
      item_sku: "PUMP-A2",
      item_name: "Coolant pump",
      planned_qty: "50.0000",
      needed_by: "2026-08-12",
      planned_bucket_count: 1,
      committed_qty: "40.0000",
      committed_overdue: false,
      on_hand: "8.0000",
      materials_status: "missing",
      missing_materials: ["ALU-6061", "SEAL-V2"],
      arriving_is_drafted: false,
      ready_by: "2026-08-19",
      ready_by_unknown: false,
      suggested_qty: null,
      in_production: "0.0000",
      outstanding_qty: "50.0000",
      can_make: false,
      cannot_make_reason: "Not enough SEAL-V2 free to start: 0 available, 100 needed.",
      detail: [],
    },
    {
      item_id: "p-2",
      item_sku: "HOUS-H1",
      item_name: "Housing kit",
      planned_qty: "200.0000",
      needed_by: "2026-08-30",
      planned_bucket_count: 1,
      committed_qty: "0",
      committed_overdue: false,
      on_hand: "240.0000",
      materials_status: "in_stock",
      missing_materials: [],
      arriving_is_drafted: false,
      ready_by: "2026-07-26",
      ready_by_unknown: false,
      suggested_qty: null,
      in_production: "0.0000",
      outstanding_qty: "200.0000",
      can_make: true,
      cannot_make_reason: null,
      detail: [],
    },
  ],
  to_buy: [],
  summary: { missing: 1, arriving: 0, in_stock: 1, jobs_needing_attention: 0 },
};

export function render(seed: (qc: QueryClient) => void = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // Default to an editable role so the existing tests (written before the
  // read-only role check existed) keep exercising the editable grid; the
  // read-only test below overrides this with a member role.
  qc.setQueryData(["org-me"], { role: "owner" });
  seed(qc);
  return renderToString(
    createElement(
      MemoryRouter,
      null,
      createElement(QueryClientProvider, { client: qc }, createElement(Planning)),
    ),
  )
    .split("<!-- -->")
    .join("");
}
