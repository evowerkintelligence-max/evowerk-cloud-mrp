// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { Kpis } from "./Kpis";

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

const DATASETS = [
  {
    id: "d1", name: "Line 1 output", original_filename: "l.csv", fmt: "csv", row_count: 10, size_bytes: 1, created_at: "",
    columns: [{ name: "machine", dtype: "VARCHAR" }, { name: "cycle_s", dtype: "VARCHAR" }],
  },
];

const KPIS = [
  {
    id: "k1", dataset_id: "d1", name: "Avg cycle time", description: "Seconds per part", sql: "SELECT 1 FROM {table}",
    unit: "s", direction: "lower_is_better", enabled: true, tags: [],
  },
  {
    id: "k2", dataset_id: "gone", name: "Legacy yield", description: null, sql: "SELECT 1 FROM {table}",
    unit: null, direction: "higher_is_better", enabled: false, tags: [],
  },
];

function Loc() {
  return <div data-testid="loc">{useLocation().pathname}</div>;
}

function render() {
  return renderPage(
    <>
      <Kpis />
      <Loc />
    </>,
    { route: "/kpis" },
  );
}

function row(name: string) {
  return screen.getByText(name).closest("tr") as HTMLElement;
}

function field(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input, textarea") as HTMLInputElement;
}

describe("Kpis", () => {
  it("lists KPIs with dataset names, units and direction", async () => {
    mockApi({ "/kpis": KPIS, "/datasets": DATASETS });
    render();
    await screen.findByText("Avg cycle time");
    const k1 = row("Avg cycle time");
    expect(within(k1).getByText("Seconds per part")).toBeTruthy();
    expect(await within(k1).findByText("Line 1 output")).toBeTruthy();
    expect(within(k1).getByText("s")).toBeTruthy();
    expect(within(k1).getByText("lower is better")).toBeTruthy();
    const k2 = row("Legacy yield");
    expect(within(k2).getByText("disabled")).toBeTruthy();
    // Unknown dataset falls back to the raw id.
    expect(within(k2).getByText("gone")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Dashboards" }));
    expect(screen.getByTestId("loc").textContent).toBe("/kpis/dashboards");
  });

  it("shows the empty state", async () => {
    mockApi({ "/kpis": [], "/datasets": [] });
    render();
    expect(await screen.findByText("No KPIs yet")).toBeTruthy();
  });

  it("computes a KPI and shows its value and status, or its error", async () => {
    const api = mockApi({
      "/kpis": KPIS,
      "/datasets": DATASETS,
      "POST /kpis/compute": ({ body }: { body: { kpi_ids: string[] } }) =>
        body.kpi_ids[0] === "k1"
          ? [{ kpi_id: "k1", value: 42.5, error: null, eval: { status: "breach", trend: "up" } }]
          : [{ kpi_id: "k2", value: null, error: "Division by zero", eval: null }],
    });
    render();
    await screen.findByText("Avg cycle time");

    await userEvent.click(within(row("Avg cycle time")).getByRole("button", { name: "Compute" }));
    expect(await within(row("Avg cycle time")).findByText("42.5")).toBeTruthy();
    expect(within(row("Avg cycle time")).getByText("breach")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/kpis/compute")!.body).toEqual({
      dataset_id: "d1", kpi_ids: ["k1"], force: true, include_eval: true,
    });

    await userEvent.click(within(row("Legacy yield")).getByRole("button", { name: "Compute" }));
    expect(await within(row("Legacy yield")).findByText("Division by zero")).toBeTruthy();
    // The first result is kept.
    expect(within(row("Avg cycle time")).getByText("42.5")).toBeTruthy();
  });

  it("deletes a KPI and refetches", async () => {
    let list = KPIS;
    const api = mockApi({
      "/kpis": () => list,
      "/datasets": DATASETS,
      "DELETE /kpis/k2": () => {
        list = KPIS.slice(0, 1);
        return new Response(null, { status: 204 });
      },
    });
    render();
    await screen.findByText("Legacy yield");
    await userEvent.click(within(row("Legacy yield")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByText("Legacy yield")).toBeNull());
    expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/kpis/k2")).toBe(true);
  });

  it("seeds manufacturing templates for the chosen dataset", async () => {
    let list: typeof KPIS = [];
    const api = mockApi({
      "/kpis": () => list,
      "/datasets": DATASETS,
      "POST /kpis/seed/manufacturing": () => {
        list = KPIS.slice(0, 1);
        return [];
      },
    });
    render();
    await screen.findByText("No KPIs yet");
    const seed = screen.getByRole("button", { name: "Seed manufacturing KPIs" }) as HTMLButtonElement;
    expect(seed.disabled).toBe(true);

    const picker = screen.getByRole("option", { name: "Seed templates for…" }).closest("select")!;
    await waitFor(() => expect(within(picker).getByRole("option", { name: "Line 1 output" })).toBeTruthy());
    await userEvent.selectOptions(picker, "d1");
    await userEvent.click(seed);

    expect(await screen.findByText("Avg cycle time")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/kpis/seed/manufacturing")!.body).toEqual({ dataset_id: "d1" });
  });

  it("creates a KPI with tags and a breakdown, then closes the dialog", async () => {
    const api = mockApi({ "/kpis": [], "/datasets": DATASETS, "POST /kpis": { id: "k9" } });
    render();
    await screen.findByText("No KPIs yet");
    await userEvent.click(screen.getByRole("button", { name: "New KPI" }));

    await userEvent.type(field("Name"), "Avg cycle by machine");
    await userEvent.type(field("Description"), "per machine");
    await userEvent.selectOptions(field("Dataset"), "d1");
    await userEvent.type(field("Unit"), "s");
    await userEvent.selectOptions(field("Direction"), "lower_is_better");
    await userEvent.type(field("Tags"), "quality, , cycle ");

    const [group, agg, value] = Array.from(
      screen.getByText("Breakdown (optional)", { selector: "label" }).parentElement!.querySelectorAll("select"),
    );
    expect(agg.disabled).toBe(true);
    await userEvent.selectOptions(group, "machine");
    expect(value.disabled).toBe(true);
    await userEvent.selectOptions(agg, "avg");
    await userEvent.selectOptions(value, "cycle_s");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "Create" })).toBeNull());
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      name: "Avg cycle by machine",
      description: "per machine",
      dataset_id: "d1",
      sql: "SELECT AVG(TRY_CAST(value AS DOUBLE)) FROM {table}",
      unit: "s",
      direction: "lower_is_better",
      tags: ["quality", "cycle"],
      breakdown_group_col: "machine",
      breakdown_agg: "avg",
      breakdown_value_col: "cycle_s",
    });
  }, 15_000); // long typing sequence; slow under a loaded parallel run

  it("sends nulls for blank optional fields and can be cancelled", async () => {
    const api = mockApi({ "/kpis": [], "/datasets": DATASETS, "POST /kpis": { id: "k9" } });
    render();
    await screen.findByText("No KPIs yet");
    await userEvent.click(screen.getByRole("button", { name: "New KPI" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("button", { name: "Create" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "New KPI" }));
    await userEvent.type(field("Name"), "Rows");
    await userEvent.selectOptions(field("Dataset"), "d1");
    await userEvent.clear(field("SQL"));
    await userEvent.type(field("SQL"), "SELECT COUNT(*) FROM {{table}");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      name: "Rows", description: null, dataset_id: "d1", sql: "SELECT COUNT(*) FROM {table}", unit: null,
      direction: "neutral", tags: [], breakdown_group_col: null, breakdown_agg: null, breakdown_value_col: null,
    });
  }, 15_000); // long typing sequence; slow under a loaded parallel run

  it("does not send a breakdown value column once the group column is cleared", async () => {
    const api = mockApi({ "/kpis": [], "/datasets": DATASETS, "POST /kpis": { id: "k9" } });
    render();
    await screen.findByText("No KPIs yet");
    await userEvent.click(screen.getByRole("button", { name: "New KPI" }));
    await userEvent.type(field("Name"), "Avg cycle");
    await userEvent.selectOptions(field("Dataset"), "d1");
    const [group, agg, value] = Array.from(
      screen.getByText("Breakdown (optional)", { selector: "label" }).parentElement!.querySelectorAll("select"),
    );
    await userEvent.selectOptions(group, "machine");
    await userEvent.selectOptions(agg, "avg");
    await userEvent.selectOptions(value, "cycle_s");
    await userEvent.selectOptions(group, "");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")!.body).toMatchObject({
      breakdown_group_col: null, breakdown_agg: null, breakdown_value_col: null,
    });
  });

  const fail = (detail: string) => new Response(JSON.stringify({ detail }), { status: 400 });

  it("shows the server's detail when creating a KPI fails, keeping the dialog open", async () => {
    mockApi({ "/kpis": [], "/datasets": DATASETS, "POST /kpis": fail("SQL must include {table}") });
    render();
    await screen.findByText("No KPIs yet");
    await userEvent.click(screen.getByRole("button", { name: "New KPI" }));
    await userEvent.type(field("Name"), "Bad");
    await userEvent.selectOptions(field("Dataset"), "d1");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText("SQL must include {table}")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create" })).toBeTruthy();
  });

  it("shows the server's detail when deleting a KPI fails", async () => {
    mockApi({ "/kpis": KPIS, "/datasets": DATASETS, "DELETE /kpis/k2": new Response(JSON.stringify({ detail: "KPI is on a dashboard" }), { status: 409 }) });
    render();
    await screen.findByText("Legacy yield");
    await userEvent.click(within(row("Legacy yield")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("KPI is on a dashboard")).toBeTruthy();
  });

  it("shows the server's detail when computing a KPI fails", async () => {
    mockApi({ "/kpis": KPIS, "/datasets": DATASETS, "POST /kpis/compute": new Response(JSON.stringify({ detail: "Dataset missing" }), { status: 500 }) });
    render();
    await screen.findByText("Avg cycle time");
    await userEvent.click(within(row("Avg cycle time")).getByRole("button", { name: "Compute" }));
    expect(await screen.findByText("Dataset missing")).toBeTruthy();
  });

  it("shows the server's detail when seeding fails", async () => {
    mockApi({ "/kpis": [], "/datasets": DATASETS, "POST /kpis/seed/manufacturing": fail("Templates already seeded") });
    render();
    await screen.findByText("No KPIs yet");
    const picker = screen.getByRole("option", { name: "Seed templates for…" }).closest("select")!;
    await waitFor(() => expect(within(picker).getByRole("option", { name: "Line 1 output" })).toBeTruthy());
    await userEvent.selectOptions(picker, "d1");
    await userEvent.click(screen.getByRole("button", { name: "Seed manufacturing KPIs" }));
    expect(await screen.findByText("Templates already seeded")).toBeTruthy();
  });
});
