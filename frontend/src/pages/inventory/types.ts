export const REASON_CODES = [
  { value: "cycle_count", label: "Cycle count" },
  { value: "damage", label: "Damage" },
  { value: "found_stock", label: "Found stock" },
  { value: "shrinkage", label: "Shrinkage" },
  { value: "correction", label: "Correction" },
  { value: "other", label: "Other" },
] as const;
export type ReasonCode = (typeof REASON_CODES)[number]["value"];

export interface LocationResponse {
  id: string;
  code: string;
  name: string;
  is_default: boolean;
}

export interface LocationQuantity {
  location_id: string;
  location_code: string;
  location_name: string;
  quantity_on_hand: string;
}

export interface InventoryItem {
  item_id: string;
  item_sku: string;
  item_name: string;
  unit_of_measure: string;
  quantity_on_hand: string;
  by_location: LocationQuantity[];
}

export interface InventoryTransaction {
  id: string;
  location_code: string;
  transaction_type: string;
  quantity_delta: string;
  reason: string | null;
  counted_quantity: string | null;
  expected_quantity: string | null;
  reason_note: string | null;
  created_at: string;
}

export interface InventoryDetail extends InventoryItem {
  transactions: InventoryTransaction[];
}

export interface AdjustmentRequestSummary {
  id: string;
  status: string;
}

export interface AdjustmentOutcome {
  applied: boolean;
  item: InventoryItem | null;
  request: AdjustmentRequestSummary | null;
}
