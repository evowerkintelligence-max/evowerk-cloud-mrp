import { describe, expect, it } from "vitest";
import { autoLayout, wouldCreateCycle } from "./stepLayout";

describe("autoLayout", () => {
  it("places a step with no dependencies in column 0", () => {
    const pos = autoLayout([{ id: "a", depends_on: [] }]);
    expect(pos.a).toEqual({ x: 0, y: 0 });
  });

  it("places a dependent step to the right of its dependency", () => {
    const pos = autoLayout([
      { id: "a", depends_on: [] },
      { id: "b", depends_on: ["a"] },
    ]);
    expect(pos.b.x).toBeGreaterThan(pos.a.x);
  });

  it("stacks independent steps in the same column at different rows", () => {
    const pos = autoLayout([
      { id: "a", depends_on: [] },
      { id: "b", depends_on: [] },
    ]);
    expect(pos.a.x).toBe(pos.b.x);
    expect(pos.a.y).not.toBe(pos.b.y);
  });

  it("places a step two levels deep two columns to the right", () => {
    const pos = autoLayout([
      { id: "a", depends_on: [] },
      { id: "b", depends_on: ["a"] },
      { id: "c", depends_on: ["b"] },
    ]);
    expect(pos.c.x).toBeGreaterThan(pos.b.x);
    expect(pos.b.x).toBeGreaterThan(pos.a.x);
  });

  it("does not infinite-loop on a cyclic input", () => {
    const pos = autoLayout([
      { id: "a", depends_on: ["b"] },
      { id: "b", depends_on: ["a"] },
    ]);
    expect(pos.a).toBeDefined();
    expect(pos.b).toBeDefined();
  });
});

describe("wouldCreateCycle", () => {
  it("is false for connecting two unrelated steps", () => {
    const steps = [
      { id: "a", depends_on: [] },
      { id: "b", depends_on: [] },
    ];
    expect(wouldCreateCycle(steps, "b", "a")).toBe(false);
  });

  it("is true when the source already (transitively) depends on the target", () => {
    // a <- b <- c (c depends_on b, b depends_on a). Connecting a to depend on
    // c would close the loop a -> c -> b -> a.
    const steps = [
      { id: "a", depends_on: [] },
      { id: "b", depends_on: ["a"] },
      { id: "c", depends_on: ["b"] },
    ];
    expect(wouldCreateCycle(steps, "a", "c")).toBe(true);
  });

  it("is false for a direct dependency that does not close a loop", () => {
    const steps = [
      { id: "a", depends_on: [] },
      { id: "b", depends_on: [] },
    ];
    expect(wouldCreateCycle(steps, "b", "a")).toBe(false);
  });
});
