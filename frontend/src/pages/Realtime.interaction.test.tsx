// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Realtime } from "./Realtime";

const INTEGRATIONS = [
  { id: "i1", name: "MES Postgres", type: "postgres" },
  { id: "i2", name: "Shared drive", type: "sharepoint" },
];
const RUNNING = {
  ts: "2026-09-10T08:00:00Z",
  schedule_id: "s1",
  name: "Line 1 MES",
  status: "running",
  error: null,
  row_count: 412,
  metrics: { oee_pct: 71.5, scrap_rate: 2.1 },
};
const STOPPED = { ...RUNNING, schedule_id: "s2", name: "Line 2 MES", status: "stopped", ts: null, row_count: 0, metrics: {} };

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: true, media: query, addEventListener() {}, removeEventListener() {} }));
});

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/integrations": INTEGRATIONS,
    "/sync-schedules": { schedules: [RUNNING, STOPPED] },
    "POST /sync-schedules": { schedule_id: "s3" },
    "POST /sync-schedules/s1/stop": { ok: true },
    "POST /sync-schedules/s2/start": { ok: true },
    "DELETE /sync-schedules/s1": new Response(null, { status: 204 }),
    "/sync-schedules/s1/snapshot": { ...RUNNING, status: "error", error: "Connection reset", row_count: 9 },
    ...overrides,
  });
}

function control(scope: HTMLElement, label: string, tag = "input") {
  return within(scope).getByText(label, { selector: "label" }).parentElement!.querySelector(tag) as HTMLElement;
}

function dialog(title: string) {
  return screen.getByRole("heading", { name: title }).parentElement!.parentElement as HTMLElement;
}

function row(name: string) {
  return screen.getByText(name).closest("tr") as HTMLElement;
}

describe("Realtime", () => {
  it("lists schedules with status and the right start/stop control", async () => {
    api();
    renderPage(<Realtime />);
    expect(await screen.findByText("Line 1 MES")).toBeTruthy();
    expect(within(row("Line 1 MES")).getByText("running")).toBeTruthy();
    expect(within(row("Line 1 MES")).getByText("412")).toBeTruthy();
    expect(within(row("Line 2 MES")).getByText("—")).toBeTruthy();
    expect(within(row("Line 1 MES")).getByRole("button", { name: "Stop" })).toBeTruthy();
    expect(within(row("Line 2 MES")).getByRole("button", { name: "Start" })).toBeTruthy();
  });

  it("shows the empty state", async () => {
    api({ "/sync-schedules": { schedules: [] } });
    renderPage(<Realtime />);
    expect(await screen.findByText("No scheduled syncs yet")).toBeTruthy();
  });

  it("stops a running schedule, starts a stopped one, and deletes", async () => {
    const { calls } = api();
    renderPage(<Realtime />);
    await screen.findByText("Line 1 MES");
    await userEvent.click(within(row("Line 1 MES")).getByRole("button", { name: "Stop" }));
    await userEvent.click(within(row("Line 2 MES")).getByRole("button", { name: "Start" }));
    await userEvent.click(within(row("Line 1 MES")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    const writes = calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.path}`);
    expect(writes).toEqual([
      "POST /sync-schedules/s1/stop",
      "POST /sync-schedules/s2/start",
      "DELETE /sync-schedules/s1",
    ]);
  });

  it("opens live details and shows the refreshed snapshot", async () => {
    api();
    renderPage(<Realtime />);
    await screen.findByText("Line 1 MES");
    await userEvent.click(within(row("Line 1 MES")).getByRole("button", { name: "View details" }));
    const d = dialog("Details — Line 1 MES");
    expect(within(d).getByText("oee pct")).toBeTruthy();
    expect(within(d).getByText("71.5")).toBeTruthy();
    expect(await within(d).findByText("Connection reset")).toBeTruthy();
    expect(within(d).getByText("error")).toBeTruthy();
    await userEvent.click(within(d).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("heading", { name: "Details — Line 1 MES" })).toBeNull();
  });

  it("creates a schedule with only incremental integrations, filled column map and sensor map", async () => {
    const { calls } = api();
    renderPage(<Realtime />);
    await userEvent.click(screen.getByRole("button", { name: "New schedule" }));
    const d = dialog("New scheduled sync");
    await within(d).findByRole("option", { name: "MES Postgres" });
    expect(within(d).queryByRole("option", { name: "Shared drive" })).toBeNull();
    await userEvent.selectOptions(control(d, "Integration", "select"), "i1");
    await userEvent.type(control(d, "Name"), "Line 3");
    const interval = control(d, "Sync interval (seconds)");
    await userEvent.clear(interval);
    await userEvent.type(interval, "600");
    await userEvent.type(control(d, "Cursor column"), "updated_at");
    await userEvent.type(within(d).getByPlaceholderText("Units column"), "units");
    await userEvent.type(within(d).getByPlaceholderText("Scrap column"), "scrap");
    expect(within(d).queryByText("Sensor map")).toBeNull();
    await userEvent.type(control(d, "Machine (for CBM, optional)"), " M3 ");
    await userEvent.click(within(d).getByRole("button", { name: "Add sensor" }));
    await userEvent.click(within(d).getByRole("button", { name: "Add sensor" }));
    await userEvent.click(within(d).getByRole("button", { name: "Add sensor" }));
    const names = within(d).getAllByPlaceholderText("Sensor name (e.g. vib_x)");
    const fields = within(d).getAllByPlaceholderText("Source field name");
    await userEvent.type(names[0], "vib_x");
    await userEvent.type(fields[0], "vibration_x");
    await userEvent.type(names[1], "half-filled");
    await userEvent.click(within(d).getAllByRole("button", { name: "Remove" })[2]);
    expect(within(d).getAllByRole("button", { name: "Remove" })).toHaveLength(2);
    await userEvent.click(within(d).getByRole("button", { name: "Create" }));

    await waitFor(() => expect(screen.queryByRole("heading", { name: "New scheduled sync" })).toBeNull());
    expect(calls.find((c) => c.method === "POST" && c.path === "/sync-schedules")!.body).toEqual({
      integration_id: "i1",
      name: "Line 3",
      machine: "M3",
      interval_seconds: 600,
      cursor_column: "updated_at",
      column_map: { units_col: "units", scrap_col: "scrap" },
      sensor_map: { vib_x: "vibration_x" },
    });
  }, 15_000); // long typing sequence; slow under a loaded parallel run

  it("shows the create error inside the form and keeps it open; Cancel closes it", async () => {
    api({ "POST /sync-schedules": new Response(JSON.stringify({ detail: "Integration unreachable" }), { status: 400 }) });
    renderPage(<Realtime />);
    await userEvent.click(screen.getByRole("button", { name: "New schedule" }));
    const d = dialog("New scheduled sync");
    await within(d).findByRole("option", { name: "MES Postgres" });
    await userEvent.selectOptions(control(d, "Integration", "select"), "i1");
    await userEvent.type(control(d, "Cursor column"), "id");
    await userEvent.click(within(d).getByRole("button", { name: "Create" }));
    expect(await within(d).findByText("Integration unreachable")).toBeTruthy();
    await userEvent.click(within(d).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("heading", { name: "New scheduled sync" })).toBeNull();
  });
});
