// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Downtime } from "./Downtime";

// echarts paints to <canvas>, which jsdom lacks.
vi.mock("echarts", () => ({ init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }) }));

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});
afterEach(() => localStorage.clear());

const ORG = (features: string[]) => ({ id: "o1", name: "Acme", role: "owner", user_id: "u1", enabled_features: features });

const DS = {
  id: "live1",
  name: "Downtime log",
  original_filename: "d.csv",
  fmt: "csv",
  row_count: 42,
  size_bytes: 1024,
  created_at: "2026-01-01T00:00:00Z",
  columns: ["duration_min", "reason", "machine", "started_at"].map((name) => ({ name, dtype: "VARCHAR" })),
};

const RESULT = {
  total_incidents: 12,
  total_minutes: 150,
  mttr_minutes: 12.5,
  top_category: "Breakdown",
  by_category: [
    { category: "Breakdown", incidents: 7, total_minutes: 100, pct_of_total: 66.7, cumulative_pct: 66.7 },
    { category: "Changeover", incidents: 5, total_minutes: 50, pct_of_total: 33.3, cumulative_pct: 100 },
  ],
  by_machine: [{ machine: "Press 4", incidents: 12, total_minutes: 150, mttr_minutes: 12.5 }],
  trend: [
    { date: "2026-01-01", incidents: 5, total_minutes: 60 },
    { date: "2026-01-02", incidents: 7, total_minutes: 90 },
  ],
};

function control(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLElement;
}
const stat = (label: string) => screen.getByText(label, { selector: "span" }).parentElement!.parentElement!.textContent;

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

function trackRoutes(open: unknown[], extra: Record<string, unknown> = {}) {
  return {
    "/orgs/me": ORG(["downtime_tracking"]),
    "/downtime-tracking/incidents": () => open,
    "/downtime-tracking/machines": ["Press 4", "Lathe 2"],
    "/downtime-tracking/shifts": ["A", "B"],
    "/downtime-tracking/live-dataset": { dataset_id: null },
    "/datasets": [DS],
    "/datasets/live1": { preview: [] },
    ...extra,
  };
}

describe("Downtime analyze (org without live tracking)", () => {
  it("goes straight to analysis with no tabs, and posts the default duration unit", async () => {
    const api = mockApi({
      "/orgs/me": ORG([]),
      "/datasets": [DS],
      "/datasets/live1": { preview: [] },
      "POST /analytics/downtime": RESULT,
    });
    renderPage(<Downtime />);
    await screen.findByRole("option", { name: "Downtime log (42 rows)" });
    expect(screen.queryByRole("button", { name: "Track" })).toBeNull();
    expect(screen.queryByText("Report downtime")).toBeNull();

    await userEvent.selectOptions(control("Dataset"), "live1");
    await userEvent.click(screen.getByRole("button", { name: "Analyze downtime" }));
    await screen.findByText("MTTR");

    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      dataset_id: "live1",
      duration_col: "duration_min",
      duration_unit: "minutes",
      category_col: "reason",
      machine_col: "machine",
      timestamp_col: "started_at",
    });
    expect(stat("Incidents")).toBe("Incidents12");
    expect(stat("Total downtime")).toBe("Total downtime2.5 h");
    expect(stat("MTTR")).toContain("12.5 min");
    expect(stat("Top category")).toBe("Top categoryBreakdown");
    expect(screen.getByRole("heading", { name: "Pareto by category" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Trend" })).toBeTruthy();
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual(["Press 4", "12", "150", "12.5"]);
  });

  it("shows sub-two-hour totals in minutes and hides empty sections", async () => {
    const api = mockApi({
      "/orgs/me": ORG([]),
      "/datasets": [DS],
      "/datasets/live1": { preview: [] },
      "POST /analytics/downtime": { ...RESULT, total_minutes: 95.4, top_category: null, by_category: [], by_machine: [], trend: [] },
    });
    renderPage(<Downtime />);
    await screen.findByRole("option", { name: "Downtime log (42 rows)" });
    await userEvent.selectOptions(control("Dataset"), "live1");
    await userEvent.selectOptions(control("Duration unit"), "hours");
    await userEvent.click(screen.getByRole("button", { name: "Analyze downtime" }));
    await screen.findByText("MTTR");

    expect(api.calls.find((c) => c.method === "POST")!.body).toMatchObject({ duration_unit: "hours" });
    expect(stat("Total downtime")).toBe("Total downtime95 min");
    expect(stat("Top category")).toBe("Top category—");
    expect(screen.queryByRole("heading", { name: "Pareto by category" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Trend" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "By machine" })).toBeNull();
  });
});

describe("Downtime track (org with live tracking)", () => {
  it("shows the empty board and keeps Mark down disabled until machine and shift are filled", async () => {
    mockApi(trackRoutes([]));
    renderPage(<Downtime />);
    expect(await screen.findByText("Nothing's down right now.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Track" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "View live analytics" })).toBeNull();

    const markDown = screen.getByRole("button", { name: "Mark down" }) as HTMLButtonElement;
    expect(markDown.disabled).toBe(true);
    await userEvent.type(control("Machine"), "Press 4");
    expect(markDown.disabled).toBe(true);
    await userEvent.type(control("Shift"), "B");
    expect(markDown.disabled).toBe(false);
    // Known machines are offered as suggestions.
    const suggested = () => Array.from(document.querySelectorAll("#downtime-machine-options option")).map((o) => o.getAttribute("value"));
    await waitFor(() => expect(suggested()).toEqual(["Press 4", "Lathe 2"]));
  });

  it("marks a machine down, posting machine/reason/shift and remembering the shift", async () => {
    const open: unknown[] = [];
    const api = mockApi(
      trackRoutes(open, {
        "POST /downtime-tracking/incidents": ({ body }: { body: { machine: string; reason: string; shift: string } }) => {
          open.push({ id: "i1", ...body, started_at: minutesAgo(1), ended_at: null });
          return open[0];
        },
      }),
    );
    renderPage(<Downtime />);
    await screen.findByText("Nothing's down right now.");
    await userEvent.type(control("Machine"), "Press 4");
    await userEvent.selectOptions(control("Reason"), "quality_hold");
    await userEvent.type(control("Shift"), "B");
    await userEvent.click(screen.getByRole("button", { name: "Mark down" }));

    expect(await screen.findByText("Press 4")).toBeTruthy();
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({ machine: "Press 4", reason: "quality_hold", shift: "B" });
    expect(screen.getByText(/Quality Hold · Shift B · 00:0[01]:\d\d/)).toBeTruthy();
    expect(localStorage.getItem("downtime-tracking-last-shift")).toBe("B");
    // Machine clears for the next report; shift stays.
    expect((control("Machine") as HTMLInputElement).value).toBe("");
    expect((control("Shift") as HTMLInputElement).value).toBe("B");
  });

  it("prefills the last-used shift and shows the server error when marking down fails", async () => {
    localStorage.setItem("downtime-tracking-last-shift", "A");
    mockApi(
      trackRoutes([], {
        "POST /downtime-tracking/incidents": new Response(JSON.stringify({ detail: "Press 4 is already down" }), { status: 409 }),
      }),
    );
    renderPage(<Downtime />);
    await screen.findByText("Nothing's down right now.");
    expect((control("Shift") as HTMLInputElement).value).toBe("A");
    await userEvent.type(control("Machine"), "Press 4");
    await userEvent.click(screen.getByRole("button", { name: "Mark down" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBe("Press 4 is already down");
  });

  it("marks an incident up and discards another, hitting each incident's endpoint", async () => {
    let open = [
      { id: "i1", machine: "Press 4", reason: "breakdown", shift: "A", started_at: minutesAgo(65), ended_at: null },
      { id: "i2", machine: "Lathe 2", reason: "mystery", shift: "A", started_at: minutesAgo(5), ended_at: null },
    ];
    const api = mockApi(
      trackRoutes([], {
        "/downtime-tracking/incidents": () => open,
        "POST /downtime-tracking/incidents/i1/end": () => {
          open = open.filter((i) => i.id !== "i1");
          return { id: "i1" };
        },
        "DELETE /downtime-tracking/incidents/i2": () => {
          open = open.filter((i) => i.id !== "i2");
          return new Response(null, { status: 204 });
        },
      }),
    );
    renderPage(<Downtime />);
    const press = await screen.findByText("Press 4");
    expect(screen.getByText(/Breakdown · Shift A · 01:0[45]:\d\d/)).toBeTruthy();
    // Unknown reasons fall back to the raw value.
    expect(screen.getByText(/mystery · Shift A/)).toBeTruthy();
    expect(screen.getByText("Currently down").nextElementSibling!.textContent).toBe("2");

    const pressCard = press.closest("div.flex") as HTMLElement;
    await userEvent.click(within(pressCard).getByRole("button", { name: "Mark up" }));
    await waitFor(() => expect(screen.queryByText("Press 4")).toBeNull());

    const lathe = screen.getByText("Lathe 2").closest("div.flex") as HTMLElement;
    await userEvent.click(within(lathe).getByRole("button", { name: "Discard" }));
    expect(await screen.findByText("Nothing's down right now.")).toBeTruthy();

    expect(api.calls.some((c) => c.method === "POST" && c.path === "/downtime-tracking/incidents/i1/end")).toBe(true);
    expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/downtime-tracking/incidents/i2")).toBe(true);
  });

  it("shows the server error when marking up fails", async () => {
    const open = [{ id: "i1", machine: "Press 4", reason: "breakdown", shift: "A", started_at: minutesAgo(2), ended_at: null }];
    mockApi(
      trackRoutes(open, {
        "POST /downtime-tracking/incidents/i1/end": new Response(JSON.stringify({ detail: "Already ended" }), { status: 409 }),
        "DELETE /downtime-tracking/incidents/i1": new Response(JSON.stringify({ detail: "Not yours" }), { status: 403 }),
      }),
    );
    renderPage(<Downtime />);
    await screen.findByText("Press 4");
    await userEvent.click(screen.getByRole("button", { name: "Mark up" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Already ended");
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Not yours"));
  });

  it("jumps from the live board to analysis with the live dataset preselected", async () => {
    mockApi(trackRoutes([], { "/downtime-tracking/live-dataset": { dataset_id: "live1" } }));
    renderPage(<Downtime />);
    await userEvent.click(await screen.findByRole("button", { name: "View live analytics" }));

    await waitFor(() => expect((control("Dataset") as HTMLSelectElement).value).toBe("live1"));
    expect((control("Duration") as HTMLSelectElement).value).toBe("duration_min");
    expect(screen.queryByText("Report downtime")).toBeNull();

    // And the Track tab brings the board back.
    await userEvent.click(screen.getByRole("button", { name: "Track" }));
    expect(await screen.findByText("Report downtime")).toBeTruthy();
  });
});
