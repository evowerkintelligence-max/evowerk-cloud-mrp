import { describe, expect, it } from "vitest";
import { approvalStatusVariant, lifecycleStatusVariant, lotStatusVariant } from "./status";

describe("lifecycleStatusVariant", () => {
  it("colors active as success", () => {
    expect(lifecycleStatusVariant("active")).toBe("success");
  });

  it("colors archived and retired as neutral", () => {
    expect(lifecycleStatusVariant("archived")).toBe("neutral");
    expect(lifecycleStatusVariant("retired")).toBe("neutral");
  });

  it("colors anything else (draft, inactive) as warning", () => {
    expect(lifecycleStatusVariant("draft")).toBe("warning");
    expect(lifecycleStatusVariant("inactive")).toBe("warning");
  });
});

describe("approvalStatusVariant", () => {
  it("colors approved as success, rejected as danger, pending as warning", () => {
    expect(approvalStatusVariant("approved")).toBe("success");
    expect(approvalStatusVariant("rejected")).toBe("danger");
    expect(approvalStatusVariant("pending")).toBe("warning");
  });
});

describe("lotStatusVariant", () => {
  it("colors on_hold as warning, depleted as neutral, active as success", () => {
    expect(lotStatusVariant("on_hold")).toBe("warning");
    expect(lotStatusVariant("depleted")).toBe("neutral");
    expect(lotStatusVariant("active")).toBe("success");
  });
});
