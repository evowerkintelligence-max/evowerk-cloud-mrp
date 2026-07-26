import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createSaveScheduler } from "./saveScheduler";

const DELAY = 600;

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("createSaveScheduler", () => {
  it("fires a single scheduled save once after the delay", () => {
    const save = vi.fn();
    const scheduler = createSaveScheduler(save, DELAY);

    scheduler.schedule({ qty: "10" });
    expect(save).not.toHaveBeenCalled();

    vi.advanceTimersByTime(DELAY);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith({ qty: "10" });

    // Confirm it does not fire again on its own.
    vi.advanceTimersByTime(DELAY * 5);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("collapses rapid re-scheduling into one save carrying the latest payload", () => {
    const save = vi.fn();
    const scheduler = createSaveScheduler(save, DELAY);

    scheduler.schedule({ qty: "1" });
    vi.advanceTimersByTime(DELAY - 100);
    scheduler.schedule({ qty: "12" });
    vi.advanceTimersByTime(DELAY - 100);
    scheduler.schedule({ qty: "120" });

    // None of the earlier arms should have fired yet.
    expect(save).not.toHaveBeenCalled();

    vi.advanceTimersByTime(DELAY);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith({ qty: "120" });
  });

  it("flush saves immediately and cancels the pending timer so no second save fires", () => {
    const save = vi.fn();
    const scheduler = createSaveScheduler(save, DELAY);

    scheduler.schedule({ qty: "42" });
    scheduler.flush();
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith({ qty: "42" });

    // The original timer must not still be armed underneath the flush.
    vi.advanceTimersByTime(DELAY * 5);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("flush is a no-op when nothing is pending", () => {
    const save = vi.fn();
    const scheduler = createSaveScheduler(save, DELAY);

    scheduler.flush();
    expect(save).not.toHaveBeenCalled();
  });

  it("cancel discards the pending save without ever calling save", () => {
    const save = vi.fn();
    const scheduler = createSaveScheduler(save, DELAY);

    scheduler.schedule({ qty: "99" });
    scheduler.cancel();

    vi.advanceTimersByTime(DELAY * 5);
    expect(save).not.toHaveBeenCalled();
  });

  it("reports pending status correctly across schedule, flush and cancel", () => {
    const save = vi.fn();
    const scheduler = createSaveScheduler(save, DELAY);

    expect(scheduler.isPending()).toBe(false);

    scheduler.schedule({ qty: "5" });
    expect(scheduler.isPending()).toBe(true);

    vi.advanceTimersByTime(DELAY);
    expect(scheduler.isPending()).toBe(false);

    scheduler.schedule({ qty: "6" });
    expect(scheduler.isPending()).toBe(true);
    scheduler.cancel();
    expect(scheduler.isPending()).toBe(false);

    scheduler.schedule({ qty: "7" });
    expect(scheduler.isPending()).toBe(true);
    scheduler.flush();
    expect(scheduler.isPending()).toBe(false);
  });
});
