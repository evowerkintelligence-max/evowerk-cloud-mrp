import { FormEvent, useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import type { EChartsOption } from "echarts";
import { Send, Sparkles } from "lucide-react";
import { apiFetch } from "../api/client";
import { AXIS_LABEL, AXIS_LINE, C, EChart, SPLIT_LINE, TOOLTIP, useDatasets } from "./analytics/shared";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { cn } from "@/lib/utils";

// Two backends power this one page:
//  - "Live production data" -> /ai/ask, a zero-LLM rules engine over this
//    org's own inventory/BOM/PO tables (compare/forecast/scenario/health).
//  - Any dataset selection -> /data-chat/message, general LLM chat (with
//    conversation history, charts, tables) over uploaded datasets. See
//    backend/app/services/{ai_insights,data_chat}.py for why these two stay
//    separate rather than one endpoint: different data sources, different
//    answer shapes.
const LIVE_DATA = "";
const ALL_DATASETS = "__all__";

interface AskResponse {
  question: string;
  intents: string[];
  narrative: string;
  warnings: string[];
}

interface ChartSpec {
  title: string;
  type: string;
  x_labels: string[];
  series: { name: string; data: number[] }[];
}

interface TableSpec {
  columns: string[];
  rows: unknown[][];
}

interface ChatResponse {
  answer: string;
  chart: ChartSpec | null;
  table: TableSpec | null;
  follow_ups: string[];
  datasets_used: string[];
}

type Turn =
  | { role: "user"; text: string }
  | { role: "assistant"; mode: "live"; text: string; intents: string[]; warnings: string[] }
  | { role: "assistant"; mode: "chat"; text: string; chart: ChartSpec | null; table: TableSpec | null; followUps: string[]; datasetsUsed: string[] };

const LIVE_EXAMPLES = [
  "how's my inventory looking?",
  "why are items low, compare by supplier",
  "forecast expected demand for WIDGET-1",
  "what if WIDGET-1's lead time were 10 days?",
];

function chartOption(spec: ChartSpec): EChartsOption {
  const palette = [C.blue, C.green, C.amber, C.violet, C.cyan, C.pink];
  if (spec.type === "pie") {
    const s0 = spec.series[0];
    return {
      tooltip: TOOLTIP,
      legend: { textStyle: { color: "#64748b", fontSize: 11 }, bottom: 0 },
      series: [
        {
          type: "pie",
          radius: ["40%", "70%"],
          data: spec.x_labels.map((label, i) => ({
            name: label,
            value: s0?.data?.[i] ?? 0,
            itemStyle: { color: palette[i % palette.length] },
          })),
          label: { color: "#94a3b8", fontSize: 11 },
        },
      ],
    };
  }
  return {
    tooltip: TOOLTIP,
    legend: spec.series.length > 1 ? { textStyle: { color: "#64748b", fontSize: 11 }, top: 0 } : undefined,
    grid: { top: spec.series.length > 1 ? 32 : 12, right: 16, bottom: 32, left: 48 },
    xAxis: { type: "category", data: spec.x_labels, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE },
    yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: spec.series.map((s, i) => ({
      name: s.name,
      type: spec.type === "line" ? "line" : "bar",
      data: s.data,
      smooth: spec.type === "line",
      itemStyle: { color: palette[i % palette.length] },
    })),
  };
}

function TurnBubble({ turn, onFollowUp }: { turn: Turn; onFollowUp: (q: string) => void }) {
  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground">
          {turn.text}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] space-y-3 rounded-2xl rounded-bl-sm border border-border bg-card/80 px-4 py-3">
        {turn.mode === "live" && turn.intents.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {turn.intents.map((i) => (
              <Badge key={i} variant="indigo">
                {i}
              </Badge>
            ))}
          </div>
        )}

        <p className="whitespace-pre-wrap text-sm leading-relaxed">{turn.text}</p>

        {turn.mode === "live" && turn.warnings.length > 0 && (
          <Alert variant="warning">
            <ul className="list-disc space-y-1 pl-4">
              {turn.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </Alert>
        )}

        {turn.mode === "chat" && turn.chart && (
          <Card className="bg-background/40">
            <CardContent className="p-3">
              {turn.chart.title && <p className="mb-1 text-xs font-medium text-muted-foreground">{turn.chart.title}</p>}
              <EChart option={chartOption(turn.chart)} height={240} />
            </CardContent>
          </Card>
        )}

        {turn.mode === "chat" && turn.table && turn.table.rows.length > 0 && (
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  {turn.table.columns.map((c) => (
                    <TH key={c}>{c}</TH>
                  ))}
                </TR>
              </THead>
              <TBody>
                {turn.table.rows.map((row, i) => (
                  <TR key={i}>
                    {row.map((cell, j) => (
                      <TD key={j}>{cell === null || cell === undefined || cell === "" ? "—" : String(cell)}</TD>
                    ))}
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
        )}

        {turn.mode === "chat" && turn.datasetsUsed.length > 0 && (
          <p className="text-xs text-muted-foreground">Source: {turn.datasetsUsed.join(", ")}</p>
        )}

        {turn.mode === "chat" && turn.followUps.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {turn.followUps.map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => onFollowUp(f)}
                className="rounded-full border border-border bg-secondary/40 px-3 py-1 text-xs text-secondary-foreground transition-colors hover:bg-secondary/70"
              >
                {f}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function Ask() {
  const [dataSource, setDataSource] = useState(LIVE_DATA);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [error, setError] = useState<string | null>(null);
  const datasets = useDatasets();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  const ask = useMutation({
    mutationFn: async (vars: { question: string; history: { role: string; content: string }[] }): Promise<Turn> => {
      if (!dataSource) {
        const res = await apiFetch("/ai/ask", {
          method: "POST",
          body: JSON.stringify({ question: vars.question, dataset_id: null }),
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Failed to answer question");
        const data: AskResponse = await res.json();
        return { role: "assistant", mode: "live", text: data.narrative, intents: data.intents, warnings: data.warnings };
      }

      const res = await apiFetch("/data-chat/message", {
        method: "POST",
        body: JSON.stringify({
          question: vars.question,
          history: vars.history,
          dataset_ids: dataSource === ALL_DATASETS ? null : [dataSource],
        }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Failed to answer question");
      const data: ChatResponse = await res.json();
      return {
        role: "assistant", mode: "chat", text: data.answer, chart: data.chart, table: data.table,
        followUps: data.follow_ups, datasetsUsed: data.datasets_used,
      };
    },
    onSuccess: (turn) => setTurns((prev) => [...prev, turn]),
    onError: (e: Error) => setError(e.message),
  });

  function send(q: string) {
    setError(null);
    if (!q.trim() || ask.isPending) return;
    const history = turns
      .filter((t): t is Extract<Turn, { role: "user" }> | Extract<Turn, { role: "assistant"; mode: "chat" }> =>
        t.role === "user" || (t.role === "assistant" && t.mode === "chat"),
      )
      .map((t) => ({ role: t.role === "user" ? "user" : "assistant", content: t.text }));
    setTurns((prev) => [...prev, { role: "user", text: q }]);
    setQuestion("");
    ask.mutate({ question: q, history });
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(question);
  }

  function onChangeDataSource(v: string) {
    setDataSource(v);
    setTurns([]);
    setError(null);
  }

  return (
    <div className="flex h-[calc(100vh-6rem)] flex-col">
      <PageHeader
        title="Ask your data"
        description="Natural-language Q&A over your live inventory or any uploaded dataset."
        actions={
          <div className="flex items-center gap-2">
            <Select value={dataSource} onChange={(e) => onChangeDataSource(e.target.value)} className="w-56">
              <option value={LIVE_DATA}>Live production data</option>
              <option value={ALL_DATASETS}>All my datasets</option>
              {datasets.data?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.row_count.toLocaleString()} rows)
                </option>
              ))}
            </Select>
            {turns.length > 0 && (
              <Button type="button" variant="outline" size="sm" onClick={() => setTurns([])}>
                New chat
              </Button>
            )}
          </div>
        }
      />

      <Card className="flex min-h-0 flex-1 flex-col">
        <CardContent ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-6">
          {turns.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
              <div className="flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary">
                <Sparkles className="size-6" />
              </div>
              <div className="space-y-1">
                <p className="text-sm font-medium">
                  {dataSource ? "Ask anything about this data" : "Ask anything about your inventory"}
                </p>
                <p className="text-sm text-muted-foreground">
                  {dataSource
                    ? "e.g. \"what's the average of <column>?\" or \"chart totals by month\""
                    : "e.g. how's my inventory looking?"}
                </p>
              </div>
              {!dataSource && (
                <div className="flex max-w-lg flex-wrap justify-center gap-2">
                  {LIVE_EXAMPLES.map((ex) => (
                    <button
                      key={ex}
                      type="button"
                      onClick={() => send(ex)}
                      className="rounded-full border border-border bg-secondary/40 px-3 py-1.5 text-xs text-secondary-foreground transition-colors hover:bg-secondary/70"
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            turns.map((t, i) => <TurnBubble key={i} turn={t} onFollowUp={send} />)
          )}

          {ask.isPending && (
            <div className="flex justify-start">
              <div className="flex items-center gap-1.5 rounded-2xl rounded-bl-sm border border-border bg-card/80 px-4 py-3">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="size-1.5 animate-bounce rounded-full bg-muted-foreground"
                    style={{ animationDelay: `${i * 120}ms` }}
                  />
                ))}
              </div>
            </div>
          )}
        </CardContent>

        <div className={cn("border-t border-border p-4", error && "pb-0")}>
          {error && (
            <Alert variant="destructive" className="mb-3">
              {error}
            </Alert>
          )}
          <form onSubmit={onSubmit} className="flex gap-2">
            <Input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder={dataSource ? "Ask about this data…" : "Ask about your inventory…"}
              className="flex-1"
              autoFocus
            />
            <Button type="submit" loading={ask.isPending} disabled={!question.trim()}>
              <Send /> Ask
            </Button>
          </form>
        </div>
      </Card>
    </div>
  );
}
