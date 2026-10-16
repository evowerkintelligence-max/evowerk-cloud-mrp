// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { CustomersPanel } from "./CustomersPanel";

const ME = (role: string) => ({ id: "org1", name: "Org", role, user_id: "u1", enabled_features: [] });
const ACME = { id: "c1", name: "Acme Foods", email: "buy@acme.test", phone: null, is_active: true };
const BETA = { id: "c2", name: "Beta Retail", email: null, phone: "555-0101", is_active: false };

const rowOf = (name: string) => within(screen.getByText(name).closest("tr")!);

describe("CustomersPanel", () => {
  it("lists customers with status and contact fallbacks; members get no edit controls", async () => {
    mockApi({ "/orgs/me": ME("member"), "/customers": [ACME, BETA] });
    renderPage(<CustomersPanel />);

    expect(await screen.findByText("Acme Foods")).toBeTruthy();
    expect(rowOf("Acme Foods").getByText("Active")).toBeTruthy();
    expect(rowOf("Beta Retail").getByText("Inactive")).toBeTruthy();
    expect(rowOf("Beta Retail").getByText("555-0101")).toBeTruthy();
    expect(rowOf("Acme Foods").getByText("—")).toBeTruthy();
    expect(screen.queryByText("Add a customer")).toBeNull();
    expect(screen.queryByRole("button", { name: "Deactivate" })).toBeNull();
  });

  it("sends search text and the active-only flag as query params", async () => {
    const api = mockApi({
      "/orgs/me": ME("member"),
      "/customers": ({ url }: { url: URL }) => (url.searchParams.get("q") === "zzz" ? [] : [ACME]),
    });
    renderPage(<CustomersPanel />);
    await screen.findByText("Acme Foods");

    await userEvent.click(screen.getByRole("checkbox", { name: "Active only" }));
    await userEvent.type(screen.getByPlaceholderText("Search customers…"), "zzz");

    expect(await screen.findByText("No customers yet")).toBeTruthy();
    const last = new URLSearchParams(api.calls.filter((c) => c.path === "/customers").slice(-1)[0].search);
    expect(last.get("q")).toBe("zzz");
    expect(last.get("active_only")).toBe("true");
  });

  it("adds a customer, sending blank contact fields as null, and clears the form", async () => {
    const api = mockApi({ "/orgs/me": ME("owner"), "/customers": [], "POST /customers": ACME });
    renderPage(<CustomersPanel />);
    const name = (await screen.findByLabelText("Name")) as HTMLInputElement;

    await userEvent.type(name, "Gamma Ltd");
    await userEvent.type(screen.getByLabelText("Phone"), "555-0199");
    await userEvent.click(screen.getByRole("button", { name: "Add customer" }));

    await waitFor(() => expect(name.value).toBe(""));
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      name: "Gamma Ltd",
      email: null,
      phone: "555-0199",
    });
  });

  it("shows the server error when adding fails", async () => {
    mockApi({
      "/orgs/me": ME("manager"),
      "/customers": [],
      "POST /customers": new Response(JSON.stringify({ detail: "Customer name already used" }), { status: 409 }),
    });
    renderPage(<CustomersPanel />);
    await userEvent.type(await screen.findByLabelText("Name"), "Acme Foods");
    await userEvent.click(screen.getByRole("button", { name: "Add customer" }));
    expect(await screen.findByText("Customer name already used")).toBeTruthy();
  });

  it("deactivates an active customer and reports a failed reactivation", async () => {
    const api = mockApi({
      "/orgs/me": ME("owner"),
      "/customers": [ACME, BETA],
      "POST /customers/c1/deactivate": { ...ACME, is_active: false },
      "POST /customers/c2/reactivate": new Response(JSON.stringify({ detail: "Customer is archived" }), { status: 409 }),
    });
    renderPage(<CustomersPanel />);
    await screen.findByText("Acme Foods");

    await userEvent.click(rowOf("Acme Foods").getByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(api.calls.some((c) => c.path === "/customers/c1/deactivate")).toBe(true));

    await userEvent.click(rowOf("Beta Retail").getByRole("button", { name: "Reactivate" }));
    expect(await screen.findByText("Customer is archived")).toBeTruthy();
  });
});
