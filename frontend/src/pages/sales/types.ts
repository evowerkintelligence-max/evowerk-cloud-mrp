export interface Customer {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  is_active: boolean;
}

export interface SalesOrderLine {
  id: string;
  item_id: string;
  item_sku: string;
  item_name: string;
  /** Decimal string from the backend, e.g. "40.0000" - render with trimQty. */
  quantity_ordered: string;
  quantity_delivered: string;
  sequence: number;
}

export interface SalesOrder {
  id: string;
  customer_id: string;
  customer_name: string;
  so_number: number;
  status: string;
  needed_by: string;
  notes: string | null;
}

export interface SalesOrderDetail extends SalesOrder {
  lines: SalesOrderLine[];
}
