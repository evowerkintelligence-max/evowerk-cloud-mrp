import { describe, expect, it } from "vitest";
import { describeOrderResult } from "./orderResult";

const PO = { po_id: "po-1", po_number: 12, supplier_name: "Sealtech", line_count: 2 };
const UNASSIGNED = { item_id: "m-2", item_sku: "FAST-M8", item_name: "M8 fastener" };

describe("describeOrderResult", () => {
  it("returns null when there is no result at all", () => {
    expect(describeOrderResult(undefined)).toBeNull();
    expect(describeOrderResult(null)).toBeNull();
  });

  it("shows created POs and no warning or empty message when everything was assigned", () => {
    const view = describeOrderResult({ created: [PO], unassigned: [] });
    expect(view).not.toBeNull();
    expect(view!.created).toEqual([PO]);
    expect(view!.showUnassignedWarning).toBe(false);
    expect(view!.showEmptyCreatedMessage).toBe(false);
    expect(view!.showProcurementPointer).toBe(true);
  });

  it("shows both created POs and the unassigned warning when some items had no supplier", () => {
    const view = describeOrderResult({ created: [PO], unassigned: [UNASSIGNED] });
    expect(view!.created).toEqual([PO]);
    expect(view!.unassigned).toEqual([UNASSIGNED]);
    expect(view!.showUnassignedWarning).toBe(true);
    expect(view!.showEmptyCreatedMessage).toBe(false);
    expect(view!.showProcurementPointer).toBe(true);
  });

  it("does not say 'nothing to order' when everything was unassigned - the warning carries the message", () => {
    const view = describeOrderResult({ created: [], unassigned: [UNASSIGNED] });
    expect(view!.showUnassignedWarning).toBe(true);
    expect(view!.showEmptyCreatedMessage).toBe(false);
    expect(view!.showProcurementPointer).toBe(false);
  });

  it("shows the empty-created message only when nothing was created and nothing was unassigned", () => {
    const view = describeOrderResult({ created: [], unassigned: [] });
    expect(view!.showEmptyCreatedMessage).toBe(true);
    expect(view!.showUnassignedWarning).toBe(false);
    expect(view!.showProcurementPointer).toBe(false);
  });
});
