import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { EChart } from "@/pages/analytics/shared";
import { controlChart, NELSON_RULE_DESCRIPTIONS, type SpcPoint } from "@/pages/analytics/shared";

interface SensorAnomaly {
  id: string;
  violations: number[];
  detected_at: string;
  acknowledged_at: string | null;
}

interface SensorDetail {
  centerline: number | null;
  ucl: number | null;
  lcl: number | null;
  n: number;
  readings: { value: number; ts: string }[];
  anomalies: SensorAnomaly[];
}

interface MachineDetailResult {
  machine: string;
  risk_level: "high" | "medium" | "low" | "unknown";
  failure_probability_30d: number | null;
  fit_tier: "per_machine" | "fleet_pooled" | "insufficient_data";
  sensors: Record<string, SensorDetail>;
}

const RISK_VARIANT: Record<string, "danger" | "warning" | "success" | "neutral"> = {
  high: "danger", medium: "warning", low: "success", unknown: "neutral",
};

const FIT_TIER_LABEL: Record<string, string> = {
  per_machine: "Calibrated from this machine's breakdown history",
  fleet_pooled: "Calibrated from fleet-wide breakdown history (not enough data for this machine alone)",
  insufficient_data: "Not enough breakdown history to estimate",
};

function toSpcPoints(sensor: SensorDetail): SpcPoint[] {
  const { centerline, ucl, lcl } = sensor;
  return sensor.readings.map((r) => ({
    label: new Date(r.ts).toLocaleString(),
    value: r.value,
    in_control: centerline == null || ucl == null || lcl == null ? true : r.value <= ucl && r.value >= lcl,
  }));
}

export function MachineDetail({ machine, onBack }: { machine: string; onBack: () => void }) {
  const qc = useQueryClient();

  const detail = useQuery<MachineDetailResult>({
    queryKey: ["cbm-machine", machine],
    queryFn: async () => {
      const res = await apiFetch(`/cbm/machines/${encodeURIComponent(machine)}`);
      if (!res.ok) throw new Error("Failed to load machine detail");
      return res.json();
    },
    refetchInterval: 15000,
  });

  const acknowledge = useMutation({
    mutationFn: async (anomalyId: string) => {
      const res = await apiFetch(`/cbm/anomalies/${anomalyId}/acknowledge`, { method: "POST" });
      if (!res.ok) throw new Error("Failed to acknowledge");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cbm-machine", machine] }),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title={machine}
        description="Condition-based monitoring detail"
        actions={
          <Button variant="secondary" onClick={onBack}>
            <ArrowLeft className="mr-2 size-4" /> Back to fleet
          </Button>
        }
      />

      {detail.data && (
        <Card>
          <CardHeader>
            <CardTitle>
              Remaining useful life{" "}
              <Badge variant={RISK_VARIANT[detail.data.risk_level] ?? "neutral"}>{detail.data.risk_level}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold">
              {detail.data.failure_probability_30d != null
                ? `${(detail.data.failure_probability_30d * 100).toFixed(1)}% chance of failure in the next 30 days`
                : "Not enough breakdown history to estimate RUL"}
            </div>
            <p className="mt-2 text-sm text-muted-foreground">{FIT_TIER_LABEL[detail.data.fit_tier]}</p>
          </CardContent>
        </Card>
      )}

      {detail.data &&
        Object.entries(detail.data.sensors).map(([sensor, s]) => (
          <Card key={sensor}>
            <CardHeader>
              <CardTitle>{sensor}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {s.centerline != null && s.ucl != null && s.lcl != null ? (
                <EChart
                  height={280}
                  option={controlChart(toSpcPoints(s), (p) => p.value, s.centerline, s.ucl, s.lcl, sensor)}
                />
              ) : (
                <p className="text-sm text-muted-foreground">Learning baseline — needs at least 20 readings.</p>
              )}

              {s.anomalies.length > 0 && (
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Detected</TH>
                        <TH>Rule violations</TH>
                        <TH>Status</TH>
                        <TH />
                      </TR>
                    </THead>
                    <TBody>
                      {s.anomalies.map((a) => (
                        <TR key={a.id}>
                          <TD className="text-muted-foreground">{new Date(a.detected_at).toLocaleString()}</TD>
                          <TD>{a.violations.map((v) => NELSON_RULE_DESCRIPTIONS[v] ?? `Rule ${v}`).join("; ")}</TD>
                          <TD>
                            <Badge variant={a.acknowledged_at ? "neutral" : "warning"}>
                              {a.acknowledged_at ? "Acknowledged" : "Open"}
                            </Badge>
                          </TD>
                          <TD>
                            {!a.acknowledged_at && (
                              <Button size="sm" variant="secondary" loading={acknowledge.isPending} onClick={() => acknowledge.mutate(a.id)}>
                                Acknowledge
                              </Button>
                            )}
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
              )}
            </CardContent>
          </Card>
        ))}
    </div>
  );
}
