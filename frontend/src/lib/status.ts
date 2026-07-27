import type { BadgeProps } from "@/components/ui/badge";

type Variant = NonNullable<BadgeProps["variant"]>;

const PO_STATUS: Record<string, Variant> = {
  draft: "neutral",
  submitted: "info",
  partially_received: "warning",
  received: "success",
  cancelled: "danger",
};

export function poStatusVariant(status: string): Variant {
  return PO_STATUS[status] ?? "neutral";
}

const SO_STATUS: Record<string, Variant> = {
  open: "info",
  done: "success",
  cancelled: "danger",
};

export function soStatusVariant(status: string): Variant {
  return SO_STATUS[status] ?? "neutral";
}

const ITEM_TYPE: Record<string, Variant> = {
  raw_material: "teal",
  component: "indigo",
  finished_good: "primary",
};

export function itemTypeVariant(type: string): Variant {
  return ITEM_TYPE[type] ?? "neutral";
}

/** "partially_received" -> "Partially Received" */
export function humanize(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const INVENTORY_STATUS: Record<string, Variant> = {
  stockout: "danger",
  low: "warning",
  dead_stock: "warning",
  excess: "info",
  slow_moving: "neutral",
  healthy: "success",
  no_data: "neutral",
};

export function inventoryStatusVariant(status: string): Variant {
  return INVENTORY_STATUS[status] ?? "neutral";
}
