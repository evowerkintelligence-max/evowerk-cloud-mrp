import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Sales } from "./Sales";

const CUSTOMER = {
  id: "cust-1",
  name: "Northwind Ltd",
  email: "buyer@northwind.test",
  phone: null,
  is_active: true,
};

const OPEN_ORDER = {
  id: "so-1",
  customer_id: "cust-1",
  customer_name: "Northwind Ltd",
  so_number: 7,
  status: "open",
  needed_by: "2030-06-01",
  notes: null,
};

const OPEN_ORDER_DETAIL = {
  ...OPEN_ORDER,
  lines: [
    {
      id: "sol-1",
      item_id: "item-1",
      item_sku: "WIDGET-1",
      item_name: "Widget",
      quantity_ordered: "40.0000",
      quantity_delivered: "0.0000",
      sequence: 0,
    },
  ],
};

function render(tab: string, seed: (qc: QueryClient) => void = () => {}, role = "owner") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["org-me"], {
    id: "org-1",
    name: "Acme",
    role,
    user_id: "user-1",
    enabled_features: ["sales"],
  });
  seed(qc);
  // renderToString splits adjacent text nodes with <!-- --> markers - strip them
  // so assertions can match the text a user actually sees.
  return renderToString(
    createElement(
      MemoryRouter,
      { initialEntries: [`/sales?tab=${tab}`] },
      createElement(QueryClientProvider, { client: qc }, createElement(Sales)),
    ),
  ).split("<!-- -->").join("");
}

describe("Sales", () => {
  it("renders each tab empty without crashing", () => {
    for (const tab of ["orders", "customers"]) {
      const html = render(tab);
      expect(html).toContain("Sales");
      expect(html).toContain("Orders");
      expect(html).toContain("Customers");
    }
  });

  it("orders tab lists an order with its number, customer, product and status", () => {
    const html = render("orders", (qc) => {
      qc.setQueryData(["sales-orders", "", ""], [OPEN_ORDER]);
      qc.setQueryData(["sales-orders", "so-1"], OPEN_ORDER_DETAIL);
    });
    expect(html).toContain("Raise an order");
    expect(html).toContain("SO-7");
    expect(html).toContain("Northwind Ltd");
    expect(html).toContain("WIDGET-1");
    expect(html).toContain("40");
    expect(html).toContain("2030-06-01");
    expect(html).toContain("Open");
  });

  it("orders tab shows the empty state when there are none", () => {
    const html = render("orders", (qc) => qc.setQueryData(["sales-orders", "", ""], []));
    expect(html).toContain("No orders yet");
  });

  it("customers tab lists a customer and its create form", () => {
    const html = render("customers", (qc) =>
      qc.setQueryData(["customers", "", false], [CUSTOMER]),
    );
    expect(html).toContain("Add a customer");
    expect(html).toContain("Northwind Ltd");
    expect(html).toContain("buyer@northwind.test");
    expect(html).toContain("Active");
  });

  it("customers tab shows the empty state when there are none", () => {
    const html = render("customers", (qc) => qc.setQueryData(["customers", "", false], []));
    expect(html).toContain("No customers yet");
  });

  it("a member sees no create form on either tab", () => {
    // Assert on the form's own field ids, not its heading: the empty-state
    // copy on both tabs contains the same words as the heading does.
    const orders = render("orders", (qc) => qc.setQueryData(["sales-orders", "", ""], []), "member");
    expect(orders).not.toContain("order-customer");
    const customers = render(
      "customers",
      (qc) => qc.setQueryData(["customers", "", false], []),
      "member",
    );
    expect(customers).not.toContain("customer-name");
  });

  it("mark done appears for an open order but not one already done", () => {
    const open = render("orders", (qc) => {
      qc.setQueryData(["sales-orders", "", ""], [OPEN_ORDER]);
      qc.setQueryData(["sales-orders", "so-1"], OPEN_ORDER_DETAIL);
    });
    expect(open).toContain("Mark done");

    const done = render("orders", (qc) => {
      qc.setQueryData(["sales-orders", "", ""], [{ ...OPEN_ORDER, status: "done" }]);
      qc.setQueryData(["sales-orders", "so-1"], { ...OPEN_ORDER_DETAIL, status: "done" });
    });
    expect(done).toContain("Done");
    expect(done).not.toContain("Mark done");
  });
});
