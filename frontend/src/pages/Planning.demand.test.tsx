import { describe, expect, it } from "vitest";
import { GRID, render } from "./Planning.testUtils";

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
