export const PAGE_SIZE = 20;

export interface ItemResponse {
  id: string;
  sku: string;
  name: string;
}

export interface SupplierResponse {
  id: string;
  name: string;
}

export interface PurchaseOrderResponse {
  id: string;
  supplier_id: string;
  supplier_name: string;
  po_number: number;
  status: string;
  expected_delivery_date: string | null;
}

export interface PurchaseOrderLineResponse {
  id: string;
  item_id: string;
  item_sku: string;
  item_name: string;
  item_is_lot_tracked: boolean;
  quantity_ordered: string;
  quantity_received: string;
  unit_price: string;
  sequence: number;
}

export interface PurchaseOrderDetailResponse extends PurchaseOrderResponse {
  lines: PurchaseOrderLineResponse[];
}

export const CANCELLABLE_STATUSES = ["draft", "submitted"];
export const RECEIVABLE_STATUSES = ["submitted", "partially_received"];

/** A submitted or partially-received PO whose expected delivery date has
 * passed. Same treatment JobsPanel gives an overdue work order - a draft PO
 * is excluded because it was never sent to the supplier in the first place. */
export function isLate(po: Pick<PurchaseOrderResponse, "status" | "expected_delivery_date">): boolean {
  if (!RECEIVABLE_STATUSES.includes(po.status)) return false;
  if (po.expected_delivery_date === null) return false;
  return po.expected_delivery_date < new Date().toISOString().slice(0, 10);
}
