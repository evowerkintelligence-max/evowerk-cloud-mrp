export interface ItemResponse {
  id: string;
  sku: string;
  name: string;
  item_type: string;
  unit_of_measure: string;
  is_lot_tracked: boolean;
  preferred_supplier_id: string | null;
  lead_time_days: number | null;
  min_order_qty: string | null;
  safety_stock_qty: string | null;
  is_active: boolean;
  description: string | null;
  barcode: string | null;
}

export interface SupplierResponse {
  id: string;
  name: string;
}

export interface DatasetResponse {
  id: string;
  name: string;
}

export interface OrgMeResponse {
  id: string;
  name: string;
  role: string;
}

export interface AuditLogEntry {
  action: string;
  actor_email: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface ImportResult {
  created: { row: number; sku: string }[];
  errors: { row: number; sku: string | null; message: string }[];
}

export const TABLE_COLUMN_COUNT = 13;
