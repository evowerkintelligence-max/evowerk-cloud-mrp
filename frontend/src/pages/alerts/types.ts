export type AlertType = "machine_downtime" | "quality" | "scrap" | "inventory" | "production_target" | "stream_metric";
export type Severity = "critical" | "warning" | "info";

export interface AlertRule {
  id: string;
  dataset_id: string | null;
  name: string;
  type: AlertType;
  severity: Severity;
  enabled: boolean;
  config: Record<string, unknown>;
}

export interface AlertTrigger {
  rule_id: string;
  rule_name: string;
  type: AlertType;
  severity: Severity;
  triggered: boolean;
  value: number;
  threshold: number;
  message: string;
  offenders: string[];
  evaluated_at: string;
}

export interface ScanResponse {
  results: AlertTrigger[];
  triggered_count: number;
  evaluated_count: number;
}

export interface ScheduleOption {
  schedule_id: string;
  name: string;
}

export async function j<T>(resPromise: Promise<Response>): Promise<T> {
  const res = await resPromise;
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Request failed");
  return res.json();
}

export const TYPE_LABELS: Record<AlertType, string> = {
  machine_downtime: "Machine Downtime",
  quality: "Quality",
  scrap: "Scrap",
  inventory: "Inventory",
  production_target: "Production Target",
  stream_metric: "Stream Metric",
};

export const SEV_VARIANT: Record<Severity, "danger" | "warning" | "info"> = {
  critical: "danger",
  warning: "warning",
  info: "info",
};
