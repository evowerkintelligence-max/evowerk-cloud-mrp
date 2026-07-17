import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { RunResultsPanel } from "../RunResultsPanel";
import { useAutoMLRun } from "../api";

// Keys already rendered elsewhere (headline, notes) or consumed by
// RunResultsPanel - everything else in `meta` is domain-specific
// (scrap_summary, downtime_summary, predictive_result, ...) and gets a
// generic best-effort section below.
const KNOWN_META_KEYS = new Set(["explanation_sentence", "recommendation", "notes", "feature_columns", "schema"]);

function titleCase(key: string) {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(4);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** Best-effort generic rendering of one domain-specific `meta` section, e.g.
 * `scrap_summary` / `downtime_summary` (flat objects) - array-of-objects
 * shapes render as a table, anything else falls back to a flat key/value
 * list so this never breaks on a template's meta shape it doesn't know about. */
function MetaSection({ title, value }: { title: string; value: unknown }) {
  const rows = Array.isArray(value) ? value : null;
  const flat = !rows && value && typeof value === "object" ? (value as Record<string, unknown>) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{titleCase(title)}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows && rows.length > 0 && typeof rows[0] === "object" ? (
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  {Object.keys(rows[0] as object).map((k) => (
                    <TH key={k}>{titleCase(k)}</TH>
                  ))}
                </TR>
              </THead>
              <TBody>
                {rows.map((row, i) => (
                  <TR key={i}>
                    {Object.keys(rows[0] as object).map((k) => (
                      <TD key={k}>{formatValue((row as Record<string, unknown>)[k])}</TD>
                    ))}
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
        ) : flat ? (
          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(flat).map(([k, v]) => (
              <div key={k}>
                <dt className="text-xs text-muted-foreground">{titleCase(k)}</dt>
                <dd className="text-sm font-medium">{formatValue(v)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">{formatValue(value)}</p>
        )}
      </CardContent>
    </Card>
  );
}

export function ResultsView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const run = useAutoMLRun(id);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const runData = run.data;

  if (run.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (!runData) return <Alert variant="destructive">Run not found.</Alert>;

  const isPending = runData.status === "queued" || runData.status === "running";
  const isFailed = runData.status === "failed";
  const isDone = runData.status === "success";

  const meta = runData.meta ?? {};
  // These land here once the explainability track (automl_explain_nl.py) ships -
  // may be absent/null on runs created before then, so render conditionally.
  const explanationSentence = typeof meta.explanation_sentence === "string" ? meta.explanation_sentence : null;
  const recommendation = typeof meta.recommendation === "string" ? meta.recommendation : null;
  const domainSections = Object.entries(meta).filter(([k, v]) => !KNOWN_META_KEYS.has(k) && v !== null && v !== undefined);

  return (
    <div className="space-y-6">
      <PageHeader
        title={runData.name}
        description="Guided AutoML result"
        actions={<Button variant="secondary" onClick={() => navigate("/automl")}>New guided run</Button>}
      />

      {isPending && (
        <Card className="p-6 text-sm text-muted-foreground">
          Training in progress ({runData.status})… this page updates automatically.
        </Card>
      )}

      {isFailed && <Alert variant="destructive">{runData.error ?? "Training failed."}</Alert>}

      {isDone && (
        <>
          {(explanationSentence || recommendation) && (
            <Card className="border-primary/40 bg-primary/5 p-5">
              {explanationSentence && <p className="text-lg font-semibold">{explanationSentence}</p>}
              {recommendation && <p className="mt-2 text-sm text-muted-foreground">{recommendation}</p>}
            </Card>
          )}

          {domainSections.map(([key, value]) => (
            <MetaSection key={key} title={key} value={value} />
          ))}

          <div>
            <Button variant="secondary" onClick={() => setShowAdvanced((v) => !v)}>
              {showAdvanced ? "Hide" : "Show"} advanced (leaderboard, SHAP, drift…)
            </Button>
          </div>
          {showAdvanced && <RunResultsPanel runData={runData} />}
        </>
      )}
    </div>
  );
}
