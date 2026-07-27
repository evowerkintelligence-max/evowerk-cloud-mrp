import { describe, expect, it } from "vitest";
import { shouldWarnBeforeCollapse } from "./collapseWarning";

describe("shouldWarnBeforeCollapse", () => {
  it("does not warn for a normal single-date plan", () => {
    expect(shouldWarnBeforeCollapse({ plannedBucketCount: 1, alreadyConfirmed: false })).toBe(
      false,
    );
  });

  it("does not warn when nothing is planned yet", () => {
    expect(shouldWarnBeforeCollapse({ plannedBucketCount: 0, alreadyConfirmed: false })).toBe(
      false,
    );
  });

  it("warns whenever a multi-date plan is about to be saved", () => {
    // Previously gated on the quantity having changed, which let a date-only
    // edit collapse an imported plan with no warning at all.
    expect(shouldWarnBeforeCollapse({ plannedBucketCount: 3, alreadyConfirmed: false })).toBe(true);
  });

  it("does not nag again once the user has confirmed this edit", () => {
    expect(shouldWarnBeforeCollapse({ plannedBucketCount: 3, alreadyConfirmed: true })).toBe(false);
  });
});
