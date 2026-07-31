import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PurchaseOrdersPanel } from "./PurchaseOrders";

const YESTERDAY = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
const TOMORROW = new Date(Date.now() + 86400000).toISOString().slice(0, 10);

const LATE_PO = {
  id: "po-1",
  supplier_id: "sup-1",
  supplier_name: "Sealtech",
  po_number: 12,
  status: "submitted",
  expected_delivery_date: YESTERDAY,
};

const ON_TIME_PO = {
  id: "po-2",
  supplier_id: "sup-1",
  supplier_name: "Sealtech",
  po_number: 13,
  status: "submitted",
  expected_delivery_date: TOMORROW,
};

const DRAFT_PO = {
  id: "po-3",
  supplier_id: "sup-1",
  supplier_name: "Sealtech",
  po_number: 14,
  status: "draft",
  expected_delivery_date: YESTERDAY,
};

function renderPos(rows: unknown[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["purchase-orders", "", 0], rows);
  qc.setQueryData(["items"], []);
  qc.setQueryData(["suppliers", "active"], []);
  return renderToString(
    createElement(QueryClientProvider, { client: qc }, createElement(PurchaseOrdersPanel)),
  ).split("<!-- -->").join("");
}

describe("PurchaseOrdersPanel", () => {
  it("badges a submitted PO past its expected delivery date as late", () => {
    const html = renderPos([LATE_PO]);
    expect(html).toContain("PO-12");
    expect(html).toContain("Late");
  });

  it("does not badge a submitted PO still due in the future", () => {
    const html = renderPos([ON_TIME_PO]);
    expect(html).toContain("PO-13");
    expect(html).not.toContain("Late");
  });

  it("does not badge a draft PO even if its expected date has passed", () => {
    // A draft hasn't been sent to the supplier yet, so "late" doesn't apply.
    const html = renderPos([DRAFT_PO]);
    expect(html).toContain("PO-14");
    expect(html).not.toContain("Late");
  });
});
