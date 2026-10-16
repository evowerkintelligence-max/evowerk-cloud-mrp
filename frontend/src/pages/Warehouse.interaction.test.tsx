// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Warehouse } from "./Warehouse";

function org(role: string) {
  return { role, adjust_approval_threshold: "10", demand_policy: "forecast" };
}

// PageHeader's Typewriter reads prefers-reduced-motion; jsdom has no matchMedia.
beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: true, media: query, addEventListener() {}, removeEventListener() {} }));
});

describe("Warehouse", () => {
  it("starts on inventory and switches to the locations tab", async () => {
    mockApi({ "/orgs/me": org("operator"), "/inventory": [], "/locations": [] });
    renderPage(<Warehouse />);
    expect(await screen.findByText("No stock yet")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Locations" }));
    expect(await screen.findByText("No locations yet")).toBeTruthy();
    expect(screen.queryByText("No stock yet")).toBeNull();
  });

  it("hides the approvals tab from non-managers", async () => {
    const api = mockApi({ "/orgs/me": org("operator"), "/inventory": [], "/locations": [] });
    renderPage(<Warehouse />);
    await screen.findByText("No stock yet");
    expect(api.calls.some((c) => c.path === "/orgs/me")).toBe(true);
    expect(screen.queryByRole("button", { name: "Approvals" })).toBeNull();
  });

  it("shows the approvals tab to owners and renders the approvals panel", async () => {
    mockApi({
      "/orgs/me": org("owner"),
      "/inventory": [],
      "/locations": [],
      "/inventory/adjustment-requests": [],
    });
    renderPage(<Warehouse />);
    await userEvent.click(await screen.findByRole("button", { name: "Approvals" }));
    expect(await screen.findByText("No pending approvals")).toBeTruthy();
  });
});
