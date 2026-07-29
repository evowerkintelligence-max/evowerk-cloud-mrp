import { describe, expect, it } from "vitest";
import { resolveShiftForTime, type ScheduleItem } from "./shared";

const SCHEDULE: ScheduleItem[] = [
  { shift_label: "Day", start_time: "08:00", end_time: "20:00" },
  { shift_label: "Night", start_time: "20:00", end_time: "08:00" },
];

describe("resolveShiftForTime", () => {
  it("resolves a time inside a normal (non-wrapping) shift window", () => {
    const at = new Date(2026, 0, 1, 14, 30);
    const result = resolveShiftForTime(SCHEDULE, at);
    expect(result?.shift).toBe("Day");
    expect(result?.minutesLeft).toBe(330); // 20:00 - 14:30 = 5h30m
  });

  it("resolves a time inside a midnight-wrapping shift window", () => {
    const at = new Date(2026, 0, 1, 2, 0);
    const result = resolveShiftForTime(SCHEDULE, at);
    expect(result?.shift).toBe("Night");
    expect(result?.minutesLeft).toBe(360); // 08:00 - 02:00 = 6h
  });

  it("resolves the exact boundary instant to the shift that is starting, not ending", () => {
    const at = new Date(2026, 0, 1, 20, 0);
    const result = resolveShiftForTime(SCHEDULE, at);
    expect(result?.shift).toBe("Night");
  });

  it("returns null when no shift window covers the time", () => {
    const result = resolveShiftForTime([{ shift_label: "Day", start_time: "08:00", end_time: "12:00" }], new Date(2026, 0, 1, 15, 0));
    expect(result).toBeNull();
  });

  it("returns null for an empty schedule", () => {
    expect(resolveShiftForTime([], new Date())).toBeNull();
  });
});
