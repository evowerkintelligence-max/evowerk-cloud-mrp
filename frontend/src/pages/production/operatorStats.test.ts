import { describe, expect, it } from "vitest";
import { UNASSIGNED_SHIFT, aggregateByOperatorShift, formatMinutes } from "./operatorStats";

describe("operatorStats", () => {
  it("formats minutes as hours and minutes", () => {
    expect(formatMinutes(125.4)).toBe("2h 5m");
  });

  it("sums time, check-ins and distinct machines per operator when no schedule exists", () => {
    const stats = aggregateByOperatorShift(
      [
        { operator_id: "o1", operator_name: "Ana", machine_id: "m1", machine_name: "Lathe", started_at: "2026-01-01T08:00:00Z", ended_at: "2026-01-01T09:00:00Z" },
        { operator_id: "o1", operator_name: "Ana", machine_id: "m1", machine_name: "Lathe", started_at: "2026-01-01T10:00:00Z", ended_at: "2026-01-01T10:30:00Z" },
      ],
      [],
    );
    expect(stats).toHaveLength(1);
    expect(stats[0]).toMatchObject({ operatorId: "o1", shift: UNASSIGNED_SHIFT, totalMinutes: 90, checkins: 2 });
    expect([...stats[0].machines]).toEqual(["Lathe"]);
  });
});
