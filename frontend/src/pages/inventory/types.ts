import { humanize } from "@/lib/status";

export interface InventoryAnalyticsLine {
  item_id: string;
  reorder_point: number | null;
  safety_stock: number | null;
  days_of_supply: number | null;
  abc_class: string | null;
  xyz_class: string | null;
  status: string;
}

export interface RcaValue {
  value: string;
  item_count: number;
  at_risk_count: number;
  expected_at_risk_count: number;
  standardized_residual: number;
  is_significant: boolean;
}

export interface RcaResult {
  dimension: string;
  method: string;
  chi2_statistic: number | null;
  p_value: number | null;
  degrees_of_freedom: number | null;
  low_expected_frequency_warning: boolean;
  values: RcaValue[];
}

export interface ScenarioDelta {
  item_id: string;
  baseline_lead_time_days: number | null;
  scenario_lead_time_days: number | null;
  baseline_reorder_point: number | null;
  scenario_reorder_point: number | null;
  reorder_point_delta: number | null;
  baseline_status: string;
  scenario_status: string;
  classification: string;
}

export const DIMENSION_OPTIONS: { value: string; label: string }[] = [
  { value: "supplier", label: "Supplier" },
  { value: "item_type", label: "Item type" },
  { value: "abc_class", label: "Value class" },
];

const ABC_LABELS: Record<string, string> = {
  A: "High-value item",
  B: "Mid-value item",
  C: "Lower-value item",
};

export function abcLabel(abcClass: string | null): string | null {
  return abcClass ? (ABC_LABELS[abcClass] ?? null) : null;
}

const XYZ_LABELS: Record<string, string> = {
  X: "Steady demand",
  Y: "Somewhat variable demand",
  Z: "Erratic demand",
};

export function xyzLabel(xyzClass: string | null): string | null {
  return xyzClass ? (XYZ_LABELS[xyzClass] ?? null) : null;
}

function classFragment(line: InventoryAnalyticsLine): string {
  const parts = [abcLabel(line.abc_class), xyzLabel(line.xyz_class)].filter(
    (p): p is string => p !== null,
  );
  if (parts.length === 0) return "";
  const sentence = parts
    .map((p, i) => (i === 0 ? p : p.charAt(0).toLowerCase() + p.slice(1)))
    .join(", ");
  return ` ${sentence}.`;
}

export function whyThisStatusSentence(line: InventoryAnalyticsLine): string {
  const days = line.days_of_supply != null ? Math.round(line.days_of_supply) : null;
  const reorder = line.reorder_point != null ? Math.round(line.reorder_point) : null;

  switch (line.status) {
    case "stockout":
      return `Out of stock.${classFragment(line)}`;
    case "low":
      if (days != null && reorder != null) {
        return `${days} days of stock left, below your reorder point of ${reorder} units.${classFragment(line)}`;
      }
      return `Running low on stock.${classFragment(line)}`;
    case "dead_stock":
      return `No recent demand, but stock is still on hand.${classFragment(line)}`;
    case "excess":
      return days != null
        ? `${days} days of stock on hand — more than you typically need.${classFragment(line)}`
        : `More stock on hand than you typically need.${classFragment(line)}`;
    case "slow_moving":
      return days != null
        ? `${days} days of stock on hand, moving slower than usual.${classFragment(line)}`
        : `Moving slower than usual.${classFragment(line)}`;
    case "healthy":
      return days != null
        ? `${days} days of stock on hand — on track.${classFragment(line)}`
        : `On track.${classFragment(line)}`;
    case "no_data":
      return "Not enough history yet to analyze this item.";
    default:
      return humanize(line.status);
  }
}

export function rcaTopValue(result: RcaResult): RcaValue | null {
  if (result.method !== "chi_square_independence") return null;
  // Backend sorts values by |standardized_residual| descending; require a
  // positive residual too, since a negative one means "runs low LESS often
  // than expected" - the opposite of what this banner reports.
  return result.values.find((v) => v.is_significant && v.standardized_residual > 0) ?? null;
}

const DIMENSION_PHRASING: Record<string, (value: string) => string> = {
  supplier: (value) => `Items from ${value}`,
  item_type: (value) => `${humanize(value)} items`,
  abc_class: (value) => `${abcLabel(value)?.replace(/ item$/, "") ?? "Unclassified"} items`,
};

export function rcaBannerSentence(result: RcaResult): string | null {
  const top = rcaTopValue(result);
  if (!top || top.expected_at_risk_count <= 0) return null;
  const ratio = top.at_risk_count / top.expected_at_risk_count;
  const phrase = (DIMENSION_PHRASING[result.dimension] ?? ((v: string) => `Items in "${v}"`))(top.value);
  return `${phrase} run low ${ratio.toFixed(1)}× more often than your other stock.`;
}

export function scenarioDeltaSentence(delta: ScenarioDelta): string {
  const from = delta.baseline_reorder_point;
  const to = delta.scenario_reorder_point;
  if (from == null || to == null) {
    return "Reorder point can't be estimated without a lead time.";
  }
  const direction = to > from ? "rise" : to < from ? "drop" : "stay";
  const reorderPart = `Reorder point would ${direction} from ${Math.round(from)} to ${Math.round(to)} units`;
  if (delta.baseline_status === delta.scenario_status) {
    return `${reorderPart}.`;
  }
  return `${reorderPart}; status would change from ${humanize(delta.baseline_status)} to ${humanize(delta.scenario_status)}.`;
}

const STATUS_URGENCY: Record<string, number> = {
  stockout: 0,
  low: 1,
  dead_stock: 2,
  excess: 3,
  slow_moving: 4,
  healthy: 5,
  no_data: 6,
};

const ABC_RANK: Record<string, number> = { A: 0, B: 1, C: 2 };

export function sortInventoryRows<T extends { item_id: string; item_sku: string }>(
  rows: T[],
  analyticsByItem: Map<string, InventoryAnalyticsLine>,
): T[] {
  return [...rows].sort((a, b) => {
    const aLine = analyticsByItem.get(a.item_id);
    const bLine = analyticsByItem.get(b.item_id);
    const aUrgency = aLine ? (STATUS_URGENCY[aLine.status] ?? 99) : 99;
    const bUrgency = bLine ? (STATUS_URGENCY[bLine.status] ?? 99) : 99;
    if (aUrgency !== bUrgency) return aUrgency - bUrgency;
    const aAbc = aLine?.abc_class ? (ABC_RANK[aLine.abc_class] ?? 3) : 3;
    const bAbc = bLine?.abc_class ? (ABC_RANK[bLine.abc_class] ?? 3) : 3;
    if (aAbc !== bAbc) return aAbc - bAbc;
    return a.item_sku.localeCompare(b.item_sku);
  });
}
