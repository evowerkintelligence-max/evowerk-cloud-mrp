import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { C, fmt, money } from "../shared";
import { cn } from "@/lib/utils";
import type { OeeResult, RevenueImpact } from "./types";

export function Revenue({ ri }: { ri: RevenueImpact }) {
  const share = (part: number) => (ri.total_lost > 0 ? Math.round((part / ri.total_lost) * 100) : 0);
  const pillars = [
    { label: "Availability loss", value: ri.availability_lost, color: C.amber, note: "machines idle" },
    { label: "Performance loss", value: ri.performance_lost, color: C.indigo, note: "running slow" },
    { label: "Quality loss", value: ri.quality_lost, color: C.pink, note: "scrap & rework" },
  ];
  return (
    <Card className="border-rose-500/30">
      <CardContent className="space-y-6 p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-xs font-bold uppercase tracking-widest text-rose-400">Revenue at risk</div>
            <div className="mt-1 text-4xl font-extrabold tracking-tight">{money(ri.total_lost)}</div>
            <div className="mt-1 text-sm text-muted-foreground">
              lost over {Math.round(ri.period_days)}
              {ri.period_days_estimated ? "*" : ""} days · {money(ri.daily_rate)}/day · {money(ri.monthly_rate)}/mo projected
            </div>
            {ri.performance_estimated && (
              <p className="mt-2 max-w-xl text-xs text-amber-400">
                * No target rate set — Performance assumed 100%, so this figure excludes speed losses and understates the true total.
              </p>
            )}
            {ri.period_days_estimated && (
              <p className="mt-2 max-w-xl text-xs text-amber-400">
                * No date column mapped — period length guessed as 1 row = 1 shift (3 shifts/day). Map a date column for an exact period.
              </p>
            )}
          </div>
          {ri.benchmark_saving > 0 && (
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-5 py-3 text-center">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-emerald-400">Reach 85% OEE target</div>
              <div className="text-2xl font-bold text-emerald-400">{money(ri.benchmark_saving)}</div>
              <div className="text-xs text-muted-foreground">additional savings</div>
            </div>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          {pillars.map((p) => (
            <div key={p.label} className="rounded-lg border border-border/60 p-4">
              <div className="text-[10px] font-bold uppercase tracking-wider" style={{ color: p.color }}>
                {p.label}
              </div>
              <div className="mt-1 text-xl font-bold">{money(p.value)}</div>
              <div className="text-xs text-muted-foreground">
                {share(p.value)}% of total · {p.note}
              </div>
              <div className="mt-3 h-1 rounded-full bg-secondary">
                <div className="h-1 rounded-full" style={{ width: `${share(p.value)}%`, background: p.color }} />
              </div>
            </div>
          ))}
        </div>

        {ri.per_machine.length > 0 && (
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Loss by machine</div>
            <div className="space-y-2">
              {ri.per_machine.map((m, i) => (
                <div key={m.machine} className="flex items-center gap-3 text-sm">
                  <span className={cn("w-40 truncate", i === 0 ? "font-bold text-rose-400" : "text-muted-foreground")}>
                    {m.machine}
                    {i === 0 && (
                      <Badge variant="danger" className="ml-2">
                        worst
                      </Badge>
                    )}
                  </span>
                  <span className="w-24 text-right font-semibold">{money(m.total_lost)}</span>
                  <div className="h-1.5 flex-1 rounded-full bg-secondary">
                    <div
                      className="h-1.5 rounded-full bg-rose-500"
                      style={{ width: `${ri.total_lost > 0 ? Math.round((m.total_lost / ri.total_lost) * 100) : 0}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const FAMILY_COLOR: Record<string, string> = { availability: C.amber, performance: C.indigo, quality: C.pink };

export function SixBigLosses({ six }: { six: NonNullable<OeeResult["six_big_losses"]> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Six Big Losses</CardTitle>
      </CardHeader>
      <CardContent>
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Loss</TH>
                <TH>Family</TH>
                <TH>Minutes</TH>
                <TH>Units</TH>
                <TH>Cost</TH>
                <TH>% of loss</TH>
              </TR>
            </THead>
            <TBody>
              {six.buckets.map((b) => (
                <TR key={b.key}>
                  <TD className="font-medium">{b.label}</TD>
                  <TD>
                    <span
                      className="rounded-full px-2 py-0.5 text-xs font-semibold capitalize"
                      style={{ color: FAMILY_COLOR[b.family], background: `${FAMILY_COLOR[b.family]}22` }}
                    >
                      {b.family}
                    </span>
                  </TD>
                  <TD>{fmt(b.minutes)}</TD>
                  <TD>{b.units !== null ? fmt(b.units) : "—"}</TD>
                  <TD>{b.cost !== null ? money(b.cost) : "—"}</TD>
                  <TD>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-24 rounded-full bg-secondary">
                        <div
                          className="h-1.5 rounded-full"
                          style={{ width: `${Math.min(100, b.pct_of_loss)}%`, background: FAMILY_COLOR[b.family] }}
                        />
                      </div>
                      <span className="text-xs text-muted-foreground">{b.pct_of_loss}%</span>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      </CardContent>
    </Card>
  );
}
