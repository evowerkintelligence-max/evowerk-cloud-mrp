import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Field } from "@/components/Field";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Checkbox } from "@/components/ui/checkbox";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { fmt } from "../analytics/shared";
import { summarizeConflicts, type ExistingForecastLite } from "./suggestLogic";

interface SuggestedPoint {
  period_start: string;
  quantity: string;
  lower: string | null;
  upper: string | null;
}

interface SuggestedItem {
  item_id: string;
  item_sku: string;
  item_name: string;
  method_used: string;
  history_points: number;
  points: SuggestedPoint[];
}

interface BulkSuggestedResponse {
  window_start: string;
  window_end: string;
  items: SuggestedItem[];
}

interface AcceptResponse {
  items: number;
  deleted: number;
  created: number;
  updated: number;
  skipped: { item_id: string; item_sku: string }[];
}

const METHOD_LABEL: Record<string, string> = {
  moving_avg: "moving average",
  exp_smoothing: "exp. smoothing",
};

export function SuggestFromHistoryCard() {
  const queryClient = useQueryClient();
  const [horizonDays, setHorizonDays] = useState("30");
  const [error, setError] = useState<string | null>(null);
  // Checkbox overrides on top of the conflict-derived defaults; reset per compute.
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [result, setResult] = useState<AcceptResponse | null>(null);

  const forecasts = useQuery<ExistingForecastLite[]>({
    queryKey: ["forecasts"],
    queryFn: async () => {
      const res = await apiFetch("/forecasts");
      if (!res.ok) throw new Error("Failed to load forecasts");
      return res.json();
    },
  });

  const suggestions = useQuery<BulkSuggestedResponse>({
    queryKey: ["suggested-forecasts", horizonDays],
    enabled: false, // O(items) computation; the planner triggers it deliberately
    queryFn: async () => {
      const res = await apiFetch(`/forecasts/suggested?horizon_days=${horizonDays}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to compute suggestions");
      }
      return res.json();
    },
  });

  function onCompute() {
    setError(null);
    setResult(null);
    setOverrides({});
    void suggestions.refetch();
  }

  const data = suggestions.data;
  const existing = forecasts.data ?? [];

  function isChecked(item: SuggestedItem): boolean {
    if (item.item_id in overrides) return overrides[item.item_id];
    if (!data) return false;
    return summarizeConflicts(item.item_id, data.window_start, data.window_end, existing)
      .defaultChecked;
  }

  const checkedIds = (data?.items ?? []).filter(isChecked).map((s) => s.item_id);

  const accept = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/forecasts/suggested/accept", {
        method: "POST",
        body: JSON.stringify({ item_ids: checkedIds, horizon_days: Number(horizonDays) }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to accept suggestions");
      }
      return (await res.json()) as AcceptResponse;
    },
    onSuccess: (r) => {
      setResult(r);
      setOverrides({});
      queryClient.invalidateQueries({ queryKey: ["forecasts"] });
      queryClient.invalidateQueries({ queryKey: ["mrp-plan"] });
      void suggestions.refetch();
    },
    onError: (e: Error) => setError(e.message),
  });

  function onAccept() {
    setError(null);
    setResult(null);
    if (checkedIds.length === 0) {
      setError("Select at least one item to accept");
      return;
    }
    accept.mutate();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Suggest from history</CardTitle>
        <CardDescription>
          Project demand from each item's real consumption history, then accept the projections you
          agree with as forecasts. Accepting an item replaces its existing forecast rows inside the
          suggestion window so demand is never double counted.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {error && <Alert variant="destructive">{error}</Alert>}

        <div className="grid gap-4 sm:grid-cols-[10rem_auto] sm:items-end">
          <Field label="Horizon (days)" htmlFor="sg-horizon">
            <Input
              id="sg-horizon"
              type="number"
              min="1"
              max="365"
              value={horizonDays}
              onChange={(e) => setHorizonDays(e.target.value)}
            />
          </Field>
          <div>
            <Button onClick={onCompute} loading={suggestions.isFetching}>
              Compute suggestions
            </Button>
          </div>
        </div>

        {suggestions.isError && (
          <Alert variant="destructive">{(suggestions.error as Error).message}</Alert>
        )}

        {result && (
          <Alert variant="success">
            Accepted {result.items} item{result.items === 1 ? "" : "s"}: {result.created} created,{" "}
            {result.updated} updated, {result.deleted} replaced.
            {result.skipped.length > 0 &&
              ` Skipped (no history): ${result.skipped.map((s) => s.item_sku).join(", ")}.`}
          </Alert>
        )}

        {data && data.items.length === 0 && (
          <p className="text-sm text-muted-foreground">No items have consumption history yet.</p>
        )}

        {data && data.items.length > 0 && (
          <>
            <p className="text-sm text-muted-foreground">
              Window {data.window_start} to {data.window_end}. Weekly quantities; range shown when
              history supports one.
            </p>
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Accept?</TH>
                    <TH>Item</TH>
                    <TH>Method</TH>
                    <TH>History</TH>
                    <TH>Week starting</TH>
                    <TH>Quantity</TH>
                    <TH>Range</TH>
                  </TR>
                </THead>
                <TBody>
                  {data.items.map((s) => {
                    const conflict = summarizeConflicts(
                      s.item_id,
                      data.window_start,
                      data.window_end,
                      existing,
                    );
                    return s.points.map((p, idx) => (
                      <TR key={`${s.item_id}-${p.period_start}`}>
                        {idx === 0 && (
                          <>
                            <TD rowSpan={s.points.length}>
                              <Checkbox
                                checked={isChecked(s)}
                                onChange={(e) =>
                                  setOverrides((prev) => ({
                                    ...prev,
                                    [s.item_id]: e.target.checked,
                                  }))
                                }
                              />
                            </TD>
                            <TD rowSpan={s.points.length} className="font-medium">
                              {s.item_sku} - {s.item_name}
                              {conflict.total > 0 && (
                                <div className="mt-1 text-xs font-normal text-muted-foreground">
                                  replaces {conflict.total} existing row
                                  {conflict.total === 1 ? "" : "s"}
                                  {conflict.manual > 0 && ` (${conflict.manual} manual)`}
                                </div>
                              )}
                            </TD>
                            <TD rowSpan={s.points.length}>
                              <Badge variant="neutral">
                                {METHOD_LABEL[s.method_used] ?? s.method_used}
                              </Badge>
                            </TD>
                            <TD rowSpan={s.points.length}>{s.history_points} days</TD>
                          </>
                        )}
                        <TD>{p.period_start}</TD>
                        <TD className="font-semibold">{fmt(Number(p.quantity))}</TD>
                        <TD className="text-muted-foreground">
                          {p.lower !== null && p.upper !== null
                            ? `${fmt(Number(p.lower))} to ${fmt(Number(p.upper))}`
                            : "—"}
                        </TD>
                      </TR>
                    ));
                  })}
                </TBody>
              </Table>
            </TableWrap>
            <Button onClick={onAccept} loading={accept.isPending}>
              Accept {checkedIds.length} selected item{checkedIds.length === 1 ? "" : "s"}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}
