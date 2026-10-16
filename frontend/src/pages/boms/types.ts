export interface ItemResponse {
  id: string;
  sku: string;
  name: string;
  item_type: string;
  unit_of_measure: string;
}

export interface ItemUnitResponse {
  id: string;
  item_id: string;
  code: string;
  factor_to_base: string;
}

export interface BomResponse {
  id: string;
  item_id: string;
  item_sku: string;
  item_name: string;
  version: number;
  status: string;
  updated_at: string;
}

export interface BomLineResponse {
  id: string;
  component_item_id: string;
  component_sku: string;
  component_name: string;
  unit_of_measure: string;
  quantity: string;
  sequence: number;
  scrap_pct: string;
  unit_id: string | null;
  unit_quantity: string | null;
  unit_code: string | null;
}

export interface BomDetailResponse extends BomResponse {
  lines: BomLineResponse[];
}

// BomLine.quantity is NUMERIC(12,4) server-side (8 integer digits max) -
// mirrored here so a typo gets instant browser feedback instead of a round
// trip to the 400 the backend now also enforces.
export const MAX_LINE_QUANTITY = 99999999.9999;
export const MAX_SCRAP_PCT = 100;
