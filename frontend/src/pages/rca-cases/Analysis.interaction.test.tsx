// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Analysis } from "./Analysis";

const COLUMNS = [
  "date",
  "machine",
  "shift",
  "planned_minutes",
  "operating_time",
  "total_units",
  "good_units",
  "reason",
  "minutes_lost",
];
const CANDIDATE = { dimension: "machine", label: "M2", oee: 58, delta_oee: -14, row_count: 300, support_pct: 25, confidence: 81 };

// The card whose title is `title` (titles are unique on this screen).
function card(title: string) {
  return screen.getByText(title).parentElement!.parentElement as HTMLElement;
}

function control(scope: HTMLElement, label: string) {
  return within(scope).getByText(label, { selector: "label" }).parentElement!.querySelector("select") as HTMLSelectElement;
}

describe("Analysis", () => {
  it("ranks candidates with auto-mapped columns and adds one as a cause", async () => {
    const { calls } = mockApi({
      "POST /rca-cases/cases/c1/evidence": { overall_oee: 72, candidates_total: 1, candidates: [CANDIDATE] },
      "POST /rca-cases/cases/c1/nodes": { id: "n9" },
    });
    renderPage(<Analysis caseId="c1" columns={COLUMNS} />);
    await userEvent.click(screen.getByRole("button", { name: "Rank candidate causes" }));
    expect(await screen.findByText("M2")).toBeTruthy();
    expect(screen.getByText("58%")).toBeTruthy();
    expect(calls.find((c) => c.path.endsWith("/evidence"))!.body).toEqual({
      planned_time_col: "planned_minutes",
      operating_time_col: "operating_time",
      total_units_col: "total_units",
      good_units_col: "good_units",
      dimension_cols: ["machine", "shift"],
      min_support_rows: 5,
    });

    await userEvent.click(screen.getByRole("button", { name: "Add as cause" }));
    expect(await screen.findByRole("button", { name: "Added" })).toBeTruthy();
    expect(calls.find((c) => c.path.endsWith("/nodes"))!.body).toEqual({
      text: "machine: M2 (OEE 58%, Δ-14pts)",
      node_type: "cause",
      evidence: [{ kind: "candidate", summary: "M2 runs 14pts below overall OEE (confidence 81)", data: CANDIDATE }],
    });
  });

  it("disables ranking when no dimension column is checked, and shows evidence errors", async () => {
    mockApi({
      "POST /rca-cases/cases/c1/evidence": new Response(JSON.stringify({ detail: "Not enough rows" }), { status: 400 }),
    });
    renderPage(<Analysis caseId="c1" columns={COLUMNS} />);
    const rank = screen.getByRole("button", { name: "Rank candidate causes" }) as HTMLButtonElement;
    await userEvent.click(screen.getByRole("checkbox", { name: "machine" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "shift" }));
    expect(rank.disabled).toBe(true);
    await userEvent.click(screen.getByRole("checkbox", { name: "shift" }));
    expect(rank.disabled).toBe(false);
    await userEvent.click(rank);
    expect(await screen.findByText("Not enough rows")).toBeTruthy();
  });

  it("runs a Pareto on chosen columns; Other buckets can't be added", async () => {
    const { calls } = mockApi({
      "POST /rca-cases/cases/c1/pareto": {
        total: 2000,
        buckets: [
          { label: "Jam", value: 1200, pct_of_total: 60, cumulative_pct: 60 },
          { label: "Other (4)", value: 800, pct_of_total: 40, cumulative_pct: 100 },
        ],
      },
      "POST /rca-cases/cases/c1/nodes": { id: "n9" },
    });
    renderPage(<Analysis caseId="c1" columns={COLUMNS} />);
    const pareto = card("Pareto");
    const run = within(pareto).getByRole("button", { name: "Run Pareto" }) as HTMLButtonElement;
    expect(run.disabled).toBe(true);
    await userEvent.selectOptions(control(pareto, "Category column"), "reason");
    await userEvent.selectOptions(control(pareto, "Value column"), "minutes_lost");
    await userEvent.click(run);
    expect(await within(pareto).findByText("Jam")).toBeTruthy();
    expect(within(pareto).getByText((1200).toLocaleString())).toBeTruthy();
    expect(calls.find((c) => c.path.endsWith("/pareto"))!.body).toEqual({ category_col: "reason", value_col: "minutes_lost", top_n: 15 });

    const addButtons = within(pareto).getAllByRole("button", { name: "Add as cause" }) as HTMLButtonElement[];
    expect(addButtons[1].disabled).toBe(true);
    await userEvent.click(addButtons[0]);
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/nodes"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/nodes"))!.body).toMatchObject({
      text: "reason: Jam (60% of total)",
      evidence: [{ kind: "pareto", summary: "Jam accounts for 60% of total minutes_lost" }],
    });
  });

  it("shows a timeline around the worst day", async () => {
    const { calls } = mockApi({
      "POST /rca-cases/cases/c1/timeline": {
        overall_oee: 72,
        events: [
          { date: "2026-09-09", oee: 70, delta_oee: -2, row_count: 40, is_center: false },
          { date: "2026-09-10", oee: 51, delta_oee: -21, row_count: 38, is_center: true },
        ],
      },
    });
    renderPage(<Analysis caseId="c1" columns={COLUMNS} />);
    await userEvent.click(screen.getByRole("button", { name: "Show timeline around worst day" }));
    expect(await screen.findByText("2026-09-10")).toBeTruthy();
    expect(screen.getByText("-21")).toBeTruthy();
    expect(calls[0].body).toMatchObject({ date_col: "date", window_days: 3, planned_time_col: "planned_minutes" });
  });

  it("disables every engine when the dataset has no columns", () => {
    const { calls } = mockApi({});
    renderPage(<Analysis caseId="c1" columns={[]} />);
    expect((screen.getByRole("button", { name: "Rank candidate causes" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Run Pareto" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Show timeline around worst day" }) as HTMLButtonElement).disabled).toBe(true);
    expect(calls).toHaveLength(0);
  });

  it("shows a timeline error", async () => {
    mockApi({ "POST /rca-cases/cases/c1/timeline": new Response(JSON.stringify({ detail: "No dates" }), { status: 400 }) });
    renderPage(<Analysis caseId="c1" columns={COLUMNS} />);
    await userEvent.click(screen.getByRole("button", { name: "Show timeline around worst day" }));
    expect(await screen.findByText("No dates")).toBeTruthy();
  });
});
