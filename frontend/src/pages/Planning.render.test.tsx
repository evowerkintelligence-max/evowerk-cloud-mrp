import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Planning } from "./Planning";
import { CollapseConfirm } from "./planning/PlannedQtyInput";

const GRID = {
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
      on_hand: "8.0000",
      materials_status: "missing",
      missing_materials: ["ALU-6061", "SEAL-V2"],
      ready_by: "2026-08-19",
      suggested_qty: null,
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
      on_hand: "240.0000",
      materials_status: "in_stock",
      missing_materials: [],
      ready_by: "2026-07-26",
      suggested_qty: null,
      detail: [],
    },
  ],
  to_buy: [],
  summary: { missing: 1, arriving: 0, in_stock: 1 },
};

function render(seed: (qc: QueryClient) => void = () => {}) {
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

describe("Planning grid", () => {
  it("renders without data without crashing", () => {
    const html = render();
    expect(html).toContain("Planning");
  });

  it("shows a row per product with its material status", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain("PUMP-A2");
    expect(html).toContain("Coolant pump");
    expect(html).toContain("2 missing");
    expect(html).toContain("HOUS-H1");
    expect(html).toContain("All in stock");
  });

  it("summarises the counts above the grid", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain("1 product needs materials");
  });

  it("names the missing materials on the row", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain("ALU-6061, SEAL-V2");
  });

  it("shows the planned quantity in an editable input", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain('value="50"');
    expect(html).toContain('value="200"');
  });

  it("says everything is covered when no product is short", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [GRID.products[1]],
        summary: { missing: 0, arriving: 0, in_stock: 1 },
      }),
    );
    expect(html).toContain("Everything is covered");
  });

  it("colours a missing row red and an in-stock row green", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    const pumpRow = html.slice(html.indexOf("Coolant pump"), html.indexOf("Housing kit"));
    const housingRow = html.slice(html.indexOf("Housing kit"));
    expect(pumpRow).toContain("text-destructive");
    expect(housingRow).toContain("text-success");
  });

  it("invites the user to add a bill of materials when there is nothing to plan", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [],
        summary: { missing: 0, arriving: 0, in_stock: 0 },
      }),
    );
    expect(html).toContain("Add a bill of materials to start planning");
  });

  it("offers the history suggestion when nothing is planned", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          {
            ...GRID.products[0],
            planned_qty: "0.0000",
            needed_by: null,
            planned_bucket_count: 0,
            suggested_qty: "60.0000",
          },
        ],
      }),
    );
    expect(html).toContain("Use 60 from history");
  });

  it("does not offer a suggestion once a quantity is planned", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          {
            ...GRID.products[0],
            planned_qty: "50.0000",
            suggested_qty: "60.0000",
          },
        ],
      }),
    );
    expect(html).not.toContain("from history");
  });

  it("shows the needed-by date next to the quantity", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain('value="2026-08-12"');
  });

  it("states the period the grid covers", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain("2026-07-26 to 2026-10-24");
  });

  it("asks for a date when a quantity is typed on a product with nothing planned yet", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          {
            ...GRID.products[0],
            planned_qty: "50.0000",
            needed_by: null,
            planned_bucket_count: 0,
            suggested_qty: null,
          },
        ],
      }),
    );
    expect(html).toContain("Pick a date to save this quantity");
    expect(html).toContain('aria-invalid="true"');
  });

  it("does not ask for a date once a product already has a plan", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).not.toContain("Pick a date to save this quantity");
  });

  it("warns before collapsing a plan spread across several dates", () => {
    const html = renderToString(
      createElement(CollapseConfirm, {
        bucketCount: 13,
        pending: false,
        onConfirm: () => {},
        onCancel: () => {},
      }),
    )
      .split("<!-- -->")
      .join("");
    expect(html).toContain("spread across 13 dates");
    expect(html).toContain("cannot be undone");
  });

  it("renders the planned quantity as plain text for a member, not an input", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], GRID);
      qc.setQueryData(["org-me"], { role: "member" });
    });
    expect(html).not.toContain("<input");
  });

  const TO_BUY = [
    {
      item_id: "m-1",
      item_sku: "SEAL-V2",
      item_name: "Viton seal kit",
      quantity: "84.0000",
      supplier_name: "Sealtech",
      order_by: "2026-07-29",
      urgency: "overdue" as const,
    },
    {
      item_id: "m-2",
      item_sku: "FAST-M8",
      item_name: "M8 fastener",
      quantity: "5000.0000",
      supplier_name: null,
      order_by: "2026-09-02",
      urgency: "later" as const,
    },
  ];

  it("lists what to buy with supplier and order-by date", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], { ...GRID, to_buy: TO_BUY });
      qc.setQueryData(["org-me"], { role: "owner", adjust_approval_threshold: "0" });
    });
    expect(html).toContain("To buy for the plan above");
    expect(html).toContain("Viton seal kit");
    expect(html).toContain("order 84 from Sealtech");
    expect(html).toContain("2026-07-29");
    expect(html).toContain("no supplier set");
  });

  it("shows the order button to owners", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], { ...GRID, to_buy: TO_BUY });
      qc.setQueryData(["org-me"], { role: "owner", adjust_approval_threshold: "0" });
    });
    expect(html).toContain("Order all missing materials");
  });

  it("hides the order button from non-owners", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], { ...GRID, to_buy: TO_BUY });
      qc.setQueryData(["org-me"], { role: "operator", adjust_approval_threshold: "0" });
    });
    expect(html).not.toContain("Order all missing materials");
  });

  it("shows a manager the to-buy list but not the order button", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], { ...GRID, to_buy: TO_BUY });
      qc.setQueryData(["org-me"], { role: "manager", adjust_approval_threshold: "0" });
    });
    expect(html).toContain("To buy for the plan above");
    expect(html).toContain("Viton seal kit");
    expect(html).not.toContain("Order all missing materials");
  });

  it("hides the to-buy panel when there is nothing to buy", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], GRID);
      qc.setQueryData(["org-me"], { role: "owner", adjust_approval_threshold: "0" });
    });
    expect(html).not.toContain("To buy for the plan above");
  });

  it("hides the engine detail until the row is expanded", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          {
            ...GRID.products[0],
            detail: [
              {
                item_id: "p-1",
                item_sku: "PUMP-A2",
                item_name: "Coolant pump",
                is_manufactured: true,
                gross_demand: "50.0000",
                on_hand: "8.0000",
                on_order: "0.0000",
                on_draft: "0.0000",
                net_requirement: "42.0000",
                suggested_order_quantity: "42.0000",
                need_by_date: "2026-08-12",
                suggested_order_date: null,
              },
            ],
          },
        ],
      }),
    );
    expect(html).toContain("Show working");
    expect(html).not.toContain("Still needed");
  });

  it("keeps CSV import available behind a button", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain("Import from CSV");
  });
});

describe("committed customer demand on the grid", () => {
  it("shows an ordered line for a product with open orders", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain("40 ordered");
  });

  it("shows no ordered line for a product with none", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], { ...GRID, products: [GRID.products[1]] }),
    );
    // Matches the rendered line itself, not the bare word: the summary above
    // the grid already says "needs materials ordered".
    expect(html).not.toMatch(/\d+ ordered<\/p>/);
  });

  it("shows the ordered line to a member, whose cell is read-only", () => {
    // The read-only branch used to return a bare span, so this is the easiest
    // case for the ordered line to be missed in.
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], GRID);
      qc.setQueryData(["org-me"], { role: "member", adjust_approval_threshold: "0" });
    });
    expect(html).toContain("40 ordered");
  });
});
