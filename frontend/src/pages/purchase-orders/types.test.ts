import { describe, expect, it } from "vitest";
import { isLate } from "./types";

describe("isLate", () => {
  it("flags only sent POs past their expected date", () => {
    expect(isLate({ status: "submitted", expected_delivery_date: "2000-01-01" })).toBe(true);
    expect(isLate({ status: "partially_received", expected_delivery_date: "2000-01-01" })).toBe(true);
    expect(isLate({ status: "draft", expected_delivery_date: "2000-01-01" })).toBe(false);
    expect(isLate({ status: "submitted", expected_delivery_date: null })).toBe(false);
    expect(isLate({ status: "submitted", expected_delivery_date: "2999-01-01" })).toBe(false);
  });
});
