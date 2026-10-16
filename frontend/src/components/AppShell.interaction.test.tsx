// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { useAuthStore } from "@/store/authStore";
import { AppShell } from "./AppShell";

const ME = { id: "o1", name: "Acme steel works", role: "owner", user_id: "u1", enabled_features: ["boms"] };
const RULES = [
  { id: "r1", name: "Spindle overheat", severity: "critical", enabled: true },
  { id: "r2", name: "Low stock", severity: "warning", enabled: true },
  { id: "r3", name: "Old rule", severity: "info", enabled: false },
];

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

function renderShell(route = "/boms") {
  return renderPage(
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<p>dashboard page</p>} />
        <Route path="/boms" element={<p>boms page</p>} />
        <Route path="/datasets" element={<p>datasets page</p>} />
        <Route path="/alerts" element={<p>alerts page</p>} />
        <Route path="/account" element={<p>account page</p>} />
        <Route path="/somewhere" element={<p>unlisted page</p>} />
      </Route>
      <Route path="/login" element={<p>login page</p>} />
    </Routes>,
    { route },
  );
}

const sidebar = () => screen.getByRole("complementary");

describe("AppShell", () => {
  it("renders the routed page with a sidebar gated by the org's features", async () => {
    mockApi({ "/orgs/me": ME });
    renderShell();
    expect(screen.getByText("boms page")).toBeTruthy();

    await within(sidebar()).findByText("Acme steel works");
    expect(within(sidebar()).getByText("AS")).toBeTruthy();
    expect(within(sidebar()).getByRole("link", { name: "Bills of Materials" })).toBeTruthy();
    expect(within(sidebar()).getByRole("link", { name: "Production" })).toBeTruthy();
    expect(within(sidebar()).queryByRole("link", { name: "Procurement" })).toBeNull();
    // Plant Analytics has only gated items, so its whole section disappears.
    expect(within(sidebar()).queryByRole("button", { name: "Plant Analytics" })).toBeNull();
  });

  it("breadcrumbs the current page under its section's hub", async () => {
    mockApi({ "/orgs/me": ME });
    renderShell("/boms");
    const header = screen.getByRole("banner");
    expect(within(header).getByRole("link", { name: "Operations" }).getAttribute("href")).toBe("/explore/operations");
    expect(within(header).getByText("Bills of Materials")).toBeTruthy();
  });

  it("falls back to the org name in the top bar for a page outside the nav", async () => {
    mockApi({ "/orgs/me": ME });
    renderShell("/somewhere");
    expect(await within(screen.getByRole("banner")).findByText("Acme steel works")).toBeTruthy();
  });

  it("collapses a sidebar section and remembers it", async () => {
    mockApi({ "/orgs/me": ME });
    renderShell();
    await userEvent.click(within(sidebar()).getByRole("button", { name: "Operations" }));
    expect(within(sidebar()).queryByRole("link", { name: "Production" })).toBeNull();
    expect(JSON.parse(localStorage.getItem("evowerk.nav.collapsed")!)).toEqual({ Operations: true });

    await userEvent.click(within(sidebar()).getByRole("button", { name: "Operations" }));
    expect(within(sidebar()).getByRole("link", { name: "Production" })).toBeTruthy();
  });

  it("starts with a section collapsed when localStorage says so", () => {
    mockApi({ "/orgs/me": ME });
    localStorage.setItem("evowerk.nav.collapsed", JSON.stringify({ System: true }));
    renderShell();
    expect(within(sidebar()).queryByRole("link", { name: "Alerts" })).toBeNull();
    expect(within(sidebar()).getByRole("link", { name: "Datasets" })).toBeTruthy();
  });

  it("ignores unreadable collapse state and a hidden-sidebar preference is honoured", () => {
    mockApi({ "/orgs/me": ME });
    localStorage.setItem("evowerk.nav.collapsed", "{not json");
    localStorage.setItem("evowerk.nav.sidebarOpen", "false");
    renderShell();
    expect(within(sidebar()).getByRole("link", { name: "Alerts" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Show sidebar" })).toBeTruthy();
  });

  it("hides and shows the sidebar, persisting the choice", async () => {
    mockApi({ "/orgs/me": ME });
    renderShell();
    await userEvent.click(screen.getByRole("button", { name: "Hide sidebar" }));
    expect(localStorage.getItem("evowerk.nav.sidebarOpen")).toBe("false");
    expect(sidebar().className).toContain("md:hidden");
    await userEvent.click(screen.getByRole("button", { name: "Show sidebar" }));
    expect(localStorage.getItem("evowerk.nav.sidebarOpen")).toBe("true");
  });

  it("logs out by revoking the refresh token, clearing the session and going to log in", async () => {
    const api = mockApi({ "/orgs/me": ME, "POST /auth/logout": {} });
    renderShell();
    await userEvent.click(screen.getByRole("button", { name: "Log out" }));

    await screen.findByText("login page");
    expect(api.calls.find((c) => c.path === "/auth/logout")!.body).toEqual({ refresh_token: "test-refresh" });
    expect(useAuthStore.getState()).toMatchObject({ accessToken: null, refreshToken: null });
  });

  it("still signs out locally when the logout request fails", async () => {
    const { fetchMock } = mockApi({ "/orgs/me": ME });
    renderShell();
    fetchMock.mockImplementation(async (input) => {
      if (String(input) === "/auth/logout") throw new TypeError("offline");
      return new Response(JSON.stringify(ME), { status: 200 });
    });
    await userEvent.click(screen.getByRole("button", { name: "Log out" }));
    await screen.findByText("login page");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("goes to account settings", async () => {
    mockApi({ "/orgs/me": ME });
    renderShell();
    await userEvent.click(screen.getByRole("button", { name: "Account settings" }));
    expect(await screen.findByText("account page")).toBeTruthy();
  });

  it("notifications loads enabled alert rules on open and links to all alerts", async () => {
    const api = mockApi({ "/orgs/me": ME, "/alerts/rules": RULES });
    renderShell();
    expect(api.calls.some((c) => c.path === "/alerts/rules")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(await screen.findByText("2 enabled, 1 critical")).toBeTruthy();
    expect(screen.getByText("Spindle overheat")).toBeTruthy();
    expect(screen.queryByText("Old rule")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "View all alerts" }));
    expect(await screen.findByText("alerts page")).toBeTruthy();
    expect(screen.queryByText("Alert rules")).toBeNull();
  });

  it("notifications closes on Escape and on an outside click", async () => {
    mockApi({ "/orgs/me": ME, "/alerts/rules": [] });
    renderShell();
    await userEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(await screen.findByText("No enabled alert rules.")).toBeTruthy();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByText("Alert rules")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(screen.getByText("Alert rules")).toBeTruthy();
    fireEvent.mouseDown(screen.getByText("boms page"));
    expect(screen.queryByText("Alert rules")).toBeNull();
  });

  it("opens the command palette with Ctrl+K and from the search button, listing only visible pages", async () => {
    mockApi({ "/orgs/me": ME });
    renderShell();
    await within(sidebar()).findByText("Acme steel works");
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByPlaceholderText("Go to page…");
    await userEvent.type(input, "procurement");
    // Procurement is gated off, but the BOM item doesn't match that query either.
    expect(screen.getByText(/No pages match/)).toBeTruthy();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByPlaceholderText("Go to page…")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /Search…/ }));
    expect(screen.getByPlaceholderText("Go to page…")).toBeTruthy();
  });
});
