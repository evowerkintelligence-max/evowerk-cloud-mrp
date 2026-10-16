// @vitest-environment jsdom
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { ToBuyPanel } from "./ToBuyPanel";
import type { GridToBuy } from "./types";

const BOLT: GridToBuy = {
  item_id: "i1",
  item_sku: "BLT-10",
  item_name: "Hex bolt",
  quantity: "40.0000",
  supplier_name: "Acme Steel",
  order_by: "2026-10-20",
  urgency: "soon",
};
const PAINT: GridToBuy = {
  item_id: "i2",
  item_sku: "PNT-1",
  item_name: "Paint",
  quantity: "3",
  supplier_name: null,
  order_by: null,
  urgency: "unknown",
};

/** Steps the rows the panel receives through `steps`, the way grid refetches do. */
function Harness({ steps }: { steps: GridToBuy[][] }) {
  const [i, setI] = useState(0);
  const rows = steps[Math.min(i, steps.length - 1)];
  return (
    <>
      <button type="button" onClick={() => setI((n) => n + 1)}>
        swap rows
      </button>
      <ToBuyPanel rows={rows} canOrder />
    </>
  );
}

describe("ToBuyPanel", () => {
  it("renders nothing when there is nothing to buy", () => {
    mockApi({});
    renderPage(<ToBuyPanel rows={[]} canOrder />);
    expect(screen.queryByText("To buy for the plan above")).toBeNull();
  });

  it("lists each shortfall with supplier, quantity, and order-by date", () => {
    mockApi({});
    renderPage(<ToBuyPanel rows={[BOLT, PAINT]} canOrder={false} />);
    expect(screen.getByText("order 40 from Acme Steel · by 2026-10-20")).toBeTruthy();
    expect(screen.getByText("order 3, no supplier set")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Order all missing materials" })).toBeNull();
  });

  it("orders everything and keeps the result visible after the list empties", async () => {
    const api = mockApi({
      "POST /mrp/generate-pos": {
        created: [{ po_id: "po1", po_number: 17, supplier_name: "Acme Steel", line_count: 1 }],
        unassigned: [{ item_id: "i2", item_sku: "PNT-1", item_name: "Paint" }],
      },
    });
    renderPage(<Harness steps={[[BOLT, PAINT], []]} />);

    await userEvent.click(screen.getByRole("button", { name: "Order all missing materials" }));
    expect(await screen.findByText("Draft PO-17")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/mrp/generate-pos")!.body).toEqual({});
    expect(screen.getByText(/Acme Steel · 1 line$/)).toBeTruthy();
    expect(screen.getByText(/No supplier set for: Paint \(PNT-1\)/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Procurement" }).getAttribute("href")).toBe("/procurement");

    // The successful order empties the rows on refetch; the result must survive it.
    await userEvent.click(screen.getByRole("button", { name: "swap rows" }));
    expect(screen.getByText("Draft PO-17")).toBeTruthy();
  });

  it("clears a stale result when the rows change for another reason", async () => {
    mockApi({ "POST /mrp/generate-pos": { created: [], unassigned: [] } });
    renderPage(<Harness steps={[[BOLT], [], [PAINT]]} />);

    await userEvent.click(screen.getByRole("button", { name: "Order all missing materials" }));
    expect(await screen.findByText("Nothing to order.")).toBeTruthy();

    // First change after the order is the refetch it caused; the next is a real edit.
    await userEvent.click(screen.getByRole("button", { name: "swap rows" }));
    expect(screen.getByText("Nothing to order.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "swap rows" }));
    expect(screen.queryByText("Nothing to order.")).toBeNull();
    expect(screen.getByText("order 3, no supplier set")).toBeTruthy();
  });

  it("shows the server error when ordering fails", async () => {
    mockApi({
      "POST /mrp/generate-pos": new Response(JSON.stringify({ detail: "No open plan" }), { status: 400 }),
    });
    renderPage(<ToBuyPanel rows={[BOLT]} canOrder />);
    await userEvent.click(screen.getByRole("button", { name: "Order all missing materials" }));
    expect(await screen.findByText("No open plan")).toBeTruthy();
  });
});
