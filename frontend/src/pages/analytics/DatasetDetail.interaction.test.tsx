// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { DatasetDetail } from "./DatasetDetail";

// echarts needs canvas, which jsdom lacks.
vi.mock("@/pages/analytics/shared/EChart", () => ({ EChart: () => null }));

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

const DETAIL = {
  id: "d1", name: "Line 1 output", original_filename: "line1.csv", fmt: "csv", row_count: 1200,
  size_bytes: 2048, created_at: "2026-10-01T10:00:00Z",
  columns: [
    { name: "ts", dtype: "VARCHAR" },
    { name: "qty", dtype: "VARCHAR" },
    { name: "defects", dtype: "VARCHAR" },
    { name: "case_id", dtype: "VARCHAR" },
  ],
  preview: [
    { ts: "2026-10-01", qty: "10", defects: "1", case_id: "J1" },
    { ts: "2026-10-02", qty: null, defects: "0", case_id: "J2" },
  ],
};

function col(name: string, inferred_dtype: string, extra: Record<string, unknown> = {}) {
  return {
    name, inferred_dtype, null_count: 0, null_pct: 0, distinct_count: 2, top_values: [{ value: "a", count: 1 }],
    min: null, max: null, mean: null, std: null, numeric_count: null, outlier_count: null, outlier_pct: null, ...extra,
  };
}

const HEALTH = {
  row_count: 1200, column_count: 4, duplicate_rows: 12, duplicate_rows_pct: 1, total_null_cells: 1,
  total_cells: 4800, null_cells_pct: 0.02,
  columns: [
    col("ts", "datetime"),
    col("qty", "numeric", { null_pct: 30, min: 0, max: 50, outlier_count: 3, outlier_pct: 6 }),
    col("defects", "numeric", { min: 0, max: 1, outlier_count: 0, outlier_pct: 0 }),
    col("case_id", "text", { top_values: [] }),
  ],
  warnings: ["Column qty is 30% null"],
};

function Loc() {
  return <div data-testid="loc">{useLocation().pathname}</div>;
}

function renderAt(route = "/datasets/d1") {
  return renderPage(
    <>
      <Routes>
        <Route path="/datasets/:id" element={<DatasetDetail />} />
        <Route path="*" element={null} />
      </Routes>
      <Loc />
    </>,
    { route },
  );
}

async function openTab(name: string) {
  await userEvent.click(await screen.findByRole("button", { name }));
}

describe("DatasetDetail", () => {
  it("shows metadata on the overview tab and navigates back", async () => {
    mockApi({ "/datasets/d1": DETAIL });
    renderAt();
    // PageHeader's Typewriter lays the description out twice (an invisible copy plus the typed one).
    expect((await screen.findAllByText("CSV · 1,200 rows · 4 columns")).length).toBeGreaterThan(0);
    const dl = screen.getByText("Original filename").closest("div") as HTMLElement;
    expect(within(dl).getByText("line1.csv")).toBeTruthy();
    expect(screen.getByText("2 KB")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: /Back to datasets/ }));
    expect(screen.getByTestId("loc").textContent).toBe("/datasets");
  });

  it("reports a missing dataset", async () => {
    mockApi({ "/datasets/nope": new Response("{}", { status: 404 }) });
    renderAt("/datasets/nope");
    expect(await screen.findByText("Dataset not found")).toBeTruthy();
  });

  it("reports other load failures with the status", async () => {
    mockApi({ "/datasets/d1": new Response("{}", { status: 500 }) });
    renderAt();
    expect(await screen.findByText("Failed to load dataset (500)")).toBeTruthy();
  });

  it("schema tab prefers the inferred dtype over the stored one", async () => {
    mockApi({ "/datasets/d1": DETAIL, "/datasets/d1/health": HEALTH });
    renderAt();
    await openTab("schema");
    const qtyRow = (await screen.findByText("qty")).closest("tr") as HTMLElement;
    expect(await within(qtyRow).findByText("numeric")).toBeTruthy();
  });

  it("preview tab renders rows with nulls as dashes", async () => {
    mockApi({ "/datasets/d1": DETAIL });
    renderAt();
    await openTab("preview");
    expect(screen.getByText("Preview (first 2 rows)")).toBeTruthy();
    const row = screen.getByText("J2").closest("tr") as HTMLElement;
    expect(within(row).getByText("—")).toBeTruthy();
  });

  it("health tab shows warnings, stats and per-column profile", async () => {
    mockApi({ "/datasets/d1": DETAIL, "/datasets/d1/health": HEALTH });
    renderAt();
    await openTab("health");
    expect(await screen.findByText("Column qty is 30% null")).toBeTruthy();
    expect(screen.getByText("12 (1%)")).toBeTruthy();
    const qty = screen.getByText("qty", { selector: "td" }).closest("tr") as HTMLElement;
    expect(within(qty).getByText("0 – 50")).toBeTruthy();
    expect(within(qty).getByText("3 (6%)")).toBeTruthy();
    const caseRow = screen.getByText("case_id", { selector: "td" }).closest("tr") as HTMLElement;
    // no range, no outliers, no top values
    expect(within(caseRow).getAllByText("—")).toHaveLength(3);
  });

  it("health tab shows an error when the profile fails", async () => {
    mockApi({ "/datasets/d1": DETAIL, "/datasets/d1/health": new Response("{}", { status: 500 }) });
    renderAt();
    await openTab("health");
    expect(await screen.findByText("Failed to compute health.")).toBeTruthy();
  });

  it("readiness tab explains what is missing and opens ready features", async () => {
    mockApi({ "/datasets/d1": DETAIL, "/datasets/d1/health": HEALTH });
    renderAt();
    await openTab("readiness");

    const oee = (await screen.findByText("OEE")).closest("tr") as HTMLElement;
    expect(within(oee).getByText("Not ready")).toBeTruthy();
    expect(within(oee).getByText("needs 2 more numeric columns (have 2, need 4)")).toBeTruthy();
    expect((within(oee).getByRole("button", { name: "Open" }) as HTMLButtonElement).disabled).toBe(true);

    // A route-backed feature navigates away...
    const throughput = screen.getByText("Throughput").closest("tr") as HTMLElement;
    expect(within(throughput).getByText("Ready")).toBeTruthy();
    await userEvent.click(within(throughput).getByRole("button", { name: "Open" }));
    expect(screen.getByTestId("loc").textContent).toBe("/analytics/throughput");
  });

  it("readiness tab switches to an in-page tab for tab-backed features", async () => {
    mockApi({ "/datasets/d1": DETAIL, "/datasets/d1/health": HEALTH });
    renderAt();
    await openTab("readiness");
    const quality = (await screen.findByText("Quality Analyze")).closest("tr") as HTMLElement;
    await userEvent.click(within(quality).getByRole("button", { name: "Open" }));
    expect(await screen.findByText("Quality analytics")).toBeTruthy();
    expect(screen.getByTestId("loc").textContent).toBe("/datasets/d1");
  });

  it("opening a cleaned dataset navigates to it", async () => {
    mockApi({
      "/datasets/d1": DETAIL,
      "/datasets/d9": { ...DETAIL, id: "d9", name: "Line 1 output (cleaned)" },
      "POST /datasets/d1/clean": { dataset: { ...DETAIL, id: "d9", name: "Line 1 output (cleaned)" }, ops: ["trim"] },
    });
    renderAt();
    await openTab("clean");
    await userEvent.click(screen.getByRole("button", { name: "Run cleaning" }));
    await userEvent.click(await screen.findByRole("button", { name: "Open cleaned dataset" }));
    expect(screen.getByTestId("loc").textContent).toBe("/datasets/d9");
    expect(await screen.findByText("Line 1 output (cleaned)", { selector: "h1 *, h1" })).toBeTruthy();
  });
});
