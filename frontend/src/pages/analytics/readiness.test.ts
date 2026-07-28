import { describe, expect, it } from "vitest";
import { computeReadiness, FEATURE_REQUIREMENTS } from "./readiness";

const numeric = { inferred_dtype: "numeric" };
const datetime = { inferred_dtype: "datetime" };
const text = { inferred_dtype: "text" };

describe("computeReadiness", () => {
  it("marks every feature ready when the dataset has 4 numeric, 1 datetime, and 1 text column", () => {
    const columns = [numeric, numeric, numeric, numeric, datetime, text];
    const results = computeReadiness(columns);
    expect(results).toHaveLength(FEATURE_REQUIREMENTS.length);
    for (const r of results) {
      expect(r.ready, `${r.label} should be ready`).toBe(true);
      expect(r.missing).toEqual([]);
    }
  });

  it("flags OEE-shaped features as not ready with an exact shortfall count when only 2 numeric columns exist", () => {
    const columns = [numeric, numeric];
    const results = computeReadiness(columns);
    const byKey = Object.fromEntries(results.map((r) => [r.key, r]));

    for (const key of ["oee", "rca", "plant_summary"]) {
      expect(byKey[key].ready).toBe(false);
      expect(byKey[key].missing).toEqual(["needs 2 more numeric columns (have 2, need 4)"]);
    }

    // Single-numeric-required features (and scrap, which needs exactly 2) are satisfied.
    for (const key of ["quality", "throughput", "downtime", "shifts", "scrap"]) {
      expect(byKey[key].ready).toBe(true);
    }
  });

  it("flags datetime-dependent features as not ready when the dataset has no datetime column", () => {
    const columns = [numeric, numeric, numeric, numeric, numeric, text, text];
    const results = computeReadiness(columns);
    const byKey = Object.fromEntries(results.map((r) => [r.key, r]));

    expect(byKey.process.ready).toBe(false);
    expect(byKey.process.missing).toEqual(["needs a datetime column"]);

    expect(byKey.mfg_forecast.ready).toBe(false);
    expect(byKey.mfg_forecast.missing).toEqual(["needs a datetime column"]);

    for (const key of ["oee", "rca", "plant_summary", "quality", "throughput", "downtime", "shifts", "scrap"]) {
      expect(byKey[key].ready).toBe(true);
    }
  });

  it("returns all-not-ready results without throwing for an empty column list", () => {
    expect(() => computeReadiness([])).not.toThrow();
    const results = computeReadiness([]);
    expect(results).toHaveLength(FEATURE_REQUIREMENTS.length);
    expect(results.every((r) => r.ready === false)).toBe(true);
  });
});
