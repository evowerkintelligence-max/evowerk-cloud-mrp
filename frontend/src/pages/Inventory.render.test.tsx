import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { InsightBanner } from "./inventory/InsightBanner";
import type { RcaResult } from "./inventory/types";

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
