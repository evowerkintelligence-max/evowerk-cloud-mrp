import { describe, expect, it } from "vitest";
import { interpolateCountUp } from "./useCountUp";

describe("interpolateCountUp", () => {
  it("returns the start value at elapsed=0", () => {
    expect(interpolateCountUp(0, 100, 0, 500)).toBe(0);
  });

  it("returns the end value once the duration has fully elapsed", () => {
    expect(interpolateCountUp(0, 100, 500, 500)).toBe(100);
  });

  it("clamps to the end value if elapsed exceeds duration", () => {
    expect(interpolateCountUp(0, 100, 750, 500)).toBe(100);
  });

  it("returns a value strictly between start and end mid-animation", () => {
    const mid = interpolateCountUp(0, 100, 250, 500);
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(100);
  });

  it("returns the end value immediately when duration is 0", () => {
    expect(interpolateCountUp(10, 20, 0, 0)).toBe(20);
  });
});
