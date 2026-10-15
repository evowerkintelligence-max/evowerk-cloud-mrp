import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "../../../api/client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { fmt } from "../shared";
import type { DatasetDetail } from "./types";

interface ProcessActivity {
  name: string;
  cases: number;
  executions: number;
  avg_duration_min: number;
  avg_wait_min: number;
}

interface ProcessBottleneck extends ProcessActivity {
  severity: string;
}

interface ProcessMiningResponse {
  summary: {
    total_cases: number;
    total_activities: number;
    avg_case_duration_min: number;
    longest_bottleneck: string;
  };
  activities: ProcessActivity[];
  bottlenecks: ProcessBottleneck[];
  conformance: {
    reference_sequence: string[];
    conformance_pct: number;
    conforming_cases: number;
    deviating_cases: number;
  };
  warnings: string[];
}

export function ProcessMiningPanel({ d }: { d: DatasetDetail }) {
  const names = d.columns.map((c) => c.name);
  const [caseColumn, setCaseColumn] = useState("");
  const [activityColumn, setActivityColumn] = useState("");
  const [timestampColumn, setTimestampColumn] = useState("");

  const process = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/datasets/${d.id}/process-mining/analyze`, {
        method: "POST",
        body: JSON.stringify({
          case_column: caseColumn,
          activity_column: activityColumn,
          timestamp_column: timestampColumn,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Process mining analysis failed");
      return res.json() as Promise<ProcessMiningResponse>;
    },
  });

  const ready = Boolean(caseColumn && activityColumn && timestampColumn);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Process mining</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Case/job/order ID (required)">
              <Select value={caseColumn} onChange={(e) => setCaseColumn(e.target.value)}>
                <option value="">Select column…</option>
                {names.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Activity/step name (required)">
              <Select value={activityColumn} onChange={(e) => setActivityColumn(e.target.value)}>
                <option value="">Select column…</option>
                {names.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Event timestamp (required)">
              <Select value={timestampColumn} onChange={(e) => setTimestampColumn(e.target.value)}>
                <option value="">Select column…</option>
                {names.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Button disabled={!ready} loading={process.isPending} onClick={() => process.mutate()}>
            Discover process
          </Button>
          {process.isError && <Alert variant="destructive">{(process.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {process.data && (
        <>
          <Card>
            <CardContent className="space-y-2 pt-6 text-sm">
              <p>
                {fmt(process.data.summary.total_cases)} cases · {fmt(process.data.summary.total_activities)}{" "}
                activities · avg case duration {process.data.summary.avg_case_duration_min} min · biggest
                bottleneck: {process.data.summary.longest_bottleneck}
              </p>
              <p>
                Conformance to majority path ({process.data.conformance.reference_sequence.join(" → ")}):{" "}
                {process.data.conformance.conformance_pct}% ({process.data.conformance.conforming_cases} conforming,{" "}
                {process.data.conformance.deviating_cases} deviating)
              </p>
            </CardContent>
          </Card>

          {process.data.warnings.map((w) => (
            <Alert key={w} variant="warning">
              {w}
            </Alert>
          ))}

          {process.data.bottlenecks.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Bottlenecks (highest avg wait time)</CardTitle>
              </CardHeader>
              <CardContent>
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Activity</TH>
                        <TH>Avg wait (min)</TH>
                        <TH>Severity</TH>
                        <TH>Cases</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {process.data.bottlenecks.map((b) => (
                        <TR key={b.name}>
                          <TD className="font-medium">{b.name}</TD>
                          <TD>{b.avg_wait_min}</TD>
                          <TD>{b.severity}</TD>
                          <TD>{fmt(b.cases)}</TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
