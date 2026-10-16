import { describe, expect, it } from "vitest";
import { GRID, render } from "./Planning.testUtils";

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
