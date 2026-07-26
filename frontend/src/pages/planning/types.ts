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
  on_hand: string;
  materials_status: "in_stock" | "arriving" | "missing";
  missing_materials: string[];
  ready_by: string | null;
  suggested_qty: string | null;
  detail: GridDetailLine[];
}

export interface GridToBuy {
  item_id: string;
  item_sku: string;
  item_name: string;
  quantity: string;
  supplier_name: string | null;
  order_by: string | null;
  urgency: "overdue" | "soon" | "later";
}

export interface PlanningGridResponse {
  horizon_start: string;
  horizon_end: string;
  products: GridProduct[];
  to_buy: GridToBuy[];
  summary: { missing: number; arriving: number; in_stock: number };
}

/** Plain-language status wording - the grid never says "net requirement". */
export function materialsStatusLabel(product: GridProduct): string {
  if (product.materials_status === "missing") {
    const count = product.missing_materials.length;
    return `${count} missing`;
  }
  if (product.materials_status === "arriving") return "Arriving";
  return "All in stock";
}

export function materialsStatusVariant(
  status: GridProduct["materials_status"],
): NonNullable<BadgeProps["variant"]> {
  if (status === "missing") return "danger";
  if (status === "arriving") return "warning";
  return "success";
}

/** "50.0000" -> "50", "1.5000" -> "1.5". Trailing zeros are engine noise. */
export function trimQty(value: string): string {
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : value;
}
