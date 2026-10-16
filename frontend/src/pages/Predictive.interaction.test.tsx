// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { Predictive } from "./Predictive";

// echarts needs canvas, which jsdom lacks.
vi.mock("@/pages/analytics/shared/EChart", () => ({ EChart: () => null }));

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

const FLEET = {
  machines: [
    { machine: "Press 4", status: "active", risk_level: "high", failure_probability_30d: 0.4567, active_anomalies: 2, last_reading_at: "2026-10-15T08:00:00Z" },
    { machine: "Lathe 1", status: "learning", risk_level: "unknown", failure_probability_30d: null, active_anomalies: 0, last_reading_at: null },
    { machine: "Mill 2", status: "unmapped", risk_level: "unknown", failure_probability_30d: null, active_anomalies: 0, last_reading_at: null },
  ],
};

const SCHEDULES = [
  { id: "s1", machine: "Press 4", priority: "high", action: "Replace bearing", status: "open" },
  { id: "s2", machine: "Lathe 1", priority: "low", action: "Lubricate", status: "done" },
];

const DETAIL = {
  machine: "Press 4",
  risk_level: "high",
  failure_probability_30d: 0.4567,
  fit_tier: "fleet_pooled",
  sensors: {
    vibration: {
      centerline: 1, ucl: 2, lcl: 0, n: 2,
      readings: [{ value: 1.2, ts: "2026-10-15T08:00:00Z" }, { value: 2.5, ts: "2026-10-15T09:00:00Z" }],
      anomalies: [
        { id: "a1", violations: [1, 99], detected_at: "2026-10-15T09:00:00Z", acknowledged_at: null },
        { id: "a2", violations: [2], detected_at: "2026-10-14T09:00:00Z", acknowledged_at: "2026-10-14T10:00:00Z" },
      ],
    },
    temperature: { centerline: null, ucl: null, lcl: null, n: 3, readings: [], anomalies: [] },
  },
};

function renderPredictive() {
  return renderPage(
    <Routes>
      <Route path="/predictive" element={<Predictive />} />
      <Route path="/realtime" element={<p>realtime page</p>} />
    </Routes>,
    { route: "/predictive" },
  );
}

describe("Predictive fleet", () => {
  it("shows the empty state and no schedule when nothing is connected", async () => {
    mockApi({ "/cbm/fleet": { machines: [] }, "/predictive/schedules": [] });
    renderPredictive();
    expect(await screen.findByText("No machines connected")).toBeTruthy();
    expect(screen.queryByText("Maintenance schedule")).toBeNull();
  });

  it("lists fleet health per machine", async () => {
    mockApi({ "/cbm/fleet": FLEET, "/predictive/schedules": [] });
    renderPredictive();
    const press = (await screen.findByText("Press 4")).closest("tr")!;
    expect(within(press).getByText("45.7%")).toBeTruthy();
    expect(within(press).getByText("Active")).toBeTruthy();
    const lathe = screen.getByText("Lathe 1").closest("tr")!;
    expect(within(lathe).getByText("Learning baseline")).toBeTruthy();
    expect(within(lathe).getByText("No readings yet")).toBeTruthy();
    const mill = screen.getByText("Mill 2").closest("tr")!;
    expect(within(mill).getByText("Needs sensor mapping")).toBeTruthy();
    await userEvent.click(within(mill).getByRole("button", { name: "Configure" }));
    expect(await screen.findByText("realtime page")).toBeTruthy();
  });

  it("marks an open maintenance item done", async () => {
    const api = mockApi({ "/cbm/fleet": FLEET, "/predictive/schedules": SCHEDULES, "PATCH /predictive/schedules/s1": { ...SCHEDULES[0], status: "done" } });
    renderPredictive();
    const open = (await screen.findByText("Replace bearing")).closest("tr")!;
    const done = screen.getByText("Lubricate").closest("tr")!;
    expect(within(done).queryByRole("button", { name: "Mark done" })).toBeNull();
    await userEvent.click(within(open).getByRole("button", { name: "Mark done" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(api.calls.find((c) => c.method === "PATCH")).toMatchObject({ path: "/predictive/schedules/s1", body: { status: "done" } });
  });
});

describe("Predictive machine detail", () => {
  it("drills into a machine, acknowledges an anomaly and returns to the fleet", async () => {
    const api = mockApi({
      "/cbm/fleet": FLEET,
      "/predictive/schedules": [],
      "/cbm/machines/Press%204": DETAIL,
      "POST /cbm/anomalies/a1/acknowledge": { ok: true },
    });
    renderPredictive();
    await userEvent.click(await screen.findByText("Press 4"));

    expect(await screen.findByText("45.7% chance of failure in the next 30 days")).toBeTruthy();
    expect(screen.getByText(/Calibrated from fleet-wide breakdown history/)).toBeTruthy();
    expect(screen.getByText("Learning baseline — needs at least 20 readings.")).toBeTruthy();
    expect(screen.getByText("Point beyond 3σ (outside control limits); Rule 99")).toBeTruthy();
    expect(screen.getByText("Acknowledged")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path === "/cbm/anomalies/a1/acknowledge")).toBe(true));

    await userEvent.click(screen.getByRole("button", { name: /Back to fleet/ }));
    expect(await screen.findByText("Fleet health")).toBeTruthy();
  });

  it("explains when there isn't enough history to estimate RUL", async () => {
    mockApi({
      "/cbm/fleet": FLEET,
      "/predictive/schedules": [],
      "/cbm/machines/Lathe%201": { ...DETAIL, machine: "Lathe 1", failure_probability_30d: null, fit_tier: "insufficient_data", sensors: {} },
    });
    renderPredictive();
    await userEvent.click(await screen.findByText("Lathe 1"));
    expect(await screen.findByText("Not enough breakdown history to estimate RUL")).toBeTruthy();
    expect(screen.getByText("Not enough breakdown history to estimate")).toBeTruthy();
  });
});
