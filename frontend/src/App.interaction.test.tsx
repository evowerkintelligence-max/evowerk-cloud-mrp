// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { render, screen, within } from "@testing-library/react";
import { mockApi } from "@/test/renderPage";
import { useAuthStore } from "@/store/authStore";
import { useStaffAuthStore } from "@/store/staffAuthStore";
import { App } from "./App";

// A chunk that throws on render, to exercise the route error boundary.
vi.mock("./pages/CheckIn", () => ({
  CheckIn: () => {
    throw new Error("chunk exploded");
  },
}));

const me = (enabled_features: string[]) => ({ id: "o1", name: "Test Org", role: "owner", user_id: "u1", enabled_features });

beforeEach(() => {
  // Typewriter (PageHeader) reads matchMedia, which jsdom lacks.
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q }));
});
afterEach(() => useStaffAuthStore.setState({ accessToken: null }));

/** App as main.tsx mounts it, minus BrowserRouter. Signed out unless asked. */
function renderApp(route: string, { signedIn = false } = {}) {
  if (signedIn) useAuthStore.setState({ accessToken: "test-token", refreshToken: "test-refresh" });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const LOGIN_SUBTITLE = "Sign in to your Evowerk workspace";

describe("App routing and guards", () => {
  it("sends a signed-out visitor from the dashboard to log in", async () => {
    const api = mockApi({ "/auth/providers": { google: false, microsoft: false } });
    renderApp("/");
    expect(await screen.findByText(LOGIN_SUBTITLE)).toBeTruthy();
    expect(api.calls.some((c) => c.path === "/orgs/me")).toBe(false);
  });

  it("guards the MFA page behind sign-in too", async () => {
    mockApi({ "/auth/providers": { google: false, microsoft: false } });
    renderApp("/mfa");
    expect(await screen.findByText(LOGIN_SUBTITLE)).toBeTruthy();
  });

  it("serves public pages without a session", () => {
    mockApi({});
    renderApp("/privacy");
    expect(screen.getByRole("heading", { name: "What we collect" })).toBeTruthy();
  });

  it("loads the dashboard inside the app shell for a signed-in user", async () => {
    mockApi({ "/orgs/me": me([]), "/inventory/morning-brief": { lot_alerts: [] } });
    renderApp("/", { signedIn: true });
    expect(await screen.findByText("Morning Brief")).toBeTruthy();
    expect(screen.getByRole("complementary")).toBeTruthy();
    expect(await screen.findByText("Nothing needs attention today.")).toBeTruthy();
  });

  it("blocks a page the plan doesn't include", async () => {
    mockApi({ "/orgs/me": me(["sales"]) });
    renderApp("/boms", { signedIn: true });
    expect(await screen.findByText("Not included in your plan")).toBeTruthy();
  });

  it("redirects a legacy analytics URL to OEE, still behind its feature gate", async () => {
    mockApi({ "/orgs/me": me([]) });
    renderApp("/analytics", { signedIn: true });
    expect(await screen.findByText("Not included in your plan")).toBeTruthy();
    expect(within(screen.getByRole("banner")).getByText("OEE")).toBeTruthy();
  });

  it("renders an ungated page for a signed-in user", async () => {
    mockApi({ "/orgs/me": me([]) });
    renderApp("/account", { signedIn: true });
    expect(await screen.findByRole("button", { name: "Delete my account" })).toBeTruthy();
  });

  it("sends a visitor without a staff session from the staff portal to log in", async () => {
    mockApi({ "/auth/providers": { google: false, microsoft: false } });
    renderApp("/staff", { signedIn: true });
    expect(await screen.findByText(LOGIN_SUBTITLE)).toBeTruthy();
  });

  it("opens the staff portal with a staff session", async () => {
    useStaffAuthStore.setState({ accessToken: "staff-token" });
    mockApi({ "/admin/customers": [] });
    renderApp("/staff");
    expect(await screen.findByRole("heading", { name: "Customers" })).toBeTruthy();
  });

  it("contains a page that throws to that route instead of blanking the app", async () => {
    mockApi({});
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    renderApp("/checkin/m1");
    expect(await screen.findByText("This page failed to load. Try refreshing.")).toBeTruthy();
    consoleError.mockRestore();
  });
});
