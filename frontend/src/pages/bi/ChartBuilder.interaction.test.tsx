// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { ChartBuilder } from "./ChartBuilder";

type Opt = {
  series: { type: string; data: (number | { value: number; name?: string })[] }[];
  xAxis?: { data?: string[] };
  yAxis?: { data?: string[] };
  dataZoom?: unknown;
};

// echarts needs canvas, which jsdom lacks; the stand-in summarizes the option it was handed.
vi.mock("@/pages/analytics/shared/EChart", () => ({
  EChart: ({ option }: { option: Opt }) => {
    const s = option.series[0];
    const cats = option.xAxis?.data ?? option.yAxis?.data ?? s.data.map((p) => (typeof p === "object" ? p.name : ""));
    const vals = s.data.map((p) => (typeof p === "object" ? p.value : p));
    return (
      <div data-testid="echart" data-type={s.type} data-zoom={String(Boolean(option.dataZoom))}>
        {cats.join("|")}={vals.join("|")}
      </div>
    );
  },
}));

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

const DATASETS = [
  {
    id: "d1", name: "Orders", original_filename: "o.csv", fmt: "csv", row_count: 1200, size_bytes: 1, created_at: "",
    columns: [{ name: "sku", dtype: "VARCHAR" }, { name: "qty", dtype: "VARCHAR" }],
  },
  {
    id: "d2", name: "Scrap", original_filename: "s.csv", fmt: "csv", row_count: 5, size_bytes: 1, created_at: "",
    columns: [{ name: "reason", dtype: "VARCHAR" }],
  },
];

const HEALTH = { columns: [{ name: "sku", inferred_dtype: "text" }, { name: "qty", inferred_dtype: "numeric" }] };

const PREVIEW = { columns: ["sku", "sum_qty"], rows: [{ sku: "A", sum_qty: 5 }, { sku: "B", sum_qty: "7" }] };

function field(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLSelectElement;
}

function chip(name: string) {
  return screen.getAllByTitle("Drag onto a field above, or click to assign").find((el) => el.textContent === name)!;
}

function Loc() {
  return <div data-testid="loc">{useLocation().pathname}</div>;
}

function render() {
  return renderPage(
    <>
      <ChartBuilder />
      <Loc />
    </>,
    { route: "/bi/new" },
  );
}

/** Picks Orders, adds sum(qty), and assigns sku/sum_qty as category/value by clicking chips. */
async function buildSkuByQty() {
  await screen.findByText("Orders (1,200 rows)");
  await userEvent.selectOptions(field("Dataset"), "d1");
  const preview = screen.getByRole("button", { name: "Preview" }) as HTMLButtonElement;
  await screen.findAllByTitle("Drag onto a field above, or click to assign");
  await userEvent.click(screen.getByRole("button", { name: "+ Add field" }));
  await userEvent.selectOptions(screen.getAllByText("Column", { selector: "label" })[1].parentElement!.querySelector("select")!, "qty");
  await userEvent.selectOptions(screen.getAllByText("Aggregate", { selector: "label" })[1].parentElement!.querySelector("select")!, "sum");
  expect(preview.disabled).toBe(true);
  await userEvent.click(chip("sku"));
  await userEvent.click(chip("sum_qty"));
  await waitFor(() => expect(preview.disabled).toBe(false));
  return preview;
}

describe("ChartBuilder", () => {
  it("previews the chart for the picked fields and saves it to a dashboard", async () => {
    const api = mockApi({
      "/datasets": DATASETS,
      "/bi/dashboards": [{ id: "b1", name: "Ops overview" }],
      "/datasets/d1/health": HEALTH,
      "POST /bi/charts/preview": PREVIEW,
      "POST /bi/charts": { id: "c1", dashboard_id: "b1" },
    });
    render();
    const preview = await buildSkuByQty();
    await userEvent.click(preview);

    const chart = await screen.findByTestId("echart");
    expect(chart.dataset.type).toBe("bar");
    expect(chart.textContent).toBe("A|B=5|7");
    const previewBody = api.calls.find((c) => c.path === "/bi/charts/preview")!.body as Record<string, unknown>;
    expect(previewBody).toMatchObject({
      dataset_id: "d1", chart_type: "bar", category_field: "sku", value_field: "sum_qty",
      query: { select: [{ column: "sku" }, { column: "qty", agg: "sum" }] },
    });

    await userEvent.type(screen.getByPlaceholderText("e.g. Qty by SKU"), " Qty by SKU ");
    await userEvent.selectOptions(field("Add to dashboard (optional)"), "b1");
    await userEvent.click(screen.getByRole("button", { name: "Save chart" }));

    await waitFor(() => expect(screen.getByTestId("loc").textContent).toBe("/bi/b1"));
    expect(api.calls.find((c) => c.method === "POST" && c.path === "/bi/charts")!.body).toMatchObject({
      ...previewBody, name: "Qty by SKU", dashboard_id: "b1",
    });
  });

  it("saves an untitled chart without a dashboard and stays on the page", async () => {
    const api = mockApi({
      "/datasets": DATASETS,
      "/bi/dashboards": [],
      "/datasets/d1/health": HEALTH,
      "POST /bi/charts/preview": PREVIEW,
      "POST /bi/charts": { id: "c1", dashboard_id: null },
    });
    render();
    await userEvent.click(await buildSkuByQty());
    await screen.findByTestId("echart");
    await userEvent.click(screen.getByRole("button", { name: "Save chart" }));

    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path === "/bi/charts")).toBe(true));
    expect(api.calls.find((c) => c.path === "/bi/charts")!.body).toMatchObject({ name: "Untitled chart", dashboard_id: null });
    expect(screen.getByTestId("loc").textContent).toBe("/bi/new");
  });

  it("renders each chart type from the same preview, with zoom for long series", async () => {
    const rows = Array.from({ length: 13 }, (_, i) => ({ sku: `S${i}`, sum_qty: i }));
    mockApi({
      "/datasets": DATASETS,
      "/bi/dashboards": [],
      "/datasets/d1/health": HEALTH,
      "POST /bi/charts/preview": { columns: ["sku", "sum_qty"], rows },
    });
    render();
    await userEvent.click(await buildSkuByQty());
    const chart = await screen.findByTestId("echart");
    expect(chart.dataset.zoom).toBe("true");

    const expected: Record<string, string> = {
      horizontal_bar: "bar", line: "line", area: "line", pie: "pie", donut: "pie", bar: "bar",
    };
    for (const [type, series] of Object.entries(expected)) {
      await userEvent.selectOptions(field("Chart type"), type);
      expect(screen.getByTestId("echart").dataset.type).toBe(series);
      expect(screen.getByTestId("echart").textContent).toContain("S12");
    }
  });

  it("warns when the preview has no chartable rows and shows preview errors", async () => {
    let fail = false;
    mockApi({
      "/datasets": DATASETS,
      "/bi/dashboards": [],
      "/datasets/d1/health": HEALTH,
      "POST /bi/charts/preview": () =>
        fail
          ? new Response(JSON.stringify({ detail: "Bad aggregate" }), { status: 400 })
          : { columns: ["other"], rows: [{ other: 1 }] },
    });
    render();
    const preview = await buildSkuByQty();
    await userEvent.click(preview);
    expect(await screen.findByText(/No rows to chart/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save chart" })).toBeNull();

    fail = true;
    await userEvent.click(preview);
    expect(await screen.findByText("Bad aggregate")).toBeTruthy();
  });

  it("assigns fields by drag and drop, clears them by click, and resets on dataset change", async () => {
    mockApi({ "/datasets": DATASETS, "/bi/dashboards": [], "/datasets/d1/health": HEALTH, "/datasets/d2/health": { columns: [] } });
    render();
    await screen.findByText("Orders (1,200 rows)");
    await userEvent.selectOptions(field("Dataset"), "d1");
    await screen.findAllByTitle("Drag onto a field above, or click to assign");

    const valueZone = screen.getByText("Value field (chart values)", { selector: "label" }).parentElement!.querySelector("div")!;
    const data = new Map<string, string>();
    const dataTransfer = { setData: (k: string, v: string) => data.set(k, v), getData: (k: string) => data.get(k) ?? "" };
    fireEvent.dragStart(chip("sku"), { dataTransfer });
    fireEvent.dragOver(valueZone, { dataTransfer });
    fireEvent.drop(valueZone, { dataTransfer });
    const assigned = await screen.findByTitle("Click to clear");
    expect(valueZone.contains(assigned)).toBe(true);

    await userEvent.click(assigned);
    expect(screen.queryByTitle("Click to clear")).toBeNull();

    await userEvent.click(chip("sku"));
    expect(screen.getByTitle("Click to clear")).toBeTruthy();
    await userEvent.selectOptions(field("Dataset"), "d2");
    expect(screen.queryByTitle("Click to clear")).toBeNull();
    expect(chip("reason")).toBeTruthy();
  });
});
