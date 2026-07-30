import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import {
  abcLabel,
  scenarioDeltaSentence,
  whyThisStatusSentence,
  xyzLabel,
  type InventoryAnalyticsLine,
  type ScenarioDelta,
} from "./types";

interface AnalyticsDetailProps {
  itemId: string;
  line: InventoryAnalyticsLine;
}

export function AnalyticsDetail({ itemId, line }: AnalyticsDetailProps) {
  const [showNumbers, setShowNumbers] = useState(false);
  const [leadTime, setLeadTime] = useState("");

  const scenario = useMutation({
    mutationFn: async (days: number): Promise<ScenarioDelta | undefined> => {
      const res = await apiFetch("/inventory/scenario-compare", {
        method: "POST",
        body: JSON.stringify({ lead_time_overrides: { [itemId]: days } }),
      });
      if (!res.ok) throw new Error("Failed to compute the scenario");
      const body = await res.json();
      return body.deltas[0] as ScenarioDelta | undefined;
    },
  });

  function checkScenario() {
    const days = Number(leadTime);
    if (leadTime !== "" && Number.isFinite(days) && days >= 0) scenario.mutate(days);
  }

  return (
    <div className="space-y-3 rounded-lg border border-border p-4">
      <p className="text-sm font-semibold">Why this status</p>
      <p className="text-sm">{whyThisStatusSentence(line)}</p>
      <Button type="button" variant="ghost" size="sm" onClick={() => setShowNumbers((v) => !v)}>
        {showNumbers ? "Hide the numbers" : "Show the numbers"}
      </Button>
      {showNumbers && (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-3">
          <div>
            <dt className="inline font-medium">Days of supply: </dt>
            <dd className="inline">{line.days_of_supply?.toFixed(1) ?? "—"}</dd>
          </div>
          <div>
            <dt className="inline font-medium">Reorder point: </dt>
            <dd className="inline">{line.reorder_point?.toFixed(1) ?? "—"}</dd>
          </div>
          <div>
            <dt className="inline font-medium">Safety stock: </dt>
            <dd className="inline">{line.safety_stock?.toFixed(1) ?? "—"}</dd>
          </div>
          <div>
            <dt className="inline font-medium">Value class: </dt>
            <dd className="inline">{line.abc_class ?? "—"} ({abcLabel(line.abc_class) ?? "unclassified"})</dd>
          </div>
          <div>
            <dt className="inline font-medium">Demand pattern: </dt>
            <dd className="inline">{line.xyz_class ?? "—"} ({xyzLabel(line.xyz_class) ?? "unclassified"})</dd>
          </div>
        </dl>
      )}

      <Field label="What if lead time were (days)" htmlFor="what-if-lead-time">
        <Input
          id="what-if-lead-time"
          type="number"
          min="0"
          step="1"
          value={leadTime}
          onChange={(e) => setLeadTime(e.target.value)}
          onBlur={checkScenario}
        />
      </Field>
      {scenario.isPending && <p className="text-xs text-muted-foreground">Checking…</p>}
      {scenario.data && <p className="text-sm">{scenarioDeltaSentence(scenario.data)}</p>}
      {scenario.isError && <p className="text-sm text-destructive">Couldn't compute that scenario.</p>}
    </div>
  );
}
