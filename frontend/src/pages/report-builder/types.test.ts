import { describe, expect, it } from "vitest";
import { blankSection, draftFromSection, sectionSummary, sectionToPayload } from "./types";

describe("report section drafts", () => {
  it("round-trips each section kind through draft and payload", () => {
    const sections = [
      { type: "text", title: "Intro", text: "Hello" },
      { type: "findings", title: "F", items: ["a", "b"] },
      { type: "table", title: "T", dataset_id: "ds1", query: { select: ["x"] } },
      { type: "bi_chart", title: "C", chart_id: "c1" },
    ];
    for (const s of sections) expect(sectionToPayload(draftFromSection(s))).toEqual(s);
  });

  it("trims bullet lines and drops blanks", () => {
    const d = { ...blankSection(), type: "risks" as const, itemsText: " one \n\n two " };
    expect(sectionToPayload(d)).toEqual({ type: "risks", title: undefined, items: ["one", "two"] });
  });

  it("rejects invalid query JSON with the section name", () => {
    const d = { ...blankSection(), type: "table" as const, title: "Sales", queryText: "{" };
    expect(() => sectionToPayload(d)).toThrow('Section "Sales" has invalid query JSON');
  });

  it("defaults a missing type to text", () => {
    expect(draftFromSection({}).type).toBe("text");
  });

  it("summarises each kind", () => {
    expect(sectionSummary({ type: "text", text: "x".repeat(80) })).toHaveLength(60);
    expect(sectionSummary({ type: "caveats", items: [1, 2, 3] })).toBe("3 item(s)");
    expect(sectionSummary({ type: "caveats" })).toBe("0 item(s)");
    expect(sectionSummary({ type: "table", dataset_id: "ds1" })).toBe("dataset ds1");
    expect(sectionSummary({ type: "bi_chart" })).toBe("chart ?");
  });
});
