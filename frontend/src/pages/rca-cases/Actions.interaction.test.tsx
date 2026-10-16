// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Actions } from "./Actions";

const COLUMNS = ["date", "machine", "planned_minutes", "operating_time", "total_units", "good_units"];
const ACTIONS = [
  { id: "a1", node_id: null, description: "Replace spindle", owner: null, status: "open", completed_at: null, verification: null },
  { id: "a2", node_id: null, description: "Retrain operator", owner: null, status: "done", completed_at: "2026-09-12", verification: null },
  {
    id: "a3",
    node_id: null,
    description: "New SOP",
    owner: null,
    status: "verified",
    completed_at: "2026-09-01",
    verification: { before_oee: 61.2, after_oee: 68.4, delta_oee: 7.2 },
  },
];

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/rca-cases/cases/c1/actions": ACTIONS,
    "POST /rca-cases/cases/c1/actions": { id: "a4" },
    "PATCH /rca-cases/cases/c1/actions/a1": { ...ACTIONS[0], status: "done" },
    "POST /rca-cases/cases/c1/actions/a2/verify": ACTIONS[2],
    "DELETE /rca-cases/cases/c1/actions/a1": new Response(null, { status: 204 }),
    ...overrides,
  });
}

describe("Actions", () => {
  it("renders actions with per-status controls and the verification result", async () => {
    api();
    renderPage(<Actions caseId="c1" columns={COLUMNS} />);
    expect(await screen.findByText("Replace spindle")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Mark done" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Verify effectiveness" })).toBeTruthy();
    // verified actions can't be deleted
    expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(2);
    expect(screen.getByText(/OEE 61.2% → 68.4% \(\+7.2pts\)/)).toBeTruthy();
  });

  it("adds an action with the typed description", async () => {
    const { calls } = api();
    renderPage(<Actions caseId="c1" columns={COLUMNS} />);
    await userEvent.type(screen.getByPlaceholderText("Describe the corrective action…"), "Add poka-yoke");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ description: "Add poka-yoke" });
  });

  it("shows an add error", async () => {
    api({ "POST /rca-cases/cases/c1/actions": new Response(JSON.stringify({ detail: "Too long" }), { status: 422 }) });
    renderPage(<Actions caseId="c1" columns={COLUMNS} />);
    await userEvent.type(screen.getByPlaceholderText("Describe the corrective action…"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText("Too long")).toBeTruthy();
  });

  it("marks done, verifies with the auto-mapped columns, and deletes after confirm", async () => {
    const { calls } = api();
    vi.stubGlobal("confirm", vi.fn(() => true));
    renderPage(<Actions caseId="c1" columns={COLUMNS} />);
    await userEvent.click(await screen.findByRole("button", { name: "Mark done" }));
    await userEvent.click(screen.getByRole("button", { name: "Verify effectiveness" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ status: "done" });
    expect(calls.find((c) => c.path.endsWith("/verify"))!.body).toEqual({
      planned_time_col: "planned_minutes",
      operating_time_col: "operating_time",
      total_units_col: "total_units",
      good_units_col: "good_units",
      date_col: "date",
    });
    expect(calls.find((c) => c.method === "DELETE")!.path).toBe("/rca-cases/cases/c1/actions/a1");
  });

  it("disables verify until all five columns are mapped, and enables it once mapped by hand", async () => {
    api();
    renderPage(<Actions caseId="c1" columns={["machine", "plan_m", "op_m", "made", "ok_qty", "day"]} />);
    const verify = (await screen.findByRole("button", { name: "Verify effectiveness" })) as HTMLButtonElement;
    expect(verify.disabled).toBe(true);
    expect(screen.getByText("Map all 5 columns above to verify effectiveness.")).toBeTruthy();
    const selects = screen.getAllByRole("combobox");
    const picks = ["plan_m", "op_m", "made", "ok_qty", "day"];
    for (let i = 0; i < picks.length; i++) await userEvent.selectOptions(selects[i], picks[i]);
    expect(verify.disabled).toBe(false);
  });

  it("shows a verify error", async () => {
    api({
      "POST /rca-cases/cases/c1/actions/a2/verify": new Response(JSON.stringify({ detail: "No data after completion" }), { status: 400 }),
    });
    renderPage(<Actions caseId="c1" columns={COLUMNS} />);
    await userEvent.click(await screen.findByRole("button", { name: "Verify effectiveness" }));
    expect(await screen.findByText("No data after completion")).toBeTruthy();
  });
});
