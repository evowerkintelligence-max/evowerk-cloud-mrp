import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Planning } from "./Planning";
import { CollapseConfirm } from "./planning/PlannedQtyInput";
import { ProductDetail } from "./planning/ProductDetail";

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

  it("points at production when a job has gone quiet", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        summary: { ...GRID.summary, jobs_needing_attention: 2 },
      }),
    );
    expect(html).toContain("2 jobs need attention");
    expect(html).toContain('href="/production"');
  });

  it("says nothing about jobs when none need attention", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).not.toContain("attention");
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

  it("says Arriving when a shortfall is covered by a real, submitted PO or job", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [{ ...GRID.products[0], materials_status: "arriving", arriving_is_drafted: false }],
      }),
    );
    expect(html).toContain("Arriving");
    expect(html).not.toContain("Drafted, not ordered yet");
  });

  it("does not claim Arriving when the only cover is a draft PO nobody sent", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [{ ...GRID.products[0], materials_status: "arriving", arriving_is_drafted: true }],
      }),
    );
    expect(html).toContain("Drafted, not ordered yet");
    expect(html).not.toMatch(/>Arriving</);
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

  it("colours ready-by red when it lands after the needed-by date", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    // PUMP-A2: needed_by 2026-08-12, ready_by 2026-08-19 - the plan lands late.
    const cell = html.match(/<td class="[^"]*">2026-08-19<\/td>/)?.[0] ?? "";
    expect(cell).toContain("text-destructive");
  });

  it("does not colour ready-by when it lands on or before the needed-by date", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    // HOUS-H1: needed_by 2026-08-30, ready_by 2026-07-26 - comfortably early.
    const cell = html.match(/<td class="[^"]*">2026-07-26<\/td>/)?.[0] ?? "";
    expect(cell).not.toContain("text-destructive");
  });

  it("says the lead time is unknown rather than 'Not scheduled' when a component's lead time is missing", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [{ ...GRID.products[0], ready_by: null, ready_by_unknown: true }],
      }),
    );
    expect(html).toContain("Lead time unknown");
    expect(html).not.toContain("Not scheduled");
  });

  it("says not scheduled when nothing is planned, not lead time unknown", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          {
            ...GRID.products[0],
            planned_qty: "0.0000",
            needed_by: null,
            planned_bucket_count: 0,
            ready_by: null,
            ready_by_unknown: false,
          },
        ],
      }),
    );
    expect(html).toContain("Not scheduled");
    expect(html).not.toContain("Lead time unknown");
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
                in_production: "0.0000",
                safety_stock: "0.0000",
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

  it("hides Import from CSV from a manager, whose submit would 403", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], GRID);
      qc.setQueryData(["org-me"], { role: "manager" });
    });
    expect(html).not.toContain("Import from CSV");
  });

  it("hides Import from CSV from a member", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], GRID);
      qc.setQueryData(["org-me"], { role: "member" });
    });
    expect(html).not.toContain("Import from CSV");
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

describe("overdue committed demand", () => {
  it("says the ordered quantity is late when it is", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [{ ...GRID.products[0], committed_overdue: true }],
      }),
    );
    expect(html).toContain("40 ordered, late");
  });

  it("says only the quantity when the commitment is on time", () => {
    const html = render((qc) => qc.setQueryData(["planning-grid"], GRID));
    expect(html).toContain("40 ordered");
    expect(html).not.toContain("ordered, late");
  });

  it("shows the late wording to a member too, whose cell is read-only", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [{ ...GRID.products[0], committed_overdue: true }],
      });
      qc.setQueryData(["org-me"], { role: "member", adjust_approval_threshold: "0" });
    });
    expect(html).toContain("40 ordered, late");
  });
});

describe("making what the grid says you need", () => {
  it("offers Make for the quantity still outstanding", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [{ ...GRID.products[0], can_make: true }],
      }),
    );
    expect(html).toContain("Make 50");
    expect(html).not.toContain("disabled=\"\"");
  });

  it("disables Make when the row cannot actually be made", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [{ ...GRID.products[0], can_make: false }],
      }),
    );
    // Paired with the enabled case above, which asserts no disabled attribute
    // is rendered at all: together they pin the can_make branch rather than
    // matching some other element's attribute.
    expect(html).toContain("Make 50");
    expect(html).toContain("disabled=\"\"");
  });

  it("says why Make is disabled instead of leaving a dead button unexplained", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          {
            ...GRID.products[0],
            can_make: false,
            cannot_make_reason: "Not enough SEAL-V2 free to start: 0 available, 100 needed.",
          },
        ],
      }),
    );
    expect(html).toContain("Not enough SEAL-V2 free to start");
  });

  it("does not show a reason once the row can actually be made", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [{ ...GRID.products[0], can_make: true, cannot_make_reason: null }],
      }),
    );
    expect(html).not.toContain("Not enough");
  });

  it("counts what is already being made out of the Make quantity", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          { ...GRID.products[0], can_make: true, in_production: "20.0000", outstanding_qty: "30.0000" },
        ],
      }),
    );
    expect(html).toContain("20 in production");
    expect(html).toContain("Make 30");
  });

  it("says a job is covering the row instead of offering Make again", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          {
            ...GRID.products[1],
            can_make: false,
            in_production: "200.0000",
            outstanding_qty: "0.0000",
          },
        ],
      }),
    );
    expect(html).toContain("200 in production");
    expect(html).toContain("A job is covering this row");
    expect(html).not.toContain("Make 0");
  });

  it("does not call a red row covered when the job is itself short of materials", () => {
    // A scheduled job supplies its finished good and demands its components,
    // so the row can read "1 missing" with nothing left outstanding. Saying it
    // is covered there is the opposite of the truth: the job cannot start.
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          { ...GRID.products[0], can_make: false, in_production: "50.0000", outstanding_qty: "0.0000" },
        ],
      }),
    );
    expect(html).toContain("A job is waiting on materials for this row");
    expect(html).not.toContain("A job is covering this row");
  });

  it("offers Make on a row wanted only by customers, which has no typed plan", () => {
    const html = render((qc) =>
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          {
            ...GRID.products[1],
            planned_qty: "0.0000",
            needed_by: null,
            planned_bucket_count: 0,
            committed_qty: "25.0000",
            outstanding_qty: "25.0000",
            can_make: true,
          },
        ],
      }),
    );
    expect(html).toContain("Make 25");
  });

  it("shows a member the in-production line but no Make button", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], {
        ...GRID,
        products: [
          { ...GRID.products[0], can_make: true, in_production: "20.0000", outstanding_qty: "30.0000" },
        ],
      });
      qc.setQueryData(["org-me"], { role: "member", adjust_approval_threshold: "0" });
    });
    expect(html).toContain("20 in production");
    expect(html).not.toContain("Make 30");
  });
});

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
