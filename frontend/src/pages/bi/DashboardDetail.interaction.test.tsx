// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { DashboardDetail } from "./DashboardDetail";

type Opt = {
  series: { type: string; data: (number | { value: number; name?: string })[]; itemStyle?: { color?: string } }[];
  xAxis?: { data?: string[] };
  color?: string[];
};

// echarts needs canvas, which jsdom lacks; the stand-in summarizes the option it was handed.
vi.mock("@/pages/analytics/shared/EChart", () => ({
  EChart: ({ option }: { option: Opt }) => {
    const s = option.series[0];
    const cats = option.xAxis?.data ?? s.data.map((p) => (typeof p === "object" ? p.name : ""));
    return (
      <div data-testid="echart" data-type={s.type} data-color={s.itemStyle?.color ?? option.color?.[0]}>
        {cats.join("|")}
      </div>
    );
  },
}));

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));
afterEach(() => localStorage.clear());

const CHARTS = [
  { id: "c2", name: "Scrap by reason", chart_type: "pie", category_field: "reason", value_field: "n", position: 1 },
  { id: "c1", name: "Qty by SKU", chart_type: "bar", category_field: "sku", value_field: "qty", position: 0 },
  { id: "c3", name: "Downtime", chart_type: "line", category_field: "day", value_field: "mins", position: 2 },
];

const DATA = {
  "/bi/charts/c1/data": { columns: ["sku", "qty"], rows: [{ sku: "Bolt-A", qty: 5 }, { sku: "Nut-B", qty: 3 }] },
  "/bi/charts/c2/data": { columns: ["reason", "n"], rows: [{ reason: "Burr", n: 2 }] },
  "/bi/charts/c3/data": new Response("{}", { status: 500 }),
};

function Loc() {
  return <div data-testid="loc">{useLocation().pathname}</div>;
}

function renderAt(route = "/bi/b1") {
  return renderPage(
    <>
      <Routes>
        <Route path="/bi/:id" element={<DashboardDetail />} />
        <Route path="*" element={null} />
      </Routes>
      <Loc />
    </>,
    { route },
  );
}

/** PageHeader's Typewriter lays the description out twice (an invisible copy plus the typed one). */
function description(text: string) {
  return screen.findAllByText(text);
}

/** The Card root for a chart: the chart grid's direct child holding that title. */
function chartCard(name: string) {
  return screen.getByText(name).closest(".grid > *") as HTMLElement;
}

describe("DashboardDetail", () => {
  it("renders charts in position order, with loading errors per chart", async () => {
    mockApi({ "/bi/dashboards/b1": { id: "b1", name: "Ops overview", charts: CHARTS }, ...DATA });
    renderAt();
    await description("3 charts");
    const titles = screen.getAllByText(/Qty by SKU|Scrap by reason|Downtime/).map((e) => e.textContent);
    expect(titles).toEqual(["Qty by SKU", "Scrap by reason", "Downtime"]);

    expect(await within(chartCard("Qty by SKU")).findByText("Bolt-A|Nut-B")).toBeTruthy();
    expect(within(chartCard("Scrap by reason")).getByTestId("echart").dataset.type).toBe("pie");
    expect(await within(chartCard("Downtime")).findByText("Failed to load chart data.")).toBeTruthy();
  });

  it("filters chart categories and reports when nothing matches", async () => {
    mockApi({ "/bi/dashboards/b1": { id: "b1", name: "Ops overview", charts: CHARTS.slice(0, 2) }, ...DATA });
    renderAt();
    await within(await waitFor(() => chartCard("Qty by SKU"))).findByText("Bolt-A|Nut-B");

    await userEvent.type(screen.getByPlaceholderText("e.g. a SKU or label…"), " bolt ");
    expect(within(chartCard("Qty by SKU")).getByText("Bolt-A")).toBeTruthy();
    expect(within(chartCard("Scrap by reason")).getByText("No data matches the filter.")).toBeTruthy();
  });

  it("applies and remembers the chosen theme per dashboard", async () => {
    mockApi({ "/bi/dashboards/b1": { id: "b1", name: "Ops overview", charts: CHARTS.slice(1, 2) }, ...DATA });
    const first = renderAt();
    const chart = await screen.findByTestId("echart");
    expect(chart.dataset.color).toBe("#3987e5");

    await userEvent.selectOptions(screen.getByText("Theme", { selector: "label" }).parentElement!.querySelector("select")!, "vibrant");
    expect(screen.getByTestId("echart").dataset.color).toBe("#3b82f6");
    expect(localStorage.getItem("bi-dashboard-theme-b1")).toBe("vibrant");

    first.unmount();
    renderAt();
    expect((await screen.findByTestId("echart")).dataset.color).toBe("#3b82f6");
  });

  it("moves a chart down by swapping positions", async () => {
    const api = mockApi({
      "/bi/dashboards/b1": { id: "b1", name: "Ops overview", charts: CHARTS },
      ...DATA,
      "PATCH /bi/charts/c1": {},
      "PATCH /bi/charts/c2": {},
    });
    renderAt();
    await description("3 charts");
    await userEvent.click(within(chartCard("Qty by SKU")).getByTitle("Move down"));

    await waitFor(() => expect(api.calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    const patches = api.calls.filter((c) => c.method === "PATCH").map((c) => [c.path, c.body]);
    expect(patches).toEqual([
      ["/bi/charts/c2", { position: 0 }],
      ["/bi/charts/c1", { position: 1 }],
    ]);
  });

  it("ignores moving the first chart up, and reorders by drag and drop", async () => {
    const api = mockApi({
      "/bi/dashboards/b1": { id: "b1", name: "Ops overview", charts: CHARTS },
      ...DATA,
      "PATCH /bi/charts/c1": {},
      "PATCH /bi/charts/c2": {},
      "PATCH /bi/charts/c3": {},
    });
    renderAt();
    await description("3 charts");
    await userEvent.click(within(chartCard("Qty by SKU")).getByTitle("Move up"));
    expect(api.calls.some((c) => c.method === "PATCH")).toBe(false);

    // Drag Downtime (index 2) onto Qty by SKU (index 0): order becomes c3, c1, c2.
    fireEvent.dragStart(within(chartCard("Downtime")).getByTitle("Drag to reorder"));
    fireEvent.dragOver(chartCard("Qty by SKU"));
    fireEvent.drop(chartCard("Qty by SKU"));

    await waitFor(() => expect(api.calls.filter((c) => c.method === "PATCH")).toHaveLength(3));
    const patches = Object.fromEntries(api.calls.filter((c) => c.method === "PATCH").map((c) => [c.path, c.body]));
    expect(patches).toEqual({
      "/bi/charts/c3": { position: 0 },
      "/bi/charts/c1": { position: 1 },
      "/bi/charts/c2": { position: 2 },
    });
  });

  it("shows a reorder failure", async () => {
    mockApi({
      "/bi/dashboards/b1": { id: "b1", name: "Ops overview", charts: CHARTS.slice(0, 2) },
      ...DATA,
      "PATCH /bi/charts/c1": new Response("{}", { status: 500 }),
      "PATCH /bi/charts/c2": new Response("{}", { status: 500 }),
    });
    renderAt();
    await description("2 charts");
    await userEvent.click(within(chartCard("Scrap by reason")).getByTitle("Move up"));
    expect(await screen.findByText("Failed to reorder chart")).toBeTruthy();
  });

  it("removes a chart from the dashboard by unlinking it", async () => {
    let charts = CHARTS.slice(0, 2);
    const api = mockApi({
      "/bi/dashboards/b1": () => ({ id: "b1", name: "Ops overview", charts }),
      ...DATA,
      "PATCH /bi/charts/c2": () => {
        charts = charts.filter((c) => c.id !== "c2");
        return {};
      },
    });
    renderAt();
    await description("2 charts");
    await userEvent.click(within(chartCard("Scrap by reason")).getByTitle("Remove from dashboard"));

    await description("1 chart");
    expect(api.calls.find((c) => c.method === "PATCH")!.body).toEqual({ dashboard_id: null });
    expect(screen.queryByText("Scrap by reason")).toBeNull();
  });

  it("shows a remove failure", async () => {
    mockApi({
      "/bi/dashboards/b1": { id: "b1", name: "Ops overview", charts: CHARTS.slice(1, 2) },
      ...DATA,
      "PATCH /bi/charts/c1": new Response("{}", { status: 500 }),
    });
    renderAt();
    await description("1 chart");
    await userEvent.click(screen.getByTitle("Remove from dashboard"));
    expect(await screen.findByText("Failed to remove chart from dashboard")).toBeTruthy();
  });

  it("sends a chart to the report inbox", async () => {
    const api = mockApi({
      "/bi/dashboards/b1": { id: "b1", name: "Ops overview", charts: CHARTS.slice(1, 2) },
      ...DATA,
      "POST /reports/inbox": { id: "i1" },
    });
    renderAt();
    await userEvent.click(await screen.findByRole("button", { name: "Send to report" }));
    expect(await screen.findByText("Sent to report inbox.")).toBeTruthy();
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({ label: "Qty by SKU", type: "bi_chart", chart_id: "c1" });
  });

  it("deletes the dashboard after confirming and returns to the list", async () => {
    const confirmMock = vi.fn(() => false);
    vi.stubGlobal("confirm", confirmMock);
    const api = mockApi({
      "/bi/dashboards/b1": { id: "b1", name: "Ops overview", charts: [] },
      "DELETE /bi/dashboards/b1": new Response(null, { status: 204 }),
    });
    renderAt();
    expect(await screen.findByText(/No charts on this dashboard yet/)).toBeTruthy();
    expect(screen.queryByPlaceholderText("e.g. a SKU or label…")).toBeNull();

    const del = screen.getByRole("button", { name: /Delete dashboard/ });
    await userEvent.click(del);
    expect(confirmMock).toHaveBeenCalledWith('Delete dashboard "Ops overview"? This cannot be undone.');
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);

    confirmMock.mockReturnValue(true);
    await userEvent.click(del);
    await waitFor(() => expect(screen.getByTestId("loc").textContent).toBe("/bi"));
  });

  it("shows a delete failure, navigates back, and handles a missing dashboard", async () => {
    vi.stubGlobal("confirm", () => true);
    mockApi({
      "/bi/dashboards/b1": { id: "b1", name: "Ops overview", charts: [] },
      "DELETE /bi/dashboards/b1": new Response("{}", { status: 500 }),
    });
    const first = renderAt();
    await userEvent.click(await screen.findByRole("button", { name: /Delete dashboard/ }));
    expect(await screen.findByText("Failed to delete dashboard")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /Back to dashboards/ }));
    expect(screen.getByTestId("loc").textContent).toBe("/bi");
    first.unmount();

    mockApi({ "/bi/dashboards/zz": new Response("{}", { status: 404 }) });
    renderAt("/bi/zz");
    expect(await screen.findByText("Dashboard not found.")).toBeTruthy();
  });
});
