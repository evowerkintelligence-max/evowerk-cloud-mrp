// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { OrdersPanel } from "./OrdersPanel";

const ME = (role: string) => ({ id: "org1", name: "Org", role, user_id: "u1", enabled_features: [] });
const CUSTOMERS = [{ id: "c1", name: "Acme Foods", email: null, phone: null, is_active: true }];
const ITEMS = [{ id: "p1", sku: "WID-1", name: "Widget" }];

function order(over: Record<string, unknown> = {}) {
  return {
    id: "so1",
    customer_id: "c1",
    customer_name: "Acme Foods",
    so_number: 42,
    status: "open",
    needed_by: "2026-11-15",
    notes: null,
    is_overdue: false,
    lines: [
      {
        id: "l1",
        item_id: "p1",
        item_sku: "WID-1",
        item_name: "Widget",
        quantity_ordered: "40.0000",
        quantity_delivered: "0",
        sequence: 0,
      },
    ],
    ...over,
  };
}

function routes(role: string, extra: Record<string, unknown> = {}) {
  return { "/orgs/me": ME(role), "/sales-orders": [order()], "/customers": CUSTOMERS, "/items": ITEMS, ...extra };
}

/** The confirm dialog renders after the table, so its button is the last match. */
const lastButton = (name: string) => screen.getAllByRole("button", { name }).slice(-1)[0];

async function fillOrderForm() {
  await screen.findByRole("option", { name: "Acme Foods" });
  await screen.findByRole("option", { name: /WID-1/ });
  await userEvent.selectOptions(screen.getByLabelText("Customer"), "c1");
  await userEvent.selectOptions(screen.getByLabelText("Product"), "p1");
  fireEvent.change(screen.getByLabelText("Needed by"), { target: { value: "2026-12-01" } });
}

describe("OrdersPanel", () => {
  it("lists orders with their lines, status, and a late badge; members cannot act", async () => {
    mockApi(
      routes("member", {
        "/sales-orders": [order(), order({ id: "so2", so_number: 43, status: "done", is_overdue: true, lines: [] })],
      }),
    );
    renderPage(<OrdersPanel />);

    expect(await screen.findByText("SO-42")).toBeTruthy();
    expect(screen.getByText("WID-1 × 40")).toBeTruthy();
    expect(within(screen.getByText("SO-43").closest("tr")!).getByText("Late")).toBeTruthy();
    expect(screen.queryByText("Raise an order")).toBeNull();
    expect(screen.queryByRole("button", { name: "Mark done" })).toBeNull();
  });

  it("filters by search text and status, showing the empty state", async () => {
    const api = mockApi(
      routes("member", {
        "/sales-orders": ({ url }: { url: URL }) => (url.searchParams.get("status") === "cancelled" ? [] : [order()]),
      }),
    );
    renderPage(<OrdersPanel />);
    await screen.findByText("SO-42");

    await userEvent.type(screen.getByPlaceholderText("Search orders…"), "acme");
    await userEvent.selectOptions(screen.getByDisplayValue("All statuses"), "cancelled");

    expect(await screen.findByText("No orders yet")).toBeTruthy();
    const last = new URLSearchParams(api.calls.filter((c) => c.path === "/sales-orders").slice(-1)[0].search);
    expect(last.get("q")).toBe("acme");
    expect(last.get("status")).toBe("cancelled");
  });

  it("raises an order with one line and resets the line fields", async () => {
    const api = mockApi(routes("owner", { "POST /sales-orders": order({ id: "so3", so_number: 44 }) }));
    renderPage(<OrdersPanel />);
    await screen.findByText("SO-42");

    await fillOrderForm();
    const qty = screen.getByLabelText("Quantity") as HTMLInputElement;
    await userEvent.clear(qty);
    await userEvent.type(qty, "12");
    await userEvent.click(screen.getByRole("button", { name: "Raise order" }));

    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path === "/sales-orders")).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      customer_id: "c1",
      needed_by: "2026-12-01",
      lines: [{ item_id: "p1", quantity_ordered: "12", sequence: 0 }],
    });
    await waitFor(() => expect(qty.value).toBe("1"));
  });

  it("shows the server error when raising an order fails", async () => {
    mockApi(
      routes("manager", {
        "POST /sales-orders": new Response(JSON.stringify({ detail: "Customer is inactive" }), { status: 400 }),
      }),
    );
    renderPage(<OrdersPanel />);
    await fillOrderForm();
    await userEvent.click(screen.getByRole("button", { name: "Raise order" }));
    expect(await screen.findByText("Customer is inactive")).toBeTruthy();
  });

  it("marks an order done after confirming, and 'Never mind' sends nothing", async () => {
    const api = mockApi(routes("owner", { "POST /sales-orders/so1/complete": order({ status: "done" }) }));
    renderPage(<OrdersPanel />);
    await screen.findByText("SO-42");

    await userEvent.click(screen.getByRole("button", { name: "Mark done" }));
    expect(screen.getByText(/SO-42 will close and the goods will come off stock/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Never mind" }));
    expect(screen.queryByText("Mark this order done?")).toBeNull();
    expect(api.calls.some((c) => c.path === "/sales-orders/so1/complete")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Mark done" }));
    await userEvent.click(lastButton("Mark done"));
    await waitFor(() => expect(screen.queryByText("Mark this order done?")).toBeNull());
    expect(api.calls.filter((c) => c.path === "/sales-orders/so1/complete")).toHaveLength(1);
  });

  it("closes the dialog and shows the shortfall when completing is refused", async () => {
    mockApi(
      routes("owner", {
        "POST /sales-orders/so1/complete": new Response(JSON.stringify({ detail: "Only 10 of 40 WID-1 in stock" }), {
          status: 409,
        }),
      }),
    );
    renderPage(<OrdersPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Mark done" }));
    await userEvent.click(lastButton("Mark done"));
    expect(await screen.findByText("Only 10 of 40 WID-1 in stock")).toBeTruthy();
    expect(screen.queryByText("Mark this order done?")).toBeNull();
  });

  it("cancels an order after confirming, and surfaces a cancel failure", async () => {
    let fail = false;
    const api = mockApi(
      routes("owner", {
        "POST /sales-orders/so1/cancel": () =>
          fail
            ? new Response(JSON.stringify({ detail: "Order already shipped" }), { status: 409 })
            : order({ status: "cancelled" }),
      }),
    );
    renderPage(<OrdersPanel />);

    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(screen.getByText(/SO-42 will be cancelled and stop counting as demand/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Cancel order" }));
    await waitFor(() => expect(screen.queryByText("Cancel this order?")).toBeNull());
    expect(api.calls.filter((c) => c.path === "/sales-orders/so1/cancel")).toHaveLength(1);

    fail = true;
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel order" }));
    expect(await screen.findByText("Order already shipped")).toBeTruthy();
  });
});
