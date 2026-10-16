// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { PurchaseOrdersPanel } from "./PurchaseOrders";

const ITEMS = [
  { id: "i1", sku: "BOLT-1", name: "Hex bolt" },
  { id: "i2", sku: "RESIN", name: "Epoxy resin" },
];
const SUPPLIERS = [
  { id: "s1", name: "Acme Steel" },
  { id: "s2", name: "Sealtech" },
];

function po(over: Record<string, unknown> = {}) {
  return {
    id: "po1",
    supplier_id: "s1",
    supplier_name: "Acme Steel",
    po_number: 7,
    status: "draft",
    expected_delivery_date: null,
    ...over,
  };
}

const LINE = {
  id: "l1",
  item_id: "i1",
  item_sku: "BOLT-1",
  item_name: "Hex bolt",
  item_is_lot_tracked: false,
  quantity_ordered: "10",
  quantity_received: "0",
  unit_price: "1.5",
  sequence: 0,
};
const LOT_LINE = {
  ...LINE,
  id: "l2",
  item_id: "i2",
  item_sku: "RESIN",
  item_name: "Epoxy resin",
  item_is_lot_tracked: true,
  sequence: 1,
};

function base(extra: Record<string, unknown> = {}) {
  return mockApi({
    "/items": ITEMS,
    "/suppliers": SUPPLIERS,
    "/purchase-orders": [po()],
    ...extra,
  });
}

async function openPo(name = "PO-7") {
  await userEvent.click(await screen.findByRole("cell", { name }));
}

describe("PurchaseOrdersPanel", () => {
  it("shows the empty state when there are no purchase orders", async () => {
    base({ "/purchase-orders": [] });
    renderPage(<PurchaseOrdersPanel />);
    expect(await screen.findByText("No purchase orders yet")).toBeTruthy();
  });

  it("lists POs, flags late ones, and requests active suppliers only", async () => {
    const api = base({
      "/purchase-orders": [
        po(),
        po({ id: "po2", po_number: 8, status: "submitted", expected_delivery_date: "2000-01-01", supplier_name: "Sealtech" }),
      ],
    });
    renderPage(<PurchaseOrdersPanel />);
    const row = (await screen.findByRole("cell", { name: "PO-8" })).closest("tr")!;
    expect(within(row).getByText("Sealtech")).toBeTruthy();
    expect(within(row).getByText("Late")).toBeTruthy();
    expect(within(row).getByText("2000-01-01")).toBeTruthy();
    const draftRow = screen.getByRole("cell", { name: "PO-7" }).closest("tr")!;
    expect(within(draftRow).queryByText("Late")).toBeNull();
    expect(api.calls.find((c) => c.path === "/suppliers")!.search).toBe("?active_only=true");
  });

  it("creates a draft PO for the chosen supplier and opens it", async () => {
    const api = base({
      "POST /purchase-orders": po({ id: "po9", po_number: 9, supplier_id: "s2", supplier_name: "Sealtech" }),
      "/purchase-orders/po9": { ...po({ id: "po9", po_number: 9, supplier_name: "Sealtech" }), lines: [] },
    });
    renderPage(<PurchaseOrdersPanel />);
    await screen.findByRole("option", { name: "Sealtech" });
    await userEvent.selectOptions(screen.getByLabelText("For supplier"), "s2");
    await userEvent.click(screen.getByRole("button", { name: "Create draft" }));

    expect(await screen.findByText(/PO-9 · Sealtech/)).toBeTruthy();
    expect(api.calls.find((c) => c.method === "POST" && c.path === "/purchase-orders")!.body).toEqual({
      supplier_id: "s2",
    });
  });

  it("shows the server's error when creating a PO fails", async () => {
    base({
      "POST /purchase-orders": new Response(JSON.stringify({ detail: "Supplier is inactive" }), { status: 400 }),
    });
    renderPage(<PurchaseOrdersPanel />);
    await screen.findByRole("option", { name: "Acme Steel" });
    await userEvent.selectOptions(screen.getByLabelText("For supplier"), "s1");
    await userEvent.click(screen.getByRole("button", { name: "Create draft" }));
    expect(await screen.findByText("Supplier is inactive")).toBeTruthy();
  });

  it("adds a line to a draft, sending the existing lines plus the new one", async () => {
    const api = base({
      "/purchase-orders/po1": { ...po(), lines: [LINE] },
      "PATCH /purchase-orders/po1": { ...po(), lines: [LINE] },
    });
    renderPage(<PurchaseOrdersPanel />);
    await openPo();
    expect(await screen.findByRole("cell", { name: "BOLT-1 - Hex bolt" })).toBeTruthy();

    await userEvent.selectOptions(screen.getByLabelText("Item"), "i2");
    await userEvent.clear(screen.getByLabelText("Quantity"));
    await userEvent.type(screen.getByLabelText("Quantity"), "4");
    await userEvent.clear(screen.getByLabelText("Unit price"));
    await userEvent.type(screen.getByLabelText("Unit price"), "2.25");
    await userEvent.click(screen.getByRole("button", { name: "Add line" }));

    await waitFor(() => expect(api.calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(api.calls.find((c) => c.method === "PATCH")!.body).toEqual([
      { item_id: "i1", quantity_ordered: "10", unit_price: "1.5", sequence: 0 },
      { item_id: "i2", quantity_ordered: "4", unit_price: "2.25", sequence: 1 },
    ]);
    // form resets after success
    await waitFor(() => expect((screen.getByLabelText("Quantity") as HTMLInputElement).value).toBe("1"));
  });

  it("surfaces a failed line update", async () => {
    base({
      "/purchase-orders/po1": { ...po(), lines: [] },
      "PATCH /purchase-orders/po1": new Response("{}", { status: 422 }),
    });
    renderPage(<PurchaseOrdersPanel />);
    await openPo();
    await screen.findByRole("button", { name: "Add line" });
    await userEvent.selectOptions(screen.getByLabelText("Item"), "i1");
    await userEvent.click(screen.getByRole("button", { name: "Add line" }));
    expect(await screen.findByText("Failed to update lines")).toBeTruthy();
  });

  it("submits a draft after confirming", async () => {
    const api = base({
      "/purchase-orders/po1": { ...po(), lines: [LINE] },
      "POST /purchase-orders/po1/submit": po({ status: "submitted" }),
    });
    renderPage(<PurchaseOrdersPanel />);
    await openPo();
    await userEvent.click(await screen.findByRole("button", { name: "Submit" }));
    expect(await screen.findByText("Submit purchase order?")).toBeTruthy();

    // "Never mind" closes without sending anything
    await userEvent.click(screen.getByRole("button", { name: "Never mind" }));
    expect(screen.queryByText("Submit purchase order?")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Submit" }));
    const dialog = screen.getByText("Submit purchase order?").parentElement!.parentElement!;
    await userEvent.click(within(dialog).getByRole("button", { name: "Submit" }));
    await waitFor(() => expect(screen.queryByText("Submit purchase order?")).toBeNull());
    expect(api.calls.filter((c) => c.path === "/purchase-orders/po1/submit")).toHaveLength(1);
  });

  it("keeps the submit dialog open and shows the error when submit fails", async () => {
    base({
      "/purchase-orders/po1": { ...po(), lines: [] },
      "POST /purchase-orders/po1/submit": new Response(JSON.stringify({ detail: "PO has no lines" }), {
        status: 409,
      }),
    });
    renderPage(<PurchaseOrdersPanel />);
    await openPo();
    await userEvent.click(await screen.findByRole("button", { name: "Submit" }));
    const dialog = screen.getByText("Submit purchase order?").parentElement!.parentElement!;
    await userEvent.click(within(dialog).getByRole("button", { name: "Submit" }));
    expect(await screen.findByText("PO has no lines")).toBeTruthy();
    expect(screen.getByText("Submit purchase order?")).toBeTruthy();
  });

  it("cancels a submitted PO after confirming", async () => {
    const api = base({
      "/purchase-orders": [po({ status: "submitted" })],
      "/purchase-orders/po1": { ...po({ status: "submitted" }), lines: [LINE] },
      "POST /purchase-orders/po1/cancel": po({ status: "cancelled" }),
    });
    renderPage(<PurchaseOrdersPanel />);
    await openPo();
    // submitted PO: no Submit or Add line, but Cancel and Receive
    await screen.findByRole("button", { name: "Cancel" });
    expect(screen.queryByRole("button", { name: "Add line" })).toBeNull();
    expect(screen.getByText("Receive stock")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText(/will be cancelled/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Cancel PO" }));
    await waitFor(() => expect(screen.queryByText("Cancel purchase order?")).toBeNull());
    expect(api.calls.some((c) => c.method === "POST" && c.path === "/purchase-orders/po1/cancel")).toBe(true);
  });

  it("shows the error when cancelling fails", async () => {
    base({
      "/purchase-orders/po1": { ...po(), lines: [] },
      "POST /purchase-orders/po1/cancel": new Response("not json", { status: 500 }),
    });
    renderPage(<PurchaseOrdersPanel />);
    await openPo();
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel PO" }));
    expect(await screen.findByText("Failed to cancel purchase order")).toBeTruthy();
  });

  it("receives a non-lot line with rejects, nulling lot fields", async () => {
    const api = base({
      "/purchase-orders": [po({ status: "partially_received" })],
      "/purchase-orders/po1": { ...po({ status: "partially_received" }), lines: [LINE, LOT_LINE] },
      "POST /purchase-orders/po1/receive": {},
    });
    renderPage(<PurchaseOrdersPanel />);
    await openPo();
    await screen.findByText("Receive stock");
    // partially received: not cancellable
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();

    await userEvent.selectOptions(screen.getByLabelText("Line"), "l1");
    expect(screen.queryByLabelText("Lot number")).toBeNull();
    await userEvent.clear(screen.getByLabelText("Quantity received"));
    await userEvent.type(screen.getByLabelText("Quantity received"), "5");
    await userEvent.clear(screen.getByLabelText("Rejected quantity"));
    await userEvent.type(screen.getByLabelText("Rejected quantity"), "1");
    await userEvent.type(screen.getByLabelText("Reject reason (optional)"), "Dented");
    await userEvent.click(screen.getByRole("button", { name: "Receive" }));

    await waitFor(() => expect(api.calls.some((c) => c.path === "/purchase-orders/po1/receive")).toBe(true));
    expect(api.calls.find((c) => c.path === "/purchase-orders/po1/receive")!.body).toEqual([
      { po_line_id: "l1", quantity: "5", quantity_rejected: "1", reject_reason: "Dented", lot_number: null, expiry_date: null },
    ]);
    await waitFor(() => expect((screen.getByLabelText("Line") as HTMLSelectElement).value).toBe(""));
  });

  it("asks for lot number and expiry on a lot-tracked line", async () => {
    const api = base({
      "/purchase-orders": [po({ status: "submitted" })],
      "/purchase-orders/po1": { ...po({ status: "submitted" }), lines: [LINE, LOT_LINE] },
      "POST /purchase-orders/po1/receive": new Response(JSON.stringify({ detail: "Over-receipt" }), { status: 400 }),
    });
    renderPage(<PurchaseOrdersPanel />);
    await openPo();
    await screen.findByText("Receive stock");
    await userEvent.selectOptions(screen.getByLabelText("Line"), "l2");
    await userEvent.type(screen.getByLabelText("Lot number"), "LOT-42");
    await userEvent.type(screen.getByLabelText("Expiry date (optional)"), "2027-03-01");
    await userEvent.click(screen.getByRole("button", { name: "Receive" }));

    expect(await screen.findByText("Over-receipt")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/purchase-orders/po1/receive")!.body).toEqual([
      { po_line_id: "l2", quantity: "1", quantity_rejected: "0", reject_reason: null, lot_number: "LOT-42", expiry_date: "2027-03-01" },
    ]);
  });

  it("searches and paginates, resetting to page 1 on a new search", async () => {
    const full = Array.from({ length: 20 }, (_, i) => po({ id: `p${i}`, po_number: i + 1 }));
    const api = base({
      "/purchase-orders": ({ url }: { url: URL }) => (url.searchParams.get("offset") === "0" ? full : [po({ id: "x", po_number: 99 })]),
    });
    renderPage(<PurchaseOrdersPanel />);
    await screen.findByRole("cell", { name: "PO-20" });
    expect(screen.getByRole("button", { name: "Previous" }).hasAttribute("disabled")).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByRole("cell", { name: "PO-99" })).toBeTruthy();
    expect(screen.getByText("Page 2")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Next" }).hasAttribute("disabled")).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    await screen.findByText("Page 1");

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Page 2");
    await userEvent.type(screen.getByPlaceholderText("Search PO # or supplier…"), "a");
    expect(await screen.findByText("Page 1")).toBeTruthy();
    const last = api.calls.filter((c) => c.path === "/purchase-orders").pop()!;
    expect(new URLSearchParams(last.search).get("q")).toBe("a");
    expect(new URLSearchParams(last.search).get("offset")).toBe("0");
    expect(new URLSearchParams(last.search).get("limit")).toBe("20");
  });
});
