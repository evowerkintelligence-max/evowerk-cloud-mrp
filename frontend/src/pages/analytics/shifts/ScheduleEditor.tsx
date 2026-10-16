import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../../api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { useShiftSchedule } from "../shared";

// Weekly-recurring start/end time per shift label — powers the "current
// shift" resolution for the handover and hourly-pace cards below.
export function ScheduleEditor({ shiftLabels }: { shiftLabels: string[] }) {
  const qc = useQueryClient();
  const schedule = useShiftSchedule();
  const byLabel = new Map((schedule.data ?? []).map((s) => [s.shift_label, s]));
  const [form, setForm] = useState<Record<string, { start: string; end: string }>>({});

  const save = useMutation({
    mutationFn: async () => {
      const items = shiftLabels
        .map((label) => ({
          shift_label: label,
          start_time: form[label]?.start ?? byLabel.get(label)?.start_time ?? "",
          end_time: form[label]?.end ?? byLabel.get(label)?.end_time ?? "",
        }))
        .filter((i) => i.start_time && i.end_time);
      const res = await apiFetch("/analytics/shifts/schedule", { method: "PUT", body: JSON.stringify(items) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to save shift schedule");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["shift-schedule"] }),
  });

  if (shiftLabels.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Shift schedule</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Set each shift's start/end time to resolve a "current shift" for the handover and hourly-pace views below.
        </p>
        {shiftLabels.map((label) => (
          <div key={label} className="flex flex-wrap items-end gap-3">
            <div className="w-24 text-sm font-medium">{label}</div>
            <Field label="Start">
              <Input
                type="time"
                value={form[label]?.start ?? byLabel.get(label)?.start_time ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    [label]: { start: e.target.value, end: form[label]?.end ?? byLabel.get(label)?.end_time ?? "" },
                  })
                }
              />
            </Field>
            <Field label="End">
              <Input
                type="time"
                value={form[label]?.end ?? byLabel.get(label)?.end_time ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    [label]: { start: form[label]?.start ?? byLabel.get(label)?.start_time ?? "", end: e.target.value },
                  })
                }
              />
            </Field>
          </div>
        ))}
        <Button variant="secondary" size="sm" loading={save.isPending} onClick={() => save.mutate()}>
          Save schedule
        </Button>
        {save.isError && <p className="text-sm text-destructive">{(save.error as Error).message}</p>}
      </CardContent>
    </Card>
  );
}
