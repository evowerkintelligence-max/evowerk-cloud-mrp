export interface WorkOrderComponent {
  item_id: string;
  item_sku: string;
  item_name: string;
  quantity_required: string;
  quantity_consumed: string;
  sequence: number;
}

export interface WorkOrder {
  id: string;
  wo_number: number;
  item_id: string;
  item_sku: string;
  item_name: string;
  /** Drives whether the finish form asks for a lot number. */
  item_is_lot_tracked: boolean;
  status: "scheduled" | "in_progress" | "done" | "cancelled";
  quantity_planned: string;
  quantity_good: string;
  quantity_scrapped: string;
  scrap_reason: string | null;
  /** not_started | consumed, set only when the job finished under plan. */
  remainder_disposition: string | null;
  location_id: string;
  location_code: string;
  scheduled_start: string | null;
  started_at: string | null;
  finished_at: string | null;
  notes: string | null;
  assigned_operator_id: string | null;
  components: WorkOrderComponent[];
}
