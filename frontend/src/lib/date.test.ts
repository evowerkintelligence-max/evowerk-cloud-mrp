import { afterEach, describe, expect, it, vi } from "vitest";
import { plusDays, today } from "./date";

afterEach(() => vi.useRealTimers());

describe("date helpers", () => {
  it("today is the ISO calendar date", () => {
    vi.useFakeTimers({ now: new Date("2026-03-10T12:00:00Z") });
    expect(today()).toBe("2026-03-10");
  });

  it("plusDays rolls over month ends", () => {
    vi.useFakeTimers({ now: new Date("2026-01-30T12:00:00Z") });
    expect(plusDays(3)).toBe("2026-02-02");
    expect(plusDays(-30)).toBe("2025-12-31");
  });
});
