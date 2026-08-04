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

const WO_STATUS: Record<string, Variant> = {
  scheduled: "neutral",
  in_progress: "info",
  done: "success",
  cancelled: "danger",
};

export function woStatusVariant(status: string): Variant {
  return WO_STATUS[status] ?? "neutral";
}

const ITEM_TYPE: Record<string, Variant> = {
  raw_material: "teal",
  component: "indigo",
  finished_good: "primary",
};

export function itemTypeVariant(type: string): Variant {
  return ITEM_TYPE[type] ?? "neutral";
}

const LIFECYCLE_STATUS: Record<string, Variant> = {
  active: "success",
  archived: "neutral",
  retired: "neutral",
};

/** Shared by any page whose status is just active/archived-or-retired/draft
 * (work centers, machines, operators, routings, BOMs) - one lifecycle shape
 * reused instead of each page re-deriving it. */
export function lifecycleStatusVariant(status: string): Variant {
  return LIFECYCLE_STATUS[status] ?? "warning"; // draft / inactive
}

// pending is amber (needs attention), approved green, rejected red - same
// success/warning/danger semantics the rest of the app uses for statuses.
export function approvalStatusVariant(status: string): Variant {
  if (status === "approved") return "success";
  if (status === "rejected") return "danger";
  return "warning"; // pending
}

export function lotStatusVariant(status: string): Variant {
  if (status === "on_hold") return "warning";
  if (status === "depleted") return "neutral";
  return "success"; // active
}

/** "partially_received" -> "Partially Received" */
export function humanize(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
