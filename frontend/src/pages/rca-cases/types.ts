import type { EngineField } from "@/pages/analytics/shared";

export interface RcaCase {
  id: string;
  dataset_id: string;
  title: string;
  problem_statement: string;
  status: string;
  owner: string | null;
  created_at: string;
  closed_at: string | null;
}

export interface RcaNode {
  id: string;
  parent_id: string | null;
  text: string;
  node_type: string;
  status: string;
  category: string | null;
}

export interface RcaAction {
  id: string;
  node_id: string | null;
  description: string;
  owner: string | null;
  status: string;
  completed_at: string | null;
  verification: { before_oee: number; after_oee: number; delta_oee: number } | null;
}

export interface RcaEvent {
  id: string;
  event_type: string;
  summary: string;
  actor: string | null;
  created_at: string;
}

export interface RcaCandidate {
  dimension: string;
  label: string;
  oee: number;
  delta_oee: number;
  row_count: number;
  support_pct: number;
  confidence: number;
}

export interface RcaParetoBucket {
  label: string;
  value: number;
  pct_of_total: number;
  cumulative_pct: number;
}

export interface RcaTimelineEvent {
  date: string;
  oee: number;
  delta_oee: number;
  row_count: number;
  is_center: boolean;
}

export interface RcaTrendEntry {
  kind: string;
  key: string;
  count: number;
  case_ids: string[];
  case_titles: string[];
}

export async function j<T>(resPromise: Promise<Response>): Promise<T> {
  const res = await resPromise;
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Request failed");
  return res.json();
}

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : "Something went wrong";
}

export const STATUS_VARIANT: Record<string, "neutral" | "info" | "success" | "warning"> = {
  open: "neutral",
  investigating: "info",
  actions_open: "warning",
  closed: "success",
  verified: "success",
};

// Valid forward/reset transitions per status - drives the manual status
// control in CaseDetail. investigating/actions_open also auto-advance from
// create_node/create_action on the backend, so most cases never need this;
// it exists for closing, verifying, and correcting a case manually.
export const STATUS_TRANSITIONS: Record<string, { value: string; label: string }[]> = {
  open: [
    { value: "investigating", label: "Start investigating" },
    { value: "closed", label: "Close" },
  ],
  investigating: [
    { value: "actions_open", label: "Open actions" },
    { value: "closed", label: "Close" },
  ],
  actions_open: [{ value: "closed", label: "Close" }],
  closed: [
    { value: "verified", label: "Mark verified" },
    { value: "open", label: "Reopen" },
  ],
  verified: [{ value: "open", label: "Reopen" }],
};

// Column-mapping hints shared by Evidence/Timeline/Verify - same convention
// (and same regexes) as the OEE engine screen, so a column named e.g.
// "operating_minutes" auto-fills instead of leaving the user to type it.
export const OEE_FIELDS: EngineField[] = [
  { key: "planned_time_col", label: "Planned time", hints: [/planned/i, /plan_time/i, /scheduled/i] },
  { key: "operating_time_col", label: "Operating time", hints: [/operating_time/i, /operating/i, /run_time/i, /uptime/i, /actual_time/i] },
  { key: "total_units_col", label: "Total units", hints: [/total_unit/i, /units_produced/i, /output/i, /total_prod/i] },
  { key: "good_units_col", label: "Good units", hints: [/good_unit/i, /pass/i, /conform/i, /ok_unit/i, /first_pass/i] },
];
export const DATE_FIELD: EngineField = { key: "date_col", label: "Date", hints: [/date/i, /time/i, /period/i, /shift_date/i] };
export const DIMENSION_HINT = /machine|line|asset|equip|station|shift|operator/i;
