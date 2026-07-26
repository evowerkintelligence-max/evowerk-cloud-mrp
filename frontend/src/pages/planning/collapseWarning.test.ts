import { describe, expect, it } from "vitest";
import { shouldWarnBeforeCollapse } from "./collapseWarning";

describe("shouldWarnBeforeCollapse", () => {
  it("does not warn for a normal single-date plan", () => {
    expect(
      shouldWarnBeforeCollapse({ plannedBucketCount: 1, quantityChanged: true, alreadyConfirmed: false }),
    ).toBe(false);
  });

  it("does not warn when nothing is planned yet", () => {
    expect(
      shouldWarnBeforeCollapse({ plannedBucketCount: 0, quantityChanged: true, alreadyConfirmed: false }),
    ).toBe(false);
  });

  it("does not warn when the quantity has not actually changed", () => {
    expect(
      shouldWarnBeforeCollapse({ plannedBucketCount: 3, quantityChanged: false, alreadyConfirmed: false }),
    ).toBe(false);
  });

  it("warns when a multi-date plan's quantity changes and it has not been confirmed yet", () => {
    expect(
      shouldWarnBeforeCollapse({ plannedBucketCount: 3, quantityChanged: true, alreadyConfirmed: false }),
    ).toBe(true);
  });

  it("does not nag again once the user has confirmed this edit", () => {
    expect(
      shouldWarnBeforeCollapse({ plannedBucketCount: 3, quantityChanged: true, alreadyConfirmed: true }),
    ).toBe(false);
  });
});
