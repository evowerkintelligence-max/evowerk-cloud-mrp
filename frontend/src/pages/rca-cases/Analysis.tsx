import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Field } from "@/components/Field";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { ColumnChecklist, autoMapColumns } from "@/pages/analytics/shared";
import type { RcaCandidate, RcaParetoBucket, RcaTimelineEvent } from "./types";
import { j, errorMessage, OEE_FIELDS, DATE_FIELD, DIMENSION_HINT } from "./types";

export function Analysis({ caseId, columns }: { caseId: string; columns: string[] }) {
  const qc = useQueryClient();
  const [oeeCols, setOeeCols] = useState<Record<string, string>>(() => ({
    ...{ planned_time_col: "", operating_time_col: "", total_units_col: "", good_units_col: "" },
    ...autoMapColumns(OEE_FIELDS, columns),
  }));
  const [dimensionCols, setDimensionCols] = useState<Set<string>>(
    () => new Set(columns.filter((c) => DIMENSION_HINT.test(c))),
  );
  const [dateCol, setDateCol] = useState(() => autoMapColumns([DATE_FIELD], columns).date_col ?? "");
  const [categoryCol, setCategoryCol] = useState("");
  const [valueCol, setValueCol] = useState("");
  const [addedKeys, setAddedKeys] = useState<Set<string>>(new Set());

  const evidence = useMutation({
    mutationFn: () =>
      j<{ overall_oee: number; candidates: RcaCandidate[]; candidates_total: number }>(
        apiFetch(`/rca-cases/cases/${caseId}/evidence`, {
          method: "POST",
          body: JSON.stringify({ ...oeeCols, dimension_cols: Array.from(dimensionCols), min_support_rows: 5 }),
        }),
      ),
  });

  const pareto = useMutation({
    mutationFn: () =>
      j<{ total: number; buckets: RcaParetoBucket[] }>(
        apiFetch(`/rca-cases/cases/${caseId}/pareto`, {
          method: "POST",
          body: JSON.stringify({ category_col: categoryCol, value_col: valueCol, top_n: 15 }),
        }),
      ),
  });

  const timeline = useMutation({
    mutationFn: () =>
      j<{ overall_oee: number; events: RcaTimelineEvent[] }>(
        apiFetch(`/rca-cases/cases/${caseId}/timeline`, {
          method: "POST",
          body: JSON.stringify({ ...oeeCols, date_col: dateCol, window_days: 3 }),
        }),
      ),
  });

  const addCause = useMutation({
    mutationFn: (payload: { key: string; text: string; evidence: Record<string, unknown>[] }) =>
      j(
        apiFetch(`/rca-cases/cases/${caseId}/nodes`, {
          method: "POST",
          body: JSON.stringify({ text: payload.text, node_type: "cause", evidence: payload.evidence }),
        }),
      ),
    onSuccess: async (_data, payload) => {
      setAddedKeys((prev) => new Set(prev).add(payload.key));
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["rca-nodes", caseId] }),
        qc.invalidateQueries({ queryKey: ["rca-case", caseId] }),
      ]);
    },
  });

  const evidenceReady = Boolean(oeeCols.planned_time_col && oeeCols.operating_time_col && oeeCols.total_units_col && oeeCols.good_units_col) && dimensionCols.size > 0;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Evidence engine</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">
            Ranks dimension values (machine, shift, operator…) that run worse than the overall OEE. Confidence combines
            how bad the drop is with how much production volume it represents, 0–100 — a huge drop on 2 rows out of
            20,000 scores low; a moderate drop across a third of production scores high.
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {OEE_FIELDS.map((f) => (
              <Field key={f.key} label={f.label}>
                <Select value={oeeCols[f.key]} onChange={(e) => setOeeCols({ ...oeeCols, [f.key]: e.target.value })}>
                  <option value="">Select…</option>
                  {columns.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </Field>
            ))}
          </div>
          <div className="space-y-2">
            <div className="text-sm font-medium">Dimension columns to check</div>
            <ColumnChecklist columns={columns} selected={dimensionCols} onChange={setDimensionCols} />
          </div>
          <Button disabled={!evidenceReady} loading={evidence.isPending} onClick={() => evidence.mutate()}>
            Rank candidate causes
          </Button>
          {evidence.isError && <Alert variant="destructive">{errorMessage(evidence.error)}</Alert>}
          {addCause.isError && <Alert variant="destructive">{errorMessage(addCause.error)}</Alert>}
          {evidence.isSuccess && (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Dimension</TH>
                    <TH>Value</TH>
                    <TH>OEE</TH>
                    <TH>Δ OEE</TH>
                    <TH>Support</TH>
                    <TH>Confidence</TH>
                    <TH></TH>
                  </TR>
                </THead>
                <TBody>
                  {evidence.data.candidates.map((c, i) => {
                    const key = `evidence-${c.dimension}-${c.label}`;
                    return (
                      <TR key={i}>
                        <TD>{c.dimension}</TD>
                        <TD className="font-medium">{c.label}</TD>
                        <TD>{c.oee}%</TD>
                        <TD className="text-rose-400">{c.delta_oee}</TD>
                        <TD>{c.support_pct}%</TD>
                        <TD>{c.confidence}</TD>
                        <TD>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={addedKeys.has(key)}
                            loading={addCause.isPending}
                            onClick={() =>
                              addCause.mutate({
                                key,
                                text: `${c.dimension}: ${c.label} (OEE ${c.oee}%, Δ${c.delta_oee}pts)`,
                                evidence: [
                                  {
                                    kind: "candidate",
                                    summary: `${c.label} runs ${Math.abs(c.delta_oee)}pts below overall OEE (confidence ${c.confidence})`,
                                    data: c,
                                  },
                                ],
                              })
                            }
                          >
                            {addedKeys.has(key) ? "Added" : "Add as cause"}
                          </Button>
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Pareto</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">
            Classic 80/20: sums the value column per category, sorted highest first, with a running cumulative % — the
            categories at the top of the list before it crosses ~80% are usually where to focus first.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Category column">
              <Select value={categoryCol} onChange={(e) => setCategoryCol(e.target.value)}>
                <option value="">Select…</option>
                {columns.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Value column">
              <Select value={valueCol} onChange={(e) => setValueCol(e.target.value)}>
                <option value="">Select…</option>
                {columns.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Button disabled={!categoryCol || !valueCol} loading={pareto.isPending} onClick={() => pareto.mutate()}>
            Run Pareto
          </Button>
          {pareto.isError && <Alert variant="destructive">{errorMessage(pareto.error)}</Alert>}
          {pareto.isSuccess && (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Category</TH>
                    <TH>Value</TH>
                    <TH>% of total</TH>
                    <TH>Cumulative %</TH>
                    <TH></TH>
                  </TR>
                </THead>
                <TBody>
                  {pareto.data.buckets.map((b, i) => {
                    const key = `pareto-${b.label}`;
                    return (
                      <TR key={i}>
                        <TD>{b.label}</TD>
                        <TD>{b.value.toLocaleString()}</TD>
                        <TD>{b.pct_of_total}%</TD>
                        <TD>{b.cumulative_pct}%</TD>
                        <TD>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={addedKeys.has(key) || b.label.startsWith("Other (")}
                            loading={addCause.isPending}
                            onClick={() =>
                              addCause.mutate({
                                key,
                                text: `${categoryCol}: ${b.label} (${b.pct_of_total}% of total)`,
                                evidence: [
                                  {
                                    kind: "pareto",
                                    summary: `${b.label} accounts for ${b.pct_of_total}% of total ${valueCol}`,
                                    data: b,
                                  },
                                ],
                              })
                            }
                          >
                            {addedKeys.has(key) ? "Added" : "Add as cause"}
                          </Button>
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Timeline</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">
            Shows daily OEE for a window of days around the single worst day, so you can spot whether the problem was a
            one-off event or a sustained slide.
          </p>
          <Field label="Date column" className="max-w-sm">
            <Select value={dateCol} onChange={(e) => setDateCol(e.target.value)}>
              <option value="">Select…</option>
              {columns.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          </Field>
          <Button
            disabled={!dateCol || !oeeCols.planned_time_col || !oeeCols.operating_time_col || !oeeCols.total_units_col || !oeeCols.good_units_col}
            loading={timeline.isPending}
            onClick={() => timeline.mutate()}
          >
            Show timeline around worst day
          </Button>
          {timeline.isError && <Alert variant="destructive">{errorMessage(timeline.error)}</Alert>}
          {timeline.isSuccess && (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Date</TH>
                    <TH>OEE</TH>
                    <TH>Δ vs overall</TH>
                    <TH>Rows</TH>
                  </TR>
                </THead>
                <TBody>
                  {timeline.data.events.map((e) => (
                    <TR key={e.date} className={e.is_center ? "bg-primary/5" : ""}>
                      <TD>{e.date}</TD>
                      <TD>{e.oee}%</TD>
                      <TD>{e.delta_oee}</TD>
                      <TD>{e.row_count}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
