import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Radio } from "lucide-react";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { MachineDetail } from "./predictive/MachineDetail";

interface FleetMachine {
  machine: string;
  status: "learning" | "active" | "unmapped";
  risk_level: "high" | "medium" | "low" | "unknown";
  failure_probability_30d: number | null;
  active_anomalies: number;
  last_reading_at: string | null;
}

interface ScheduleItem {
  id: string;
  machine: string;
  priority: string;
  action: string;
  status: string;
}

const RISK_VARIANT: Record<string, "danger" | "warning" | "success" | "neutral"> = {
  high: "danger", medium: "warning", low: "success", unknown: "neutral",
};

const STATUS_LABEL: Record<string, string> = {
  learning: "Learning baseline", active: "Active", unmapped: "Needs sensor mapping",
};

function ScheduleTable() {
  const qc = useQueryClient();
  const schedules = useQuery<ScheduleItem[]>({
    queryKey: ["maintenance-schedules"],
    queryFn: async () => {
      const res = await apiFetch("/predictive/schedules");
      if (!res.ok) throw new Error("Failed to load schedules");
      return res.json();
    },
  });

  const markDone = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/predictive/schedules/${id}`, { method: "PATCH", body: JSON.stringify({ status: "done" }) });
      if (!res.ok) throw new Error("Update failed");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["maintenance-schedules"] }),
  });

  if (!schedules.data?.length) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Maintenance schedule</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Machine</TH>
                <TH>Priority</TH>
                <TH>Action</TH>
                <TH>Status</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {schedules.data.map((s) => (
                <TR key={s.id}>
                  <TD className="font-medium">{s.machine}</TD>
                  <TD>{s.priority}</TD>
                  <TD>{s.action}</TD>
                  <TD>
                    <Badge variant={s.status === "done" ? "success" : "neutral"}>{s.status}</Badge>
                  </TD>
                  <TD>
                    {s.status !== "done" && (
                      <Button size="sm" variant="outline" loading={markDone.isPending} onClick={() => markDone.mutate(s.id)}>
                        Mark done
                      </Button>
                    )}
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

export function Predictive() {
  const [selectedMachine, setSelectedMachine] = useState<string | null>(null);
  const navigate = useNavigate();

  const fleet = useQuery<{ machines: FleetMachine[] }>({
    queryKey: ["cbm-fleet"],
    queryFn: async () => {
      const res = await apiFetch("/cbm/fleet");
      if (!res.ok) throw new Error("Failed to load fleet status");
      return res.json();
    },
    refetchInterval: 15000,
  });

  if (selectedMachine) {
    return <MachineDetail machine={selectedMachine} onBack={() => setSelectedMachine(null)} />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Predictive Maintenance"
        description="Condition-based monitoring: live sensor anomaly detection and Weibull-calibrated remaining-useful-life, per machine."
      />

      {fleet.data && fleet.data.machines.length === 0 ? (
        <EmptyState
          icon={<Radio />}
          title="No machines connected"
          description="Set a machine and sensor map on a realtime stream (Realtime page) to start monitoring it here."
        />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Fleet health</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Machine</TH>
                    <TH>Status</TH>
                    <TH>Risk</TH>
                    <TH>30-day failure probability</TH>
                    <TH>Active anomalies</TH>
                    <TH>Last reading</TH>
                  </TR>
                </THead>
                <TBody>
                  {fleet.data?.machines.map((m) => {
                    const isUnmapped = m.status === "unmapped";
                    return (
                      <TR
                        key={m.machine}
                        className={isUnmapped ? undefined : "cursor-pointer"}
                        onClick={isUnmapped ? undefined : () => setSelectedMachine(m.machine)}
                      >
                        <TD className="font-medium">{m.machine}</TD>
                        <TD className="text-muted-foreground">{STATUS_LABEL[m.status] ?? m.status}</TD>
                        <TD>
                          <Badge variant={RISK_VARIANT[m.risk_level] ?? "neutral"}>{m.risk_level}</Badge>
                        </TD>
                        <TD>{m.failure_probability_30d != null ? `${(m.failure_probability_30d * 100).toFixed(1)}%` : "—"}</TD>
                        <TD>{m.active_anomalies}</TD>
                        <TD className="text-muted-foreground">
                          {isUnmapped ? (
                            <Button size="sm" variant="outline" onClick={() => navigate("/realtime")}>
                              Configure
                            </Button>
                          ) : m.last_reading_at ? (
                            new Date(m.last_reading_at).toLocaleString()
                          ) : (
                            "No readings yet"
                          )}
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            </TableWrap>
          </CardContent>
        </Card>
      )}

      <ScheduleTable />
    </div>
  );
}
