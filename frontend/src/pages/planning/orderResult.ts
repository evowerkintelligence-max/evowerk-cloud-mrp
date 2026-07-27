export interface CreatedPo {
  po_id: string;
  po_number: number;
  supplier_name: string;
  line_count: number;
}

export interface UnassignedItem {
  item_id: string;
  item_sku: string;
  item_name: string;
}

export interface GeneratePosResult {
  created: CreatedPo[];
  unassigned: UnassignedItem[];
}

export interface OrderResultView {
  created: CreatedPo[];
  unassigned: UnassignedItem[];
  showUnassignedWarning: boolean;
  // "Nothing to order." only makes sense when nothing was created and the
  // reason isn't already explained by the unassigned warning above it.
  showEmptyCreatedMessage: boolean;
  showProcurementPointer: boolean;
}

/** Turns a /mrp/generate-pos response into what the result block should show.
 * Returns null when there's no result to show at all. */
export function describeOrderResult(result: GeneratePosResult | undefined | null): OrderResultView | null {
  if (!result) return null;
  const showUnassignedWarning = result.unassigned.length > 0;
  return {
    created: result.created,
    unassigned: result.unassigned,
    showUnassignedWarning,
    showEmptyCreatedMessage: result.created.length === 0 && !showUnassignedWarning,
    showProcurementPointer: result.created.length > 0,
  };
}
