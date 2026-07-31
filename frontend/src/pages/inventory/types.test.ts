import { describe, expect, it } from "vitest";
import {
  abcLabel,
  rcaBannerSentence,
  rcaTopValue,
  scenarioDeltaSentence,
  sortInventoryRows,
  whyThisStatusSentence,
  xyzLabel,
  type InventoryAnalyticsLine,
  type RcaResult,
  type ScenarioDelta,
} from "./types";

describe("abcLabel", () => {
  it("translates each class to plain language", () => {
    expect(abcLabel("A")).toBe("High-value item");
    expect(abcLabel("B")).toBe("Mid-value item");
    expect(abcLabel("C")).toBe("Lower-value item");
  });

  it("returns null for an unclassified item", () => {
    expect(abcLabel(null)).toBeNull();
  });
});

describe("xyzLabel", () => {
  it("translates each class to plain language", () => {
    expect(xyzLabel("X")).toBe("Steady demand");
    expect(xyzLabel("Y")).toBe("Somewhat variable demand");
    expect(xyzLabel("Z")).toBe("Erratic demand");
  });

  it("returns null for an unclassified item", () => {
    expect(xyzLabel(null)).toBeNull();
  });
});

const LINE: InventoryAnalyticsLine = {
  item_id: "i-1",
  reorder_point: 120,
  safety_stock: 40,
  days_of_supply: 12,
  abc_class: "A",
  xyz_class: "X",
  status: "low",
};

describe("whyThisStatusSentence", () => {
  it("explains a low item with its reorder point and value/demand class", () => {
    expect(whyThisStatusSentence(LINE)).toBe(
      "12 days of stock left, below your reorder point of 120 units. High-value item, steady demand.",
    );
  });

  it("explains a stockout without a days-of-supply figure", () => {
    expect(whyThisStatusSentence({ ...LINE, status: "stockout", days_of_supply: null, abc_class: null, xyz_class: null })).toBe(
      "Out of stock.",
    );
  });

  it("explains dead stock", () => {
    expect(whyThisStatusSentence({ ...LINE, status: "dead_stock", abc_class: null, xyz_class: null })).toBe(
      "No recent demand, but stock is still on hand.",
    );
  });

  it("falls back to plain status text for no_data", () => {
    expect(whyThisStatusSentence({ ...LINE, status: "no_data", abc_class: null, xyz_class: null })).toBe(
      "Not enough history yet to analyze this item.",
    );
  });
});

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

describe("rcaTopValue / rcaBannerSentence", () => {
  it("picks the top significant value and phrases a supplier finding", () => {
    expect(rcaTopValue(SIGNIFICANT_RESULT)?.value).toBe("Acme Supplier");
    expect(rcaBannerSentence(SIGNIFICANT_RESULT)).toBe(
      "Items from Acme Supplier run low 3.2× more often than your other stock.",
    );
  });

  it("phrases an item_type finding without 'from'", () => {
    const result: RcaResult = {
      ...SIGNIFICANT_RESULT,
      dimension: "item_type",
      values: [{ ...SIGNIFICANT_RESULT.values[0], value: "raw_material" }],
    };
    expect(rcaBannerSentence(result)).toBe(
      "Raw Material items run low 3.2× more often than your other stock.",
    );
  });

  it("returns null when the method is insufficient_data", () => {
    expect(rcaBannerSentence({ ...SIGNIFICANT_RESULT, method: "insufficient_data", values: [] })).toBeNull();
  });

  it("returns null when no value is significant", () => {
    const result: RcaResult = {
      ...SIGNIFICANT_RESULT,
      values: [{ ...SIGNIFICANT_RESULT.values[0], is_significant: false }],
    };
    expect(rcaBannerSentence(result)).toBeNull();
  });

  it("skips a significant but under-represented (negative-residual) value", () => {
    const result: RcaResult = {
      ...SIGNIFICANT_RESULT,
      values: [
        {
          value: "Supplier B",
          item_count: 10,
          at_risk_count: 0,
          expected_at_risk_count: 5,
          standardized_residual: -2.24,
          is_significant: true,
        },
        {
          value: "Supplier A",
          item_count: 10,
          at_risk_count: 10,
          expected_at_risk_count: 5,
          standardized_residual: 2.24,
          is_significant: true,
        },
      ],
    };
    expect(rcaTopValue(result)?.value).toBe("Supplier A");
  });

  it("translates a bare ABC letter to plain language in the banner", () => {
    const result: RcaResult = {
      ...SIGNIFICANT_RESULT,
      dimension: "abc_class",
      values: [{ ...SIGNIFICANT_RESULT.values[0], value: "A" }],
    };
    const sentence = rcaBannerSentence(result);
    expect(sentence).toContain("High-value items");
    expect(sentence).not.toContain("A class");
  });
});

describe("scenarioDeltaSentence", () => {
  it("describes a reorder point drop with a status change", () => {
    const delta: ScenarioDelta = {
      item_id: "i-1",
      baseline_lead_time_days: 14,
      scenario_lead_time_days: 7,
      baseline_reorder_point: 120,
      scenario_reorder_point: 95,
      reorder_point_delta: -25,
      baseline_status: "low",
      scenario_status: "healthy",
      classification: "improved",
    };
    expect(scenarioDeltaSentence(delta)).toBe(
      "Reorder point would drop from 120 to 95 units; status would change from Low to Healthy.",
    );
  });

  it("says nothing changes when the status is the same", () => {
    const delta: ScenarioDelta = {
      item_id: "i-1",
      baseline_lead_time_days: 14,
      scenario_lead_time_days: 12,
      baseline_reorder_point: 120,
      scenario_reorder_point: 118,
      reorder_point_delta: -2,
      baseline_status: "healthy",
      scenario_status: "healthy",
      classification: "unchanged",
    };
    expect(scenarioDeltaSentence(delta)).toBe("Reorder point would drop from 120 to 118 units.");
  });

  it("says the reorder point can't be estimated without a lead time", () => {
    const delta: ScenarioDelta = {
      item_id: "i-1",
      baseline_lead_time_days: null,
      scenario_lead_time_days: 7,
      baseline_reorder_point: null,
      scenario_reorder_point: null,
      reorder_point_delta: null,
      baseline_status: "no_data",
      scenario_status: "no_data",
      classification: "unchanged",
    };
    expect(scenarioDeltaSentence(delta)).toBe("Reorder point can't be estimated without a lead time.");
  });
});

describe("sortInventoryRows", () => {
  const analyticsByItem = new Map<string, InventoryAnalyticsLine>([
    ["healthy-c", { ...LINE, item_id: "healthy-c", status: "healthy", abc_class: "C" }],
    ["stockout-b", { ...LINE, item_id: "stockout-b", status: "stockout", abc_class: "B" }],
    ["stockout-a", { ...LINE, item_id: "stockout-a", status: "stockout", abc_class: "A" }],
  ]);
  const rows = [
    { item_id: "healthy-c", item_sku: "HC" },
    { item_id: "stockout-b", item_sku: "SB" },
    { item_id: "stockout-a", item_sku: "SA" },
  ];

  it("sorts stockouts before healthy items, high-value first within a tie", () => {
    const sorted = sortInventoryRows(rows, analyticsByItem);
    expect(sorted.map((r) => r.item_id)).toEqual(["stockout-a", "stockout-b", "healthy-c"]);
  });

  it("puts rows with no analytics data last, ahead of nothing", () => {
    const sorted = sortInventoryRows(
      [...rows, { item_id: "no-data", item_sku: "ND" }],
      analyticsByItem,
    );
    expect(sorted[sorted.length - 1].item_id).toBe("no-data");
  });
});
