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
