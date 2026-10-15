import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useCurrentUser } from "@/lib/currentUser";
import { C, EChart, asUtcMs, rankedBarOption, useShiftSchedule } from "../analytics/shared";
import {
  CAPTURE_EVENT_DAY_OPTIONS,
  STALE_OPEN_EVENT_MS,
  UNASSIGNED_SHIFT,
  aggregateByOperatorShift,
  formatMinutes,
  type CaptureEventItem,
} from "./operatorStats";

function useOperatorCaptureEvents(days: number) {
  return useQuery<CaptureEventItem[]>({
    queryKey: ["operator-capture-events", days],
    queryFn: async () => {
      const res = await apiFetch(`/operators/capture-events?days=${days}`);
      if (!res.ok) throw new Error("Failed to load check-in history");
      return res.json();
    },
  });
}

export function OperatorPerformancePanel() {
  const [days, setDays] = useState(7);
  const [shift, setShift] = useState("");
  const org = useCurrentUser();
  const hasShifts = (org.data?.enabled_features ?? []).includes("shifts");
  const schedule = useShiftSchedule(hasShifts);
  const events = useOperatorCaptureEvents(days);

  // The org may be entitled to "production" (this card) without "shifts" -
  // /analytics/shifts/schedule 403s for those orgs, so don't even ask for it,
  // and treat the schedule as empty rather than surfacing that 403.
  const scheduleRows = hasShifts ? schedule.data ?? [] : [];
  const eventRows = events.data ?? [];
  const stats = aggregateByOperatorShift(eventRows, scheduleRows);
  const shifts = Array.from(new Set(stats.map((s) => s.shift))).sort();
  const activeShift = shifts.includes(shift) ? shift : shifts[0] ?? "";
  const rowsForShift = stats.filter((s) => s.shift === activeShift).sort((a, b) => b.totalMinutes - a.totalMinutes);
  const hasUnassigned = stats.some((s) => s.shift === UNASSIGNED_SHIFT);

  const onFloor = new Map<string, string>();
  for (const e of eventRows)
    if (e.ended_at === null && Date.now() - asUtcMs(e.started_at) < STALE_OPEN_EVENT_MS) {
      onFloor.set(e.operator_id, e.machine_name);
    }

  const sortedStats = [...stats].sort(
    (a, b) => a.operatorName.localeCompare(b.operatorName) || a.shift.localeCompare(b.shift),
  );

  return (
    <Card className="mt-8">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Operator performance by shift</CardTitle>
        <Select value={String(days)} onChange={(e) => setDays(Number(e.target.value))} className="w-32">
          {CAPTURE_EVENT_DAY_OPTIONS.map((d) => (
            <option key={d} value={d}>
              Last {d} days
            </option>
          ))}
        </Select>
      </CardHeader>
      <CardContent className="space-y-4">
        {eventRows.length > 0 &&
          (!hasShifts ? (
            <Alert variant="warning">
              Check-ins are grouped as "{UNASSIGNED_SHIFT}" — shift breakdowns aren't available on your plan.
            </Alert>
          ) : scheduleRows.length === 0 ? (
            <Alert variant="warning">
              No shift schedule configured — check-ins are grouped as "{UNASSIGNED_SHIFT}". Set one up on the Shift
              Comparison page (Analytics → Shift Comparison) to break this down by shift.
            </Alert>
          ) : (
            hasUnassigned && (
              <Alert variant="warning">
                Some check-ins fell outside every configured shift window and are grouped as "{UNASSIGNED_SHIFT}".
              </Alert>
            )
          ))}
        {!events.isSuccess ? (
          <p className="text-sm text-muted-foreground">
            {events.isError ? "Failed to load check-in history." : "Loading…"}
          </p>
        ) : eventRows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No check-ins in the last {days} days.</p>
        ) : (
          <>
            <Select value={activeShift} onChange={(e) => setShift(e.target.value)} className="w-40">
              {shifts.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
            {rowsForShift.length > 0 && (
              <EChart
                height={Math.max(120, rowsForShift.length * 36 + 20)}
                option={rankedBarOption({
                  categories: rowsForShift.map((r) => r.operatorName),
                  values: rowsForShift.map((r) => Math.round((r.totalMinutes / 60) * 10) / 10),
                  colorOf: () => C.blue,
                  formatter: (v) => `${v} h`,
                })}
              />
            )}
            <p className="text-xs text-muted-foreground">
              The chart above shows the selected shift; this table lists every shift.
            </p>
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Operator</TH>
                    <TH>Shift</TH>
                    <TH>Total time</TH>
                    <TH>Check-ins</TH>
                    <TH>Machines</TH>
                  </TR>
                </THead>
                <TBody>
                  {sortedStats.map((s) => (
                    <TR key={`${s.operatorId}::${s.shift}`}>
                      <TD className="font-medium">
                        {s.operatorName}
                        {onFloor.has(s.operatorId) && (
                          <Badge variant="success" className="ml-2">
                            On floor — {onFloor.get(s.operatorId)}
                          </Badge>
                        )}
                      </TD>
                      <TD>{s.shift}</TD>
                      <TD>{formatMinutes(s.totalMinutes)}</TD>
                      <TD>{s.checkins}</TD>
                      <TD>{Array.from(s.machines).join(", ")}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          </>
        )}
      </CardContent>
    </Card>
  );
}
