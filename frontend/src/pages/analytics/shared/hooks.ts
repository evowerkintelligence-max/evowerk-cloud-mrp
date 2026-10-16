import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../../api/client";

// ponytail: naive (no "Z"/offset) ISO string under SQLite (aiosqlite drops
// tzinfo on DateTime(timezone=True) columns even though the value is UTC;
// Postgres in prod keeps the offset) - new Date(...) would otherwise parse a
// naive string as local time and throw the elapsed clock off by the
// browser's UTC offset. Force UTC when no offset is present.
export function asUtcMs(iso: string): number {
  return new Date(/Z$|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`).getTime();
}

export function useElapsed(startedAt: string | undefined): string {
  const [elapsed, setElapsed] = useState("00:00:00");
  useEffect(() => {
    if (!startedAt) return;
    const started = asUtcMs(startedAt);
    const tick = () => {
      const s = Math.max(0, Math.floor((Date.now() - started) / 1000));
      const h = String(Math.floor(s / 3600)).padStart(2, "0");
      const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
      const sec = String(s % 60).padStart(2, "0");
      setElapsed(`${h}:${m}:${sec}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);
  return elapsed;
}

export interface ScheduleItem {
  shift_label: string;
  start_time: string;
  end_time: string;
}

export function useShiftSchedule(enabled = true) {
  return useQuery<ScheduleItem[]>({
    queryKey: ["shift-schedule"],
    queryFn: async () => {
      const res = await apiFetch("/analytics/shifts/schedule");
      if (!res.ok) throw new Error("Failed to load shift schedule");
      return res.json();
    },
    enabled,
  });
}

function minutesOfDay(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

// Matches `at` against each configured shift window, handling the case where
// a shift crosses midnight (end_time < start_time). Used both for "now" (the
// Shift Comparison page's current-shift resolution) and for bucketing any
// historical timestamp — e.g. an operator capture event — into a shift.
export function resolveShiftForTime(schedule: ScheduleItem[], at: Date): { shift: string; minutesLeft: number } | null {
  const atMin = at.getHours() * 60 + at.getMinutes();
  for (const s of schedule) {
    const start = minutesOfDay(s.start_time);
    const end = minutesOfDay(s.end_time);
    const wraps = end <= start;
    const within = wraps ? atMin >= start || atMin < end : atMin >= start && atMin < end;
    if (within) {
      const minutesLeft = wraps ? (atMin >= start ? end + 24 * 60 - atMin : end - atMin) : end - atMin;
      return { shift: s.shift_label, minutesLeft };
    }
  }
  return null;
}
