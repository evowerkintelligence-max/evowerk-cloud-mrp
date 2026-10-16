// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { InventoryPanel } from "./Inventory";

const LOCATIONS = [
  { id: "loc-a", code: "MAIN", name: "Main store", is_default: true },
  { id: "loc-b", code: "OVF", name: "Overflow", is_default: false },
];

const BOLT = {
  item_id: "it-1",
  item_sku: "BLT-10",
  item_name: "Hex bolt",
  unit_of_measure: "pcs",
  quantity_on_hand: "120",
  by_location: [
    { location_id: "loc-a", location_code: "MAIN", location_name: "Main store", quantity_on_hand: "100" },
    { location_id: "loc-b", location_code: "OVF", location_name: "Overflow", quantity_on_hand: "20" },
  ],
};

const BOLT_DETAIL = {
  ...BOLT,
  transactions: [
    {
      id: "tx-1",
      location_code: "MAIN",
      transaction_type: "cycle_count",
      quantity_delta: "-3",
      reason: "shrinkage",
      counted_quantity: "97",
      expected_quantity: "100",
      reason_note: null,
      created_at: "2026-10-01T10:00:00Z",
    },
    {
      id: "tx-2",
      location_code: "OVF",
      transaction_type: "po_receipt",
      quantity_delta: "20",
      reason: null,
      counted_quantity: null,
      expected_quantity: null,
      reason_note: null,
      created_at: "2026-09-30T10:00:00Z",
    },
  ],
};

function baseRoutes(extra: Record<string, unknown> = {}) {
  return { "/inventory": [BOLT], "/locations": LOCATIONS, "/inventory/it-1": BOLT_DETAIL, ...extra };
}

async function openDetail() {
  await userEvent.click(await screen.findByText("BLT-10 - Hex bolt"));
  await screen.findByText("Total on hand: 120 pcs");
}

function formOf(heading: string) {
  return screen.getByText(heading, { selector: "p" }).closest("form") as HTMLFormElement;
}

describe("InventoryPanel", () => {
  it("shows the empty state when there is no stock", async () => {
    mockApi({ "/inventory": [], "/locations": [] });
    renderPage(<InventoryPanel />);
    expect(await screen.findByText("No stock yet")).toBeTruthy();
  });

  it("lists balances with per-location breakdown and opens the item detail on click", async () => {
    const api = mockApi(baseRoutes());
    renderPage(<InventoryPanel />);

    expect(await screen.findByText("MAIN: 100, OVF: 20")).toBeTruthy();
    await openDetail();

    expect(api.calls.some((c) => c.path === "/inventory/it-1")).toBe(true);
    expect(screen.getByText("MAIN - Main store", { selector: "td" })).toBeTruthy();
    // Transactions: humanized type, reason, count breakdown, and a dash for no reason.
    expect(screen.getByText("Shrinkage", { selector: "td" })).toBeTruthy();
    expect(screen.getByText(/counted 97 · expected 100/)).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.getByRole("link", { name: "View in Planning" }).getAttribute("href")).toBe("/planning");
  });

  it("preselects the item from the ?item= query param", async () => {
    mockApi(baseRoutes());
    renderPage(<InventoryPanel />, { route: "/inventory?item=it-1" });
    expect(await screen.findByText("Total on hand: 120 pcs")).toBeTruthy();
  });

  it("consumes the typed quantity at the chosen location", async () => {
    const api = mockApi(baseRoutes({ "POST /inventory/it-1/consume": BOLT }));
    renderPage(<InventoryPanel />);
    await openDetail();

    const form = within(formOf("Consume"));
    const qty = form.getByLabelText("Quantity");
    await userEvent.clear(qty);
    await userEvent.type(qty, "5");
    await userEvent.selectOptions(form.getByLabelText("At location"), "loc-b");
    await userEvent.click(form.getByRole("button", { name: "Consume" }));

    await waitFor(() => expect(api.calls.some((c) => c.path === "/inventory/it-1/consume")).toBe(true));
    expect(api.calls.find((c) => c.path === "/inventory/it-1/consume")!.body).toEqual({
      quantity: "5",
      location_id: "loc-b",
    });
  });

  it("shows the server error detail when a consume fails", async () => {
    mockApi(
      baseRoutes({
        "POST /inventory/it-1/consume": new Response(JSON.stringify({ detail: "Insufficient stock" }), { status: 400 }),
      }),
    );
    renderPage(<InventoryPanel />);
    await openDetail();
    await userEvent.click(within(formOf("Consume")).getByRole("button", { name: "Consume" }));
    expect(await screen.findByText("Insufficient stock")).toBeTruthy();
  });

  it("sends an 'other' adjustment with its note and shows the pending-approval notice", async () => {
    const api = mockApi(
      baseRoutes({
        "POST /inventory/it-1/adjust": { applied: false, item: null, request: { id: "request-0001-pending", status: "pending" } },
      }),
    );
    renderPage(<InventoryPanel />);
    await openDetail();

    const form = within(formOf("Adjust (no count)"));
    const delta = form.getByLabelText("Delta (+/-)");
    await userEvent.clear(delta);
    await userEvent.type(delta, "-2");
    await userEvent.selectOptions(form.getByLabelText("Reason"), "other");
    await userEvent.type(form.getByLabelText("Note"), "Dropped in transit");
    await userEvent.click(form.getByRole("button", { name: "Adjust" }));

    expect(await screen.findByText("Submitted for owner approval (request request-…).")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/inventory/it-1/adjust")!.body).toEqual({
      quantity: "-2",
      location_id: null,
      reason_code: "other",
      reason_note: "Dropped in transit",
    });
  });

  it("applies an adjustment directly and refetches balances", async () => {
    const api = mockApi(baseRoutes({ "POST /inventory/it-1/adjust": { applied: true, item: BOLT, request: null } }));
    renderPage(<InventoryPanel />);
    await openDetail();
    const before = api.calls.filter((c) => c.method === "GET" && c.path === "/inventory").length;

    await userEvent.click(within(formOf("Adjust (no count)")).getByRole("button", { name: "Adjust" }));

    await waitFor(() =>
      expect(api.calls.filter((c) => c.method === "GET" && c.path === "/inventory").length).toBeGreaterThan(before),
    );
    expect(api.calls.find((c) => c.path === "/inventory/it-1/adjust")!.body).toMatchObject({
      quantity: "1",
      reason_code: "correction",
      reason_note: null,
    });
    expect(screen.queryByText(/Submitted for owner approval/)).toBeNull();
  });

  it("posts a cycle count and clears the counted quantity afterwards", async () => {
    const api = mockApi(
      baseRoutes({ "POST /inventory/it-1/count": { applied: false, item: null, request: { id: "req99999xyz", status: "pending" } } }),
    );
    renderPage(<InventoryPanel />);
    await openDetail();

    const form = within(formOf("Count"));
    const qty = form.getByLabelText("Counted quantity") as HTMLInputElement;
    await userEvent.type(qty, "97");
    await userEvent.selectOptions(form.getByLabelText("At location"), "loc-a");
    await userEvent.selectOptions(form.getByLabelText("Reason"), "other");
    await userEvent.type(form.getByLabelText("Note"), "Audit");
    await userEvent.click(form.getByRole("button", { name: "Count" }));

    expect(await screen.findByText("Submitted for owner approval (request req99999…).")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/inventory/it-1/count")!.body).toEqual({
      location_id: "loc-a",
      counted_quantity: "97",
      reason_code: "other",
      reason_note: "Audit",
    });
    expect(qty.value).toBe("");
  });

  it("refuses a transfer without both locations, then posts once both are picked", async () => {
    const api = mockApi(baseRoutes({ "POST /inventory/it-1/transfer": BOLT }));
    renderPage(<InventoryPanel />);
    await openDetail();

    const form = within(formOf("Transfer"));
    await userEvent.click(form.getByRole("button", { name: "Transfer" }));
    expect(await screen.findByText("Pick both a source and destination location")).toBeTruthy();
    expect(api.calls.some((c) => c.path === "/inventory/it-1/transfer")).toBe(false);

    await userEvent.selectOptions(form.getByLabelText("From"), "loc-a");
    await userEvent.selectOptions(form.getByLabelText("To"), "loc-b");
    await userEvent.click(form.getByRole("button", { name: "Transfer" }));

    await waitFor(() => expect(api.calls.some((c) => c.path === "/inventory/it-1/transfer")).toBe(true));
    expect(api.calls.find((c) => c.path === "/inventory/it-1/transfer")!.body).toEqual({
      from_location_id: "loc-a",
      to_location_id: "loc-b",
      quantity: "1",
    });
    expect(screen.queryByText("Pick both a source and destination location")).toBeNull();
  });
});
