import { describe, expect, it } from "vitest";
import { summarizeConflicts } from "./suggestLogic";

const existing = [
  { item_id: "A", period_start: "2026-08-01", source: "manual" },
  { item_id: "A", period_start: "2026-08-05", source: "suggested" },
  { item_id: "A", period_start: "2036-01-01", source: "manual" }, // outside window
  { item_id: "B", period_start: "2026-08-02", source: "manual" }, // other item
];

describe("summarizeConflicts", () => {
  it("counts in-window rows for the item and unchecks by default", () => {
    const s = summarizeConflicts("A", "2026-07-26", "2026-08-24", existing);
    expect(s).toEqual({ total: 2, manual: 1, defaultChecked: false });
  });

  it("checks by default when the item has no in-window rows", () => {
    const s = summarizeConflicts("C", "2026-07-26", "2026-08-24", existing);
    expect(s).toEqual({ total: 0, manual: 0, defaultChecked: true });
  });

  it("treats window bounds as inclusive", () => {
    const s = summarizeConflicts(
      "A",
      "2026-08-01",
      "2026-08-05",
      existing,
    );
    expect(s.total).toBe(2);
  });
});
