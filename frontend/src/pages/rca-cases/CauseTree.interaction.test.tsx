// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { CauseTree } from "./CauseTree";

const NODES = [
  { id: "n0", parent_id: null, text: "OEE drop on M2", node_type: "problem", status: "hypothesis", category: null },
  { id: "n1", parent_id: "n0", text: "Worn spindle", node_type: "cause", status: "hypothesis", category: "Machine" },
  { id: "n2", parent_id: "n0", text: "New operator", node_type: "root_cause", status: "verified", category: null },
];
const TAXONOMY = { Machine: ["Worn tooling", "Calibration drift"], Method: ["Wrong SOP"] };

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/rca-cases/cases/c1/nodes": NODES,
    "/rca-cases/taxonomy": TAXONOMY,
    "POST /rca-cases/cases/c1/nodes": { id: "n3" },
    "PATCH /rca-cases/cases/c1/nodes/n1": NODES[1],
    "PATCH /rca-cases/cases/c1/nodes/n2": NODES[2],
    "DELETE /rca-cases/cases/c1/nodes/n1": new Response(null, { status: 204 }),
    ...overrides,
  });
}

describe("CauseTree", () => {
  it("renders nodes; the problem node has no controls", async () => {
    api();
    renderPage(<CauseTree caseId="c1" />);
    expect(await screen.findByText("Worn spindle")).toBeTruthy();
    expect(screen.getByText("OEE drop on M2")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Unmark root cause" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Mark root cause" })).toBeTruthy();
  });

  it("picks a category suggestion and posts the cause with its category", async () => {
    const { calls } = api();
    renderPage(<CauseTree caseId="c1" />);
    await screen.findByRole("option", { name: "Method" });
    const [category, suggestion] = screen.getAllByRole("combobox") as HTMLSelectElement[];
    expect(suggestion.disabled).toBe(true);
    await userEvent.selectOptions(category, "Machine");
    await userEvent.selectOptions(suggestion, "Calibration drift");
    const input = screen.getByPlaceholderText("Add a contributing cause…") as HTMLInputElement;
    expect(input.value).toBe("Calibration drift");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      text: "Calibration drift",
      node_type: "cause",
      category: "Machine",
    });
    await waitFor(() => expect(input.value).toBe(""));
  });

  it("posts a null category when none is picked and shows API errors", async () => {
    const { calls } = api({
      "POST /rca-cases/cases/c1/nodes": new Response(JSON.stringify({ detail: "Case is closed" }), { status: 409 }),
    });
    renderPage(<CauseTree caseId="c1" />);
    await userEvent.type(screen.getByPlaceholderText("Add a contributing cause…"), "Dirty sensor");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText("Case is closed")).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ text: "Dirty sensor", node_type: "cause", category: null });
  });

  it("patches status and root-cause flag, and deletes after confirm", async () => {
    const { calls } = api();
    vi.stubGlobal("confirm", vi.fn(() => true));
    renderPage(<CauseTree caseId="c1" />);
    await screen.findByText("Worn spindle");
    const statusSelects = screen.getAllByRole("combobox").slice(2) as HTMLSelectElement[];
    await userEvent.selectOptions(statusSelects[0], "ruled_out");
    await userEvent.click(screen.getByRole("button", { name: "Mark root cause" }));
    await userEvent.click(screen.getByRole("button", { name: "Unmark root cause" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    const patches = calls.filter((c) => c.method === "PATCH").map((c) => [c.path, c.body]);
    expect(patches).toEqual([
      ["/rca-cases/cases/c1/nodes/n1", { status: "ruled_out" }],
      ["/rca-cases/cases/c1/nodes/n1", { node_type: "root_cause" }],
      ["/rca-cases/cases/c1/nodes/n2", { node_type: "cause" }],
    ]);
    expect(calls.find((c) => c.method === "DELETE")!.path).toBe("/rca-cases/cases/c1/nodes/n1");
  });

  it("shows a patch error", async () => {
    api({ "PATCH /rca-cases/cases/c1/nodes/n1": new Response(JSON.stringify({ detail: "Not allowed" }), { status: 403 }) });
    renderPage(<CauseTree caseId="c1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Mark root cause" }));
    expect(await screen.findByText("Not allowed")).toBeTruthy();
  });
});
