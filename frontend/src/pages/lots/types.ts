export interface ItemResponse {
  id: string;
  sku: string;
  name: string;
}

export interface LocationResponse {
  id: string;
  code: string;
  name: string;
  is_default: boolean;
}

export interface LotResponse {
  id: string;
  item_id: string;
  item_sku: string;
  item_name: string;
  location_id: string;
  location_code: string;
  lot_number: string;
  quantity_received: string;
  quantity_remaining: string;
  status: string;
  expiry_date: string | null;
}

export interface LotTransactionResponse {
  id: string;
  transaction_type: string;
  quantity: string;
  reason: string | null;
  created_by: string;
  created_at: string;
}

export interface LotDetailResponse extends LotResponse {
  transactions: LotTransactionResponse[];
}

export interface FefoConsumeLine {
  lot_id: string;
  lot_number: string;
  quantity_consumed: string;
  quantity_remaining: string;
  expiry_date: string | null;
}
