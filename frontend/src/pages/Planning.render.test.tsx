import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Planning } from "./Planning";

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

  it("renders the planned quantity as plain text for a member, not an input", () => {
    const html = render((qc) => {
      qc.setQueryData(["planning-grid"], GRID);
      qc.setQueryData(["org-me"], { role: "member" });
    });
    expect(html).not.toContain("<input");
  });
});
