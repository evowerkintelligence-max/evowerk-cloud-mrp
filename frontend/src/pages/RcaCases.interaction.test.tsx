// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { RcaCases } from "./RcaCases";

const DATASET = {
  id: "ds1",
  name: "Line 2 shifts",
  original_filename: "line2.csv",
  fmt: "csv",
  row_count: 1200,
  size_bytes: 4096,
  created_at: "2026-09-01T00:00:00Z",
  columns: ["date", "machine", "planned_minutes", "operating_time", "total_units", "good_units"].map((name) => ({
    name,
    dtype: "string",
  })),
};

const CASE = {
  id: "c1",
  dataset_id: "ds1",
  title: "M2 underperforming",
  problem_statement: "OEE dropped since Monday",
  status: "open",
  owner: "Priya",
  created_at: "2026-09-10T08:00:00Z",
  closed_at: null,
};

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full title at once.
beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: true, media: query, addEventListener() {}, removeEventListener() {} }));
});

// Field labels aren't wired to their controls, so find the control in the label's wrapper.
function control(label: string, tag = "select") {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector(tag) as HTMLElement;
}

describe("RcaCases list", () => {
  it("lists cases and shows the empty state", async () => {
    mockApi({ "/rca-cases/cases": [], "/rca-cases/trends": { total_root_causes: 0, by_category: [] }, "/datasets": [] });
    renderPage(<RcaCases />);
    expect(await screen.findByText("No cases yet.")).toBeTruthy();
    expect(screen.queryByText("Recurring root causes")).toBeNull();
  });

  it("creates a case with the chosen dataset and opens it", async () => {
    const api = mockApi({
      "/rca-cases/cases": [CASE],
      "/rca-cases/trends": { total_root_causes: 0, by_category: [] },
      "/datasets": [DATASET],
      "POST /rca-cases/cases": { ...CASE, id: "c2", title: "New one" },
      "/rca-cases/cases/c2": { ...CASE, id: "c2", title: "New one" },
      "/rca-cases/cases/c2/nodes": [],
      "/rca-cases/taxonomy": {},
    });
    renderPage(<RcaCases />);
    expect(await screen.findByText("M2 underperforming")).toBeTruthy();
    expect(screen.getByText("Priya")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "New case" }));
    const createBtn = screen.getByRole("button", { name: "Create case" }) as HTMLButtonElement;
    expect(createBtn.disabled).toBe(true);
    await userEvent.type(control("Title", "input"), "New one");
    await userEvent.type(control("Problem statement", "input"), "Scrap spike");
    await screen.findByRole("option", { name: "Line 2 shifts" });
    await userEvent.selectOptions(control("Dataset"), "ds1");
    await userEvent.click(createBtn);

    await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      title: "New one",
      problem_statement: "Scrap spike",
      dataset_id: "ds1",
    });
    expect(await screen.findByRole("heading", { name: "New one" })).toBeTruthy();
  });

  it("shows the create error from the API", async () => {
    mockApi({
      "/rca-cases/cases": [],
      "/rca-cases/trends": { total_root_causes: 0, by_category: [] },
      "/datasets": [DATASET],
      "POST /rca-cases/cases": new Response(JSON.stringify({ detail: "Dataset is empty" }), { status: 400 }),
    });
    renderPage(<RcaCases />);
    await userEvent.click(await screen.findByRole("button", { name: "New case" }));
    await userEvent.type(control("Title", "input"), "X");
    await screen.findByRole("option", { name: "Line 2 shifts" });
    await userEvent.selectOptions(control("Dataset"), "ds1");
    await userEvent.click(screen.getByRole("button", { name: "Create case" }));
    expect(await screen.findByText("Dataset is empty")).toBeTruthy();
  });

  it("shows recurring root causes and opens a linked case", async () => {
    mockApi({
      "/rca-cases/cases": [CASE],
      "/rca-cases/trends": {
        total_root_causes: 3,
        by_category: [{ kind: "category", key: "Machine", count: 2, case_ids: ["c1", "c9"], case_titles: ["M2 underperforming"] }],
      },
      "/datasets": [DATASET],
      "/rca-cases/cases/c9": { ...CASE, id: "c9", title: "Older case" },
      "/rca-cases/cases/c9/nodes": [],
      "/rca-cases/taxonomy": {},
    });
    renderPage(<RcaCases />);
    expect(await screen.findByText("Recurring root causes")).toBeTruthy();
    expect(screen.getByText("2×")).toBeTruthy();
    // second case has no title in the payload, so it falls back to "case"
    await userEvent.click(screen.getByRole("button", { name: "case" }));
    expect(await screen.findByRole("heading", { name: "Older case" })).toBeTruthy();
  });
});

describe("RcaCases detail", () => {
  function detailApi(overrides: Record<string, unknown> = {}) {
    return mockApi({
      "/rca-cases/cases": [CASE],
      "/rca-cases/trends": { total_root_causes: 0, by_category: [] },
      "/datasets": [DATASET],
      "/rca-cases/cases/c1": CASE,
      "/rca-cases/cases/c1/nodes": [],
      "/rca-cases/taxonomy": {},
      "/rca-cases/cases/c1/actions": [],
      "/rca-cases/cases/c1/events": [
        { id: "e1", event_type: "created", summary: "Case opened", actor: null, created_at: "2026-09-10T08:00:00Z" },
      ],
      "PATCH /rca-cases/cases/c1": { ...CASE, status: "investigating" },
      "DELETE /rca-cases/cases/c1": new Response(null, { status: 204 }),
      "POST /reports/inbox": { id: "r1" },
      ...overrides,
    });
  }

  async function openCase() {
    renderPage(<RcaCases />);
    await userEvent.click(await screen.findByText("M2 underperforming"));
    await screen.findByRole("heading", { name: "M2 underperforming" });
  }

  it("patches the status from a transition button", async () => {
    const api = detailApi();
    await openCase();
    expect(screen.getAllByText("OEE dropped since Monday").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Start investigating" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(api.calls.find((c) => c.method === "PATCH")!.body).toEqual({ status: "investigating" });
  });

  it("shows a status update error", async () => {
    detailApi({ "PATCH /rca-cases/cases/c1": new Response(JSON.stringify({ detail: "Bad transition" }), { status: 409 }) });
    await openCase();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(await screen.findByText("Bad transition")).toBeTruthy();
  });

  it("deletes the case after confirmation and returns to the list", async () => {
    const api = detailApi();
    vi.stubGlobal("confirm", vi.fn(() => true));
    await openCase();
    await userEvent.click(screen.getByRole("button", { name: "Delete case" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/rca-cases/cases/c1")).toBe(true));
    expect(await screen.findByRole("heading", { name: "Root Cause Analysis" })).toBeTruthy();
  });

  it("does not delete when the confirm is cancelled", async () => {
    const api = detailApi();
    vi.stubGlobal("confirm", vi.fn(() => false));
    await openCase();
    await userEvent.click(screen.getByRole("button", { name: "Delete case" }));
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("sends the case to the report inbox", async () => {
    const api = detailApi();
    await openCase();
    await userEvent.click(screen.getByRole("button", { name: "Send to report" }));
    expect(await screen.findByText("Sent to report inbox.")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/reports/inbox")!.body).toEqual({
      label: "M2 underperforming",
      type: "rca_case",
      case_id: "c1",
    });
  });

  it("switches tabs to history and back to the list", async () => {
    detailApi();
    await openCase();
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText("Case opened")).toBeTruthy();
    expect(screen.getByText(/system/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByText("CAPA actions")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Analysis" }));
    expect(await screen.findByText("Evidence engine")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Back to cases" }));
    expect(await screen.findByRole("heading", { name: "Root Cause Analysis" })).toBeTruthy();
  });

  it("shows the empty history state", async () => {
    detailApi({ "/rca-cases/cases/c1/events": [] });
    await openCase();
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText("No history yet.")).toBeTruthy();
  });

  it("offers reopen and verify for a closed case", async () => {
    detailApi({ "/rca-cases/cases/c1": { ...CASE, status: "closed" } });
    await openCase();
    const badges = screen.getAllByText("closed");
    expect(badges.length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Mark verified" })).toBeTruthy();
    expect(within(document.body).getByRole("button", { name: "Reopen" })).toBeTruthy();
  });
});
