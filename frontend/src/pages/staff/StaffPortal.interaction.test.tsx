// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { useStaffAuthStore } from "@/store/staffAuthStore";
import { CORE_FEATURES } from "@/lib/features";
import { StaffPortal } from "./StaffPortal";

const ACME = { org_id: "o1", org_name: "Acme Steel", owner_email: "owner@example.com", enabled_features: ["boms", "oee"] };
const BARE = { org_id: "o2", org_name: "Bare Co", owner_email: "bare@example.com", enabled_features: [] };

beforeEach(() => useStaffAuthStore.setState({ accessToken: "staff-token" }));
afterEach(() => useStaffAuthStore.setState({ accessToken: null }));

function renderPortal() {
  renderPage(
    <Routes>
      <Route path="/staff" element={<StaffPortal />} />
      <Route path="/login" element={<p>login page</p>} />
    </Routes>,
    { route: "/staff" },
  );
}

function newCustomerCard() {
  return screen.getByText("New customer").closest(".rounded-lg") as HTMLElement;
}

// The form's Field labels have no htmlFor, so find inputs by order.
function inputs(card: HTMLElement) {
  const [orgName, ownerEmail] = within(card).getAllByRole("textbox") as HTMLInputElement[];
  return { orgName, ownerEmail };
}

describe("StaffPortal", () => {
  it("lists customers with their feature labels, using the staff token", async () => {
    const api = mockApi({ "/admin/customers": [ACME, BARE] });
    renderPortal();

    const acme = (await screen.findByText("Acme Steel")).closest("tr")!;
    expect(within(acme).getByText("Bills of Materials")).toBeTruthy();
    expect(within(acme).getByText("OEE")).toBeTruthy();
    expect(within(screen.getByText("Bare Co").closest("tr")!).getByText("None")).toBeTruthy();
    expect(api.fetchMock.mock.calls[0][1]!.headers).toBeInstanceOf(Headers);
    expect((api.fetchMock.mock.calls[0][1]!.headers as Headers).get("Authorization")).toBe("Bearer staff-token");
  });

  it("creates a customer with the chosen features and reloads the list", async () => {
    const api = mockApi({ "/admin/customers": [], "POST /admin/customers": { org_id: "o3" } });
    renderPortal();
    const card = newCustomerCard();
    const create = within(card).getByRole("button", { name: "Create customer" });
    expect((create as HTMLButtonElement).disabled).toBe(true);

    await userEvent.type(inputs(card).orgName, "New Org");
    await userEvent.type(inputs(card).ownerEmail, "new@example.com");
    await userEvent.click(within(card).getByRole("button", { name: "Core + Premium" }));
    await userEvent.click(within(card).getByRole("switch", { name: "AutoML" }));
    await userEvent.click(create);

    await waitFor(() => expect(api.calls.filter((c) => c.path === "/admin/customers" && c.method === "GET")).toHaveLength(2));
    const body = api.calls.find((c) => c.method === "POST")!.body as { org_name: string; owner_email: string; enabled_features: string[] };
    expect(body.org_name).toBe("New Org");
    expect(body.owner_email).toBe("new@example.com");
    expect(body.enabled_features).toContain("computer_vision");
    expect(body.enabled_features).not.toContain("automl");
    // Form resets to the core plan after a successful create.
    expect((inputs(card).orgName as HTMLInputElement).value).toBe("");
    expect(within(card).getByRole("switch", { name: "Simulation" }).getAttribute("aria-checked")).toBe("false");
    expect(within(card).getByRole("switch", { name: "OEE" }).getAttribute("aria-checked")).toBe("true");
  });

  it("the Core preset selects exactly the core features", async () => {
    const api = mockApi({ "/admin/customers": [], "POST /admin/customers": {} });
    renderPortal();
    const card = newCustomerCard();
    await userEvent.click(within(card).getByRole("button", { name: "Core + Premium" }));
    await userEvent.click(within(card).getByRole("button", { name: "Core" }));
    await userEvent.type(inputs(card).orgName, "Core Org");
    await userEvent.type(inputs(card).ownerEmail, "core@example.com");
    await userEvent.click(within(card).getByRole("button", { name: "Create customer" }));

    await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
    const sent = (api.calls.find((c) => c.method === "POST")!.body as { enabled_features: string[] }).enabled_features;
    expect([...sent].sort()).toEqual([...CORE_FEATURES].sort());
  });

  it("edits a customer's features and patches only that customer", async () => {
    const api = mockApi({ "/admin/customers": [ACME], "PATCH /admin/customers/o1": {} });
    renderPortal();
    await userEvent.click(await screen.findByRole("button", { name: "Edit features" }));
    const row = screen.getByRole("button", { name: "Save" }).closest("tr")!;
    await userEvent.click(within(row).getByRole("switch", { name: "OEE" }));
    await userEvent.click(within(row).getByRole("switch", { name: "Sales Orders" }));
    await userEvent.click(within(row).getByRole("button", { name: "Save" }));

    await screen.findByRole("button", { name: "Edit features" });
    const patch = api.calls.find((c) => c.method === "PATCH")!;
    expect(patch.path).toBe("/admin/customers/o1");
    expect([...(patch.body as { enabled_features: string[] }).enabled_features].sort()).toEqual(["boms", "sales"]);
  });

  it("cancelling an edit sends nothing", async () => {
    const api = mockApi({ "/admin/customers": [ACME] });
    renderPortal();
    await userEvent.click(await screen.findByRole("button", { name: "Edit features" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByRole("button", { name: "Edit features" })).toBeTruthy();
    expect(api.calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("logging out clears the staff session and returns to log in", async () => {
    mockApi({ "/admin/customers": [] });
    renderPortal();
    await userEvent.click(screen.getByRole("button", { name: "Log out" }));
    await screen.findByText("login page");
    expect(useStaffAuthStore.getState().accessToken).toBeNull();
  });

  it("a 401 from the admin API drops the staff session", async () => {
    mockApi({ "/admin/customers": new Response("{}", { status: 401 }) });
    renderPortal();
    await waitFor(() => expect(useStaffAuthStore.getState().accessToken).toBeNull());
  });
});
