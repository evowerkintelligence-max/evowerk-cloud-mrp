import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { ProductDetail } from "./planning/ProductDetail";
import { GRID, render } from "./Planning.testUtils";

describe("ProductDetail (the 'Show working' panel)", () => {
  const LINE = {
    item_id: "c-1",
    item_sku: "SEAL-V2",
    item_name: "Viton seal",
    is_manufactured: false,
    gross_demand: "120.0000",
    on_hand: "8.0000",
    on_order: "0.0000",
    on_draft: "0.0000",
    in_production: "15.0000",
    safety_stock: "25.0000",
    net_requirement: "122.0000",
    suggested_order_quantity: "122.0000",
    need_by_date: "2026-08-12",
    suggested_order_date: "2026-07-29",
  };

  it("shows in-production and safety stock as their own columns", () => {
    const html = renderToString(createElement(ProductDetail, { lines: [LINE] }))
      .split("<!-- -->")
      .join("");
    expect(html).toContain("In production");
    expect(html).toContain("Safety stock");
    // Both figures must render as their own numbers rather than being
    // silently absorbed into "Still needed" with no visible line.
    expect(html).toContain("15");
    expect(html).toContain("25");
  });
});

describe("cross-link to Inventory", () => {
  it("links each product row to its Warehouse inventory detail", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain('href="/warehouse?item=p-1"');
    expect(html).toContain('href="/warehouse?item=p-2"');
  });
});
