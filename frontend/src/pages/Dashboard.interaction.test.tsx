// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Dashboard } from "./Dashboard";

const ME = { id: "o1", name: "Test Org", role: "owner", user_id: "u1", enabled_features: ["data_chat"] };

// PageHeader's Typewriter reads matchMedia, which jsdom lacks.
beforeEach(() => {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q }));
});

function statCard(label: string) {
  return screen.getByText(label).closest(".group") as HTMLElement;
}

describe("Dashboard", () => {
  it("greets the org, counts each list, and lists morning-brief lots", async () => {
    mockApi({
      "/orgs/me": ME,
      "/items": [{}, {}, {}],
      "/boms": [{}],
      "/suppliers": [],
      "/purchase-orders": new Response("{}", { status: 500 }),
      "/inventory/morning-brief": {
        lot_alerts: [
          { lot_id: "l1", item_sku: "SKU-1", lot_number: "A1", location_code: "WH-1", days_until_expiry: 3, reason: "expiring_soon" },
          { lot_id: "l2", item_sku: "SKU-2", lot_number: "B2", location_code: "WH-2", days_until_expiry: null, reason: "on_hold" },
        ],
      },
    });
    renderPage(<Dashboard />);

    expect(await screen.findByText("Test Org")).toBeTruthy();
    await waitFor(() => expect(statCard("Items").textContent).toBe("Items3"));
    expect(statCard("BOMs").textContent).toBe("BOMs1");
    expect(statCard("Suppliers").textContent).toBe("Suppliers0");
    await waitFor(() => expect(statCard("Purchase Orders").textContent).toBe("Purchase Orders—"));

    const expiring = (await screen.findByText("SKU-1 — Lot A1")).closest("a")!;
    expect(expiring.getAttribute("href")).toBe("/lots");
    expect(expiring.textContent).toContain("WH-1 · expires in 3d");
    expect(within(expiring).getByText("Expiring")).toBeTruthy();
    const held = screen.getByText("SKU-2 — Lot B2").closest("a")!;
    expect(held.textContent).toContain("WH-2 · on QA hold");
    expect(within(held).getByText("On Hold")).toBeTruthy();
    expect(screen.getByText(/^Updated /).textContent).toBe("Updated just now");

    // data_chat is enabled, so the Ask card links to /ask.
    expect(screen.getByRole("link", { name: /Ask$/ }).getAttribute("href")).toBe("/ask");
    expect(screen.getByRole("link", { name: /Operations/ }).getAttribute("href")).toBe("/explore/operations");
  });

  it("says nothing needs attention for an empty brief and hides Ask without data_chat", async () => {
    mockApi({
      "/orgs/me": { ...ME, enabled_features: [] },
      "/items": [],
      "/boms": [],
      "/suppliers": [],
      "/purchase-orders": [],
      "/inventory/morning-brief": { lot_alerts: [] },
    });
    renderPage(<Dashboard />);

    expect(await screen.findByText("Nothing needs attention today.")).toBeTruthy();
    await screen.findByText("Test Org");
    expect(screen.queryByText("Ask a question")).toBeNull();
  });

  it("reports a failed brief and refetches it on refresh", async () => {
    const api = mockApi({
      "/orgs/me": ME,
      "/items": [],
      "/boms": [],
      "/suppliers": [],
      "/purchase-orders": [],
      "/inventory/morning-brief": new Response("{}", { status: 500 }),
    });
    renderPage(<Dashboard />);

    expect(await screen.findByText("Couldn't load the Morning Brief.")).toBeTruthy();
    const briefCalls = () => api.calls.filter((c) => c.path === "/inventory/morning-brief").length;
    expect(briefCalls()).toBe(1);

    await userEvent.click(screen.getByRole("button", { name: "Refresh Morning Brief" }));
    await waitFor(() => expect(briefCalls()).toBe(2));
  });
});
