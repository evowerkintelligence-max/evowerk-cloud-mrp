import { asUtcMs, resolveShiftForTime, type ScheduleItem } from "../analytics/shared";

export interface CaptureEventItem {
  operator_id: string;
  operator_name: string;
  machine_id: string;
  machine_name: string;
  started_at: string;
  ended_at: string | null;
}

export const CAPTURE_EVENT_DAY_OPTIONS = [7, 30, 90];
export const UNASSIGNED_SHIFT = "Unassigned";
// ponytail: a machine's next scan is what normally closes an event
// (open_capture_event); if that scan never comes (shift ended, operator
// walked off), the event stays open forever. Treat one this old as
// abandoned rather than still running - caps its time contribution and
// drops it from "on floor" - instead of growing unbounded.
export const STALE_OPEN_EVENT_MS = 24 * 60 * 60 * 1000;

export interface OperatorShiftStat {
  operatorId: string;
  operatorName: string;
  shift: string;
  totalMinutes: number;
  checkins: number;
  machines: Set<string>;
}

// Buckets each capture event by its start time (never split across a shift
// boundary) and sums duration/check-ins/distinct machines per operator per
// shift. An open event (ended_at null) counts as running until now, capped
// at STALE_OPEN_EVENT_MS so a missed scan-out doesn't grow forever.
export function aggregateByOperatorShift(events: CaptureEventItem[], schedule: ScheduleItem[]): OperatorShiftStat[] {
  const now = Date.now();
  const byKey = new Map<string, OperatorShiftStat>();
  for (const e of events) {
    const started = asUtcMs(e.started_at);
    const ended = e.ended_at ? asUtcMs(e.ended_at) : Math.min(now, started + STALE_OPEN_EVENT_MS);
    const minutes = Math.max(0, (ended - started) / 60000);
    const resolved = schedule.length > 0 ? resolveShiftForTime(schedule, new Date(started)) : null;
    const shift = resolved?.shift ?? UNASSIGNED_SHIFT;
    const key = `${e.operator_id}::${shift}`;
    let stat = byKey.get(key);
    if (!stat) {
      stat = { operatorId: e.operator_id, operatorName: e.operator_name, shift, totalMinutes: 0, checkins: 0, machines: new Set() };
      byKey.set(key, stat);
    }
    stat.totalMinutes += minutes;
    stat.checkins += 1;
    // ponytail: dedupe by name (not id) so the Machines column can render the
    // list of distinct machines directly instead of a bare count that hides
    // which machines were involved.
    stat.machines.add(e.machine_name);
  }
  return Array.from(byKey.values());
}

export function formatMinutes(total: number): string {
  const t = Math.round(total);
  return `${Math.floor(t / 60)}h ${t % 60}m`;
}
