// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { RoutingsPanel } from "./RoutingsPanel";

const WC = {
  id: "wc1",
  code: "CNC",
  name: "CNC cell",
  status: "active",
  machine_capacity: 1,
  default_cycle_time_seconds: null,
  default_mtbf_hours: null,
  default_mttr_hours: null,
};
const ITEM = { id: "item-1", sku: "PUMP-1", name: "Coolant pump" };
const DRAFT = {
  id: "r1",
  item_id: "item-1",
  version: 2,
  status: "draft",
  steps: [{ id: "s1", sequence: 10, work_center_id: "wc1", cycle_time_seconds: 30, setup_time_seconds: null }],
};
const ACTIVE = { id: "r0", item_id: "missing-item", version: 1, status: "active", steps: [] };

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/work-centers": [WC],
    "/items": [ITEM],
    "/routings": [DRAFT, ACTIVE],
    "POST /routings": { ...DRAFT, id: "r2", steps: [] },
    "POST /routings/r1/steps": DRAFT,
    "POST /routings/r1/activate": { ...DRAFT, status: "active" },
    ...overrides,
  });
}

describe("RoutingsPanel", () => {
  it("lists routings with item labels, falling back to the id", async () => {
    api();
    renderPage(<RoutingsPanel />);
    expect(await screen.findByText("PUMP-1 - Coolant pump", { selector: "td" })).toBeTruthy();
    expect(screen.getByText("missing-item")).toBeTruthy();
    expect(screen.getByText("v2")).toBeTruthy();
    // only drafts can be activated
    expect(screen.getAllByRole("button", { name: "Activate" })).toHaveLength(1);
  });

  it("shows the empty state", async () => {
    api({ "/routings": [] });
    renderPage(<RoutingsPanel />);
    expect(await screen.findByText("No routings yet")).toBeTruthy();
  });

  it("creates a draft routing with item_id in the query string", async () => {
    const { calls } = api();
    renderPage(<RoutingsPanel />);
    await screen.findByRole("option", { name: /PUMP-1/ });
    await userEvent.selectOptions(screen.getByLabelText("For item"), "item-1");
    await userEvent.click(screen.getByRole("button", { name: "Create draft" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/routings")).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.path === "/routings")!;
    expect(post.search).toBe("?item_id=item-1");
    expect(post.body).toBeUndefined();
  });

  it("shows a create error", async () => {
    api({ "POST /routings": new Response(JSON.stringify({ detail: "Item already has a draft" }), { status: 409 }) });
    renderPage(<RoutingsPanel />);
    await screen.findByRole("option", { name: /PUMP-1/ });
    await userEvent.selectOptions(screen.getByLabelText("For item"), "item-1");
    await userEvent.click(screen.getByRole("button", { name: "Create draft" }));
    expect(await screen.findByText("Item already has a draft")).toBeTruthy();
  });

  it("expands a draft and adds a step after the existing ones", async () => {
    const { calls } = api();
    renderPage(<RoutingsPanel />);
    await screen.findByText("v2");
    await userEvent.click(screen.getAllByTitle("Show steps")[0]);
    expect(screen.getByText("10")).toBeTruthy();
    expect(screen.getByText("30")).toBeTruthy();
    await userEvent.selectOptions(screen.getByLabelText("Work center"), "wc1");
    await userEvent.type(screen.getByLabelText("Cycle time (s)"), "45");
    await userEvent.click(screen.getByRole("button", { name: "Add step" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/routings/r1/steps")).toBe(true));
    expect(calls.find((c) => c.path === "/routings/r1/steps")!.body).toEqual([
      { sequence: 10, work_center_id: "wc1", cycle_time_seconds: 30, setup_time_seconds: null },
      { sequence: 11, work_center_id: "wc1", cycle_time_seconds: 45, setup_time_seconds: null },
    ]);
    await userEvent.click(screen.getByTitle("Collapse"));
    expect(screen.queryByRole("button", { name: "Add step" })).toBeNull();
  });

  it("shows no step form for an active routing with no steps", async () => {
    api();
    renderPage(<RoutingsPanel />);
    await screen.findByText("v1");
    await userEvent.click(screen.getAllByTitle("Show steps")[1]);
    expect(screen.getByText("No steps yet.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add step" })).toBeNull();
  });

  it("activates a draft, and surfaces activation and step errors", async () => {
    const { calls } = api({
      "POST /routings/r1/activate": new Response(JSON.stringify({ detail: "Routing has no steps" }), { status: 409 }),
      "POST /routings/r1/steps": new Response(JSON.stringify({ detail: [{ msg: "bad" }] }), { status: 422 }),
    });
    renderPage(<RoutingsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Activate" }));
    expect(await screen.findByText("Routing has no steps")).toBeTruthy();
    expect(calls.some((c) => c.path === "/routings/r1/activate")).toBe(true);

    await userEvent.click(screen.getAllByTitle("Show steps")[0]);
    await userEvent.selectOptions(screen.getByLabelText("Work center"), "wc1");
    await userEvent.click(screen.getByRole("button", { name: "Add step" }));
    // a non-string detail (validation list) falls back to the generic message
    expect(await screen.findByText("Failed to add step")).toBeTruthy();
  });
});
