// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { KpiDashboards } from "./KpiDashboards";

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

const DASHBOARDS = [
  { id: "db1", name: "Floor", tier: "operator", kpi_ids: ["k1", "k2"], refresh_seconds: 15 },
  { id: "db2", name: "Weekly", tier: "supervisor", kpi_ids: ["k3"], refresh_seconds: 300 },
];

const KPIS = [{ id: "k1", name: "Avg cycle time" }, { id: "k2", name: "Scrap rate" }, { id: "k3", name: "OEE" }];

const CARDS_DB1 = [
  {
    kpi_id: "k1", dataset_id: "d1", name: "Avg cycle time", unit: "s", direction: "lower_is_better", value: 42.5,
    error: null, computed_at: "", eval: { status: "breach", trend: "up" },
    breakdown: [{ label: "M1", value: 50 }, { label: "M2", value: 35.25 }],
  },
  {
    kpi_id: "k2", dataset_id: "d1", name: "Scrap rate", unit: null, direction: "lower_is_better", value: null,
    error: "No rows", computed_at: "", eval: null, breakdown: null,
  },
];

const CARDS_DB2 = [
  {
    kpi_id: "k3", dataset_id: "d2", name: "OEE", unit: "%", direction: "higher_is_better", value: 71,
    error: null, computed_at: "", eval: { status: "ok", trend: "flat" }, breakdown: null,
  },
];

function Loc() {
  return <div data-testid="loc">{useLocation().pathname}</div>;
}

function render() {
  return renderPage(
    <>
      <KpiDashboards />
      <Loc />
    </>,
    { route: "/kpis/dashboards" },
  );
}

function card(name: string) {
  return screen.getByText(name).closest(".grid > *") as HTMLElement;
}

describe("KpiDashboards", () => {
  afterEach(() => {
    Reflect.deleteProperty(document, "fullscreenElement");
  });

  it("shows the first dashboard's cards with value, status, trend, breakdown and history", async () => {
    mockApi({
      "/kpi-dashboards": DASHBOARDS,
      "/kpis": KPIS,
      "/kpi-dashboards/db1/cards": CARDS_DB1,
      "/kpis/k1/history": [{ value: 40 }, { value: null }, { value: 44 }],
      "/kpis/k2/history": [],
    });
    const { container } = render();
    await screen.findByText("Avg cycle time");

    const cycle = card("Avg cycle time");
    expect(within(cycle).getByText("42.5")).toBeTruthy();
    expect(within(cycle).getByText("s")).toBeTruthy();
    expect(within(cycle).getByText("breach")).toBeTruthy();
    expect(within(cycle).getByText("↑")).toBeTruthy();
    expect(within(cycle).getByText("M1")).toBeTruthy();
    expect(within(cycle).getByText("35.3")).toBeTruthy();
    // Two non-null history points are enough for a sparkline.
    await waitFor(() => expect(cycle.querySelector("polyline")).not.toBeNull());
    expect(within(card("Scrap rate")).getByText("No rows")).toBeTruthy();
    expect(screen.getByText("Auto-refresh (15s)")).toBeTruthy();
    expect(container.querySelectorAll(".grid > *")).toHaveLength(2);
  });

  it("switches dashboards from the picker", async () => {
    const api = mockApi({
      "/kpi-dashboards": DASHBOARDS,
      "/kpis": KPIS,
      "/kpi-dashboards/db1/cards": CARDS_DB1,
      "/kpi-dashboards/db2/cards": CARDS_DB2,
      "/kpis/k1/history": [],
      "/kpis/k2/history": [],
      "/kpis/k3/history": [],
    });
    render();
    await screen.findByText("Avg cycle time");

    await userEvent.selectOptions(screen.getByRole("option", { name: "Weekly (supervisor)" }).closest("select")!, "db2");
    expect(await screen.findByText("OEE")).toBeTruthy();
    expect(within(card("OEE")).getByText("71")).toBeTruthy();
    expect(within(card("OEE")).getByText("→")).toBeTruthy();
    expect(screen.queryByText("Avg cycle time")).toBeNull();
    expect(screen.getByText("Auto-refresh (300s)")).toBeTruthy();
    expect(api.calls.some((c) => c.path === "/kpis/k3/history" && c.search === "?dataset_id=d2&limit=20")).toBe(true);
  });

  it("refetches cards on the dashboard's refresh interval until auto-refresh is turned off", async () => {
    const api = mockApi({
      "/kpi-dashboards": [{ ...DASHBOARDS[0], refresh_seconds: 0.2 }],
      "/kpis": KPIS,
      "/kpi-dashboards/db1/cards": CARDS_DB1,
      "/kpis/k1/history": [],
      "/kpis/k2/history": [],
    });
    render();
    await screen.findByText("Avg cycle time");
    const cardCalls = () => api.calls.filter((c) => c.path === "/kpi-dashboards/db1/cards").length;
    await waitFor(() => expect(cardCalls()).toBeGreaterThanOrEqual(2));

    await userEvent.click(screen.getByText(/Auto-refresh/).querySelector("input")!);
    const after = cardCalls();
    await new Promise((r) => setTimeout(r, 600));
    expect(cardCalls()).toBe(after);
  });

  it("enters and leaves TV mode via the Fullscreen API", async () => {
    const request = vi.fn();
    const exit = vi.fn();
    HTMLElement.prototype.requestFullscreen = request;
    document.exitFullscreen = exit;
    mockApi({
      "/kpi-dashboards": DASHBOARDS,
      "/kpis": KPIS,
      "/kpi-dashboards/db1/cards": CARDS_DB1,
      "/kpis/k1/history": [],
      "/kpis/k2/history": [],
    });
    render();
    await screen.findByText("Avg cycle time");

    await userEvent.click(screen.getByRole("button", { name: "TV mode" }));
    expect(request).toHaveBeenCalledTimes(1);
    const grid = request.mock.contexts[0] as HTMLElement;
    expect(within(grid).getByText("Avg cycle time")).toBeTruthy();

    Object.defineProperty(document, "fullscreenElement", { value: grid, configurable: true });
    act(() => {
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    await userEvent.click(screen.getByRole("button", { name: "Exit TV mode" }));
    expect(exit).toHaveBeenCalledTimes(1);
  });

  it("shows the empty state and navigates to definitions", async () => {
    mockApi({ "/kpi-dashboards": [], "/kpis": KPIS });
    render();
    expect(await screen.findByText("No dashboards yet")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Definitions" }));
    expect(screen.getByTestId("loc").textContent).toBe("/kpis");
  });

  it("creates a dashboard from picked KPIs and selects it", async () => {
    let list: typeof DASHBOARDS = [];
    const created = { id: "db3", name: "Line 2", tier: "operator", kpi_ids: ["k3", "k1"], refresh_seconds: 15 };
    const api = mockApi({
      "/kpi-dashboards": () => list,
      "/kpis": KPIS,
      "POST /kpi-dashboards": () => {
        list = [...DASHBOARDS, created];
        return created;
      },
      "/kpi-dashboards/db3/cards": CARDS_DB2,
      "/kpis/k3/history": [],
    });
    render();
    await screen.findByText("No dashboards yet");
    await userEvent.click(screen.getByRole("button", { name: "New dashboard" }));

    const createBtn = screen.getByRole("button", { name: "Create" }) as HTMLButtonElement;
    expect(createBtn.disabled).toBe(true);
    await userEvent.type(screen.getByText("Name", { selector: "label" }).parentElement!.querySelector("input")!, "Line 2");
    await userEvent.selectOptions(screen.getByText("Tier", { selector: "label" }).parentElement!.querySelector("select")!, "operator");
    const kpiBox = (name: string) => screen.getByText(name, { selector: "label" }).querySelector("input")!;
    await userEvent.click(kpiBox("OEE"));
    await userEvent.click(kpiBox("Scrap rate"));
    await userEvent.click(kpiBox("Scrap rate"));
    await userEvent.click(kpiBox("Avg cycle time"));
    await userEvent.click(createBtn);

    expect(await screen.findByText("OEE")).toBeTruthy();
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({ name: "Line 2", tier: "operator", kpi_ids: ["k3", "k1"] });
    expect((screen.getByRole("option", { name: "Line 2 (operator)" }) as HTMLOptionElement).selected).toBe(true);
    expect(screen.queryByRole("button", { name: "Create" })).toBeNull();
  });

  it("closes the create dialog on cancel without posting", async () => {
    const api = mockApi({ "/kpi-dashboards": [], "/kpis": KPIS });
    render();
    await screen.findByText("No dashboards yet");
    await userEvent.click(screen.getByRole("button", { name: "New dashboard" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Tier")).toBeNull();
    expect(api.calls.some((c) => c.method === "POST")).toBe(false);
  });
});
