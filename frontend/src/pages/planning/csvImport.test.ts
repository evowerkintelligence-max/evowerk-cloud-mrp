import { describe, expect, it } from "vitest";
import { mapForecastCsv } from "./csvImport";

const items = new Map([
  ["RAW-1", "id-1"],
  ["RAW-2", "id-2"],
]);

describe("mapForecastCsv", () => {
  it("maps valid rows, sku to item_id", () => {
    const { rows, errors } = mapForecastCsv(
      [
        ["sku", "period_start", "quantity", "notes"],
        ["RAW-1", "2026-08-01", "100", "week 1"],
        ["RAW-2", "2026-08-01", "2.5", ""],
      ],
      items,
    );
    expect(errors).toEqual([]);
    expect(rows).toEqual([
      { item_id: "id-1", period_start: "2026-08-01", quantity: "100", notes: "week 1" },
      { item_id: "id-2", period_start: "2026-08-01", quantity: "2.5", notes: null },
    ]);
  });

  it("accepts a header without the optional notes column", () => {
    const { rows, errors } = mapForecastCsv(
      [
        ["sku", "period_start", "quantity"],
        ["RAW-1", "2026-08-01", "1"],
      ],
      items,
    );
    expect(errors).toEqual([]);
    expect(rows[0].notes).toBeNull();
  });

  it("rejects a bad header", () => {
    const { errors } = mapForecastCsv([["sku", "qty"]], items);
    expect(errors[0].line).toBe(1);
    expect(errors[0].message).toMatch(/header/i);
  });

  it("flags unknown sku, bad date, bad quantity with line numbers", () => {
    const { rows, errors } = mapForecastCsv(
      [
        ["sku", "period_start", "quantity"],
        ["NOPE", "2026-08-01", "1"],
        ["RAW-1", "08/01/2026", "1"],
        ["RAW-1", "2026-08-01", "-5"],
        ["RAW-1", "2026-08-01", "abc"],
      ],
      items,
    );
    expect(rows).toEqual([]);
    expect(errors.map((e) => e.line)).toEqual([2, 3, 4, 5]);
  });

  it("errors on empty input", () => {
    const { errors } = mapForecastCsv([], items);
    expect(errors).toHaveLength(1);
  });
});
