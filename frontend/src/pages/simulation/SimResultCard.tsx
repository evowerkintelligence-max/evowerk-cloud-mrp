import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { SendToReportButton } from "@/pages/analytics/shared";
import { BASELINE_FIELD_LABELS, METRIC_LABELS, deltaColor, fmtVal, resultMetricKeys, resultToReportText } from "./config";
import type { SimResult } from "./types";

export function SimResultCard({ result }: { result: SimResult }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{result.scenario_name}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <SendToReportButton spec={{ type: "text", text: resultToReportText(result) }} label={result.scenario_name} />
        {result.variance_source && (
          <div className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 py-1 text-xs text-muted-foreground">
            {result.variance_source === "dataset"
              ? "Confidence range: derived from your data"
              : "Confidence range: default estimate (no linked dataset)"}
          </div>
        )}
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Metric</TH>
                <TH>Baseline</TH>
                <TH>Projected</TH>
                {result.bands && <TH>Range (P10–P90)</TH>}
                <TH>Δ</TH>
              </TR>
            </THead>
            <TBody>
              {resultMetricKeys(result)
                .map((k) => {
                  const d = result.delta[k] ?? { absolute: 0, pct: 0, direction: "unchanged" };
                  const arrow = d.direction === "increase" ? "↑" : d.direction === "decrease" ? "↓" : "→";
                  return (
                    <TR key={k}>
                      <TD className="font-medium">{METRIC_LABELS[k]}</TD>
                      <TD>{fmtVal(k, result.baseline[k])}</TD>
                      <TD className="font-semibold">{fmtVal(k, result.projected[k])}</TD>
                      {result.bands && (
                        <TD className="text-muted-foreground">
                          {result.bands[k] ? `${fmtVal(k, result.bands[k].p10)} – ${fmtVal(k, result.bands[k].p90)}` : "—"}
                        </TD>
                      )}
                      <TD className={deltaColor(k, d.direction) + " font-semibold"}>
                        {arrow} {fmtVal(k, d.absolute)} ({d.pct > 0 ? "+" : ""}
                        {d.pct.toFixed(1)}%)
                      </TD>
                    </TR>
                  );
                })}
            </TBody>
          </Table>
        </TableWrap>

        <div className="space-y-2">
          {result.insights.map((insight, i) => (
            <div key={i} className="rounded-r-md border-l-4 border-primary bg-primary/5 px-4 py-2.5 text-sm leading-relaxed">
              {insight}
            </div>
          ))}
        </div>

        {result.sensitivity && result.sensitivity.ranked.length > 0 && (
          <div className="space-y-2">
            <div className="text-sm font-medium">
              What moves {METRIC_LABELS[result.sensitivity.target_metric] ?? result.sensitivity.target_metric} the most
            </div>
            {(() => {
              const ranked = result.sensitivity!.ranked;
              const maxImpact = Math.max(...ranked.map((r) => r.impact), 1);
              return ranked.map((r) => (
                <div key={r.parameter} className="flex items-center gap-3 text-sm">
                  <span className="w-40 shrink-0 text-muted-foreground">{BASELINE_FIELD_LABELS[r.parameter] ?? r.parameter}</span>
                  <div className="h-3 flex-1 rounded-full bg-muted">
                    <div className="h-3 rounded-full bg-primary" style={{ width: `${(r.impact / maxImpact) * 100}%` }} />
                  </div>
                  <span className="w-24 shrink-0 text-right text-xs text-muted-foreground">
                    {fmtVal(result.sensitivity!.target_metric, r.impact)}
                  </span>
                </div>
              ));
            })()}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
