import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { InsightBanner } from "./inventory/InsightBanner";
import { AnalyticsDetail } from "./inventory/AnalyticsDetail";
import type { RcaResult, InventoryAnalyticsLine } from "./inventory/types";
import { InventoryPanel } from "./Inventory";

function renderWithProviders(node: ReturnType<typeof createElement>, seed: (qc: QueryClient) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed(qc);
  return renderToString(
    createElement(MemoryRouter, null, createElement(QueryClientProvider, { client: qc }, node)),
  )
    .split("<!-- -->")
    .join("");
}

const SIGNIFICANT_RESULT: RcaResult = {
  dimension: "supplier",
  method: "chi_square_independence",
  chi2_statistic: 14.2,
  p_value: 0.0002,
  degrees_of_freedom: 1,
  low_expected_frequency_warning: false,
  values: [
    {
      value: "Acme Supplier",
      item_count: 10,
      at_risk_count: 10,
      expected_at_risk_count: 3.125,
      standardized_residual: 3.9,
      is_significant: true,
    },
  ],
};

const EMPTY_RESULT: RcaResult = {
  dimension: "",
  method: "no_significant_dimension",
  chi2_statistic: null,
  p_value: null,
  degrees_of_freedom: null,
  low_expected_frequency_warning: false,
  values: [],
};

describe("InsightBanner", () => {
  it("shows a plain-language finding when one dimension is significant", () => {
    const html = renderWithProviders(createElement(InsightBanner), (qc) =>
      qc.setQueryData(["inventory-rca-auto"], SIGNIFICANT_RESULT),
    );
    expect(html).toContain("Items from Acme Supplier run low 3.2");
    expect(html).toContain("See why");
    expect(html).toContain("Check a different cause");
  });

  it("renders nothing when no dimension is significant", () => {
    const html = renderWithProviders(createElement(InsightBanner), (qc) =>
      qc.setQueryData(["inventory-rca-auto"], EMPTY_RESULT),
    );
    expect(html).toBe("");
  });

  it("renders nothing before the query has resolved", () => {
    const html = renderWithProviders(createElement(InsightBanner), () => {});
    expect(html).toBe("");
  });

  it("hides the raw numbers until 'See why' is used", () => {
    const html = renderWithProviders(createElement(InsightBanner), (qc) =>
      qc.setQueryData(["inventory-rca-auto"], SIGNIFICANT_RESULT),
    );
    expect(html).not.toContain("Expected");
  });
});

const ANALYTICS_LINE: InventoryAnalyticsLine = {
  item_id: "i-1",
  reorder_point: 120,
  safety_stock: 40,
  days_of_supply: 12,
  abc_class: "A",
  xyz_class: "X",
  status: "low",
};

describe("AnalyticsDetail", () => {
  function renderDetail() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return renderToString(
      createElement(
        MemoryRouter,
        null,
        createElement(
          QueryClientProvider,
          { client: qc },
          createElement(AnalyticsDetail, { itemId: "i-1", line: ANALYTICS_LINE }),
        ),
      ),
    )
      .split("<!-- -->")
      .join("");
  }

  it("explains why the item has its status in plain language", () => {
    const html = renderDetail();
    expect(html).toContain("12 days of stock left, below your reorder point of 120 units");
    expect(html).toContain("High-value item, steady demand");
  });

  it("hides the raw numbers until requested", () => {
    const html = renderDetail();
    expect(html).not.toContain("Safety stock");
  });

  it("shows a what-if lead time field", () => {
    const html = renderDetail();
    expect(html).toContain("What if lead time were");
  });
});

// Deliberately seeded with the healthy item first and the stockout item
// second, so the "sorts the most urgent item first" test actually exercises
// sortInventoryRows rather than passing by coincidence of API order.
const BALANCES = [
  {
    item_id: "healthy-c",
    item_sku: "HC",
    item_name: "Cardboard Box",
    unit_of_measure: "ea",
    quantity_on_hand: "500",
    by_location: [],
  },
  {
    item_id: "stockout-a",
    item_sku: "SA",
    item_name: "Steel Rod",
    unit_of_measure: "ea",
    quantity_on_hand: "0",
    by_location: [],
  },
];

const ANALYTICS_LINES = [
  {
    item_id: "stockout-a",
    reorder_point: 50,
    safety_stock: 10,
    days_of_supply: 0,
    abc_class: "A",
    xyz_class: "Z",
    status: "stockout",
  },
  {
    item_id: "healthy-c",
    reorder_point: 20,
    safety_stock: 5,
    days_of_supply: 90,
    abc_class: "C",
    xyz_class: "X",
    status: "healthy",
  },
];

function renderInventoryPanel(seed: (qc: QueryClient) => void, initialPath = "/warehouse") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed(qc);
  return renderToString(
    createElement(
      MemoryRouter,
      { initialEntries: [initialPath] },
      createElement(QueryClientProvider, { client: qc }, createElement(InventoryPanel)),
    ),
  )
    .split("<!-- -->")
    .join("");
}

describe("InventoryPanel integration", () => {
  it("sorts the most urgent, highest-value item first", () => {
    const html = renderInventoryPanel((qc) => {
      qc.setQueryData(["inventory"], BALANCES);
      qc.setQueryData(["inventory-analytics"], ANALYTICS_LINES);
      qc.setQueryData(["locations"], []);
    });
    expect(html.indexOf("Steel Rod")).toBeLessThan(html.indexOf("Cardboard Box"));
  });

  it("auto-selects and expands the row named by ?item= on load", () => {
    const html = renderInventoryPanel(
      (qc) => {
        qc.setQueryData(["inventory"], BALANCES);
        qc.setQueryData(["inventory-analytics"], ANALYTICS_LINES);
        qc.setQueryData(["locations"], []);
        qc.setQueryData(["inventory", "healthy-c"], {
          ...BALANCES[0], // BALANCES[0] is healthy-c (Cardboard Box) - see the seed order note above
          transactions: [],
        });
      },
      "/warehouse?item=healthy-c",
    );
    expect(html).toContain("Why this status");
  });

  it("links to the Planning Grid from the row detail", () => {
    const html = renderInventoryPanel(
      (qc) => {
        qc.setQueryData(["inventory"], BALANCES);
        qc.setQueryData(["inventory-analytics"], ANALYTICS_LINES);
        qc.setQueryData(["locations"], []);
        qc.setQueryData(["inventory", "healthy-c"], {
          ...BALANCES[0], // BALANCES[0] is healthy-c (Cardboard Box) - see the seed order note above
          transactions: [],
        });
      },
      "/warehouse?item=healthy-c",
    );
    expect(html).toContain('href="/planning"');
  });
});
