import type { BadgeProps } from "@/components/ui/badge";

export interface GridDetailLine {
  item_id: string;
  item_sku: string;
  item_name: string;
  is_manufactured: boolean;
  gross_demand: string;
  on_hand: string;
  on_order: string;
  on_draft: string;
  in_production: string;
  safety_stock: string;
  net_requirement: string;
  suggested_order_quantity: string;
  need_by_date: string | null;
  suggested_order_date: string | null;
}

export interface GridProduct {
  item_id: string;
  item_sku: string;
  item_name: string;
  planned_qty: string;
  needed_by: string | null;
  /** How many separate dated rows make up planned_qty: 0 nothing planned,
   * 1 normal, more than 1 only for an imported or legacy plan. */
  planned_bucket_count: number;
  /** What customers have actually ordered, as opposed to planned_qty which is
   * the estimate someone typed. */
  committed_qty: string;
  /** True when some of that committed quantity was due before today. */
  committed_overdue: boolean;
  on_hand: string;
  materials_status: "in_stock" | "arriving" | "missing";
  missing_materials: string[];
  /** Meaningful only when materials_status is "arriving": true when every
   * covering component sits on a draft PO only, none of them actually
   * submitted or in flight. A draft can still be edited or cancelled, so
   * this is "on the way" versus "nothing sent to a supplier yet". */
  arriving_is_drafted: boolean;
  ready_by: string | null;
  /** True when ready_by is null because a component's lead time is not on
   * file, as opposed to null because nothing is planned. Same null, two
   * different messages. */
  ready_by_unknown: boolean;
  suggested_qty: string | null;
  /** Units already being made, as opposed to planned_qty which is the
   * estimate someone typed and committed_qty which is what customers
   * ordered. */
  in_production: string;
  /** What a job raised from this row would be for: the row's demand under the
   * org's demand policy, less what jobs are already making. Backend-owned so
   * it cannot drift from the can_make the backend judges against it. */
  outstanding_qty: string;
  /** False when the product has no active recipe, its components are short,
   * or jobs already cover the row, so Make is offered only when it would
   * actually succeed. */
  can_make: boolean;
  /** Why can_make is false when there is something outstanding to make: no
   * active recipe, or which component is short and by how much. Null when
   * can_make is true, or when nothing is outstanding (the row already says
   * a job is covering or waiting on this case instead). */
  cannot_make_reason: string | null;
  detail: GridDetailLine[];
}

export interface GridToBuy {
  item_id: string;
  item_sku: string;
  item_name: string;
  quantity: string;
  supplier_name: string | null;
  order_by: string | null;
  urgency: "overdue" | "unknown" | "soon" | "later";
}

export interface PlanningGridResponse {
  horizon_start: string;
  horizon_end: string;
  products: GridProduct[];
  to_buy: GridToBuy[];
  summary: {
    missing: number;
    arriving: number;
    in_stock: number;
    /** Jobs late to start, or running far longer than a job like them should. */
    jobs_needing_attention: number;
  };
}

/** Plain-language status wording - the grid never says "net requirement". */
export function materialsStatusLabel(product: GridProduct): string {
  if (product.materials_status === "missing") {
    const count = product.missing_materials.length;
    return `${count} missing`;
  }
  if (product.materials_status === "arriving") {
    // A draft PO is not a promise: it can still be edited or cancelled
    // outright, so this must not claim something is actually on its way.
    return product.arriving_is_drafted ? "Drafted, not ordered yet" : "Arriving";
  }
  return "All in stock";
}

export function materialsStatusVariant(
  status: GridProduct["materials_status"],
): NonNullable<BadgeProps["variant"]> {
  if (status === "missing") return "danger";
  if (status === "arriving") return "warning";
  return "success";
}

/** What the Ready by cell should say, distinguishing "nothing planned" from
 * "a component's lead time is not on file" - both are a null ready_by but
 * mean different things to a shop floor user. */
export function readyByLabel(product: GridProduct): string {
  if (product.ready_by !== null) return product.ready_by;
  return product.ready_by_unknown ? "Lead time unknown" : "Not scheduled";
}

/** Red when the plan will not be ready until after the date it is needed
 * by - the spec's "ready by red when later than needed-by". */
export function readyByIsLate(product: GridProduct): boolean {
  return (
    product.ready_by !== null &&
    product.needed_by !== null &&
    product.ready_by > product.needed_by
  );
}

/** "50.0000" -> "50", "1.5000" -> "1.5". Trailing zeros are engine noise. */
export function trimQty(value: string): string {
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : value;
}
