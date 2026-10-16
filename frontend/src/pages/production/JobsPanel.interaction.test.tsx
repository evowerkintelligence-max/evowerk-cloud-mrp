// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { JobsPanel } from "./JobsPanel";
import type { WorkOrder } from "./types";

const OWNER = { id: "u1", name: "Owner", role: "owner", user_id: "u1", enabled_features: ["production"] };

const BASE: WorkOrder = {
  id: "wo-1",
  wo_number: 4,
  item_id: "item-1",
  item_sku: "PUMP-1",
  item_name: "Coolant pump",
  item_is_lot_tracked: false,
  status: "scheduled",
  quantity_planned: "50.0000",
  quantity_good: "0.0000",
  quantity_scrapped: "0.0000",
  scrap_reason: null,
  remainder_disposition: null,
  location_id: "loc-1",
  location_code: "MAIN",
  scheduled_start: "2020-01-02",
  started_at: null,
  finished_at: null,
  notes: "rush",
  assigned_operator_id: null,
  components: [],
};
const SCHEDULED = BASE;
const RUNNING: WorkOrder = { ...BASE, id: "wo-2", wo_number: 5, status: "in_progress", scheduled_start: null, item_is_lot_tracked: true };

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/orgs/me": OWNER,
    "/work-orders": [SCHEDULED, RUNNING],
    "/operators": [{ id: "op1", name: "Ana" }],
    "/items": [{ id: "item-1", sku: "PUMP-1", name: "Coolant pump" }],
    "POST /work-orders": { ...BASE, id: "wo-3" },
    "POST /work-orders/wo-1/start": { ...SCHEDULED, status: "in_progress" },
    "POST /work-orders/wo-1/cancel": { ...SCHEDULED, status: "cancelled" },
    "POST /work-orders/wo-2/finish": { ...RUNNING, status: "done" },
    "PATCH /work-orders/wo-1": SCHEDULED,
    "PATCH /work-orders/wo-2": RUNNING,
    ...overrides,
  });
}

function dialog(title: string) {
  return screen.getByRole("heading", { name: title }).parentElement!.parentElement as HTMLElement;
}

describe("JobsPanel", () => {
  it("lists jobs with a Late badge and owner controls", async () => {
    api();
    renderPage(<JobsPanel />);
    expect(await screen.findByText("WO-4")).toBeTruthy();
    expect(screen.getByText("Late")).toBeTruthy();
    expect(screen.getByText("Not scheduled")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "New job" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Finish" })).toBeTruthy();
  });

  it("hides edit controls from operators", async () => {
    const { fetchMock } = api({ "/orgs/me": { ...OWNER, role: "operator" } });
    renderPage(<JobsPanel />);
    await screen.findByText("WO-4");
    // let /orgs/me resolve so the role is actually known before asserting absence
    await waitFor(() => expect(fetchMock.mock.results.length).toBeGreaterThanOrEqual(2));
    await Promise.all(fetchMock.mock.results.map((r) => r.value));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("button", { name: "New job" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
  });

  it("shows the empty state", async () => {
    api({ "/work-orders": [] });
    renderPage(<JobsPanel />);
    expect(await screen.findByText("No jobs yet")).toBeTruthy();
  });

  it("shows the list error", async () => {
    api({ "/work-orders": new Response("{}", { status: 500 }) });
    renderPage(<JobsPanel />);
    expect(await screen.findByText("Failed to load jobs")).toBeTruthy();
  });

  it("sends search and status filters as query params", async () => {
    const { calls } = api();
    renderPage(<JobsPanel />);
    await screen.findByText("WO-4");
    await userEvent.selectOptions(screen.getByRole("combobox"), "in_progress");
    await userEvent.type(screen.getByPlaceholderText("Search jobs…"), "P");
    await waitFor(() => expect(calls.some((c) => c.search === "?q=P&status=in_progress")).toBe(true));
  });

  it("starts a job, and shows the backend's 409 message when stock is short", async () => {
    const { calls } = api({
      "POST /work-orders/wo-1/start": new Response(JSON.stringify({ detail: "Short 3 x BOLT at MAIN" }), { status: 409 }),
    });
    renderPage(<JobsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Start" }));
    expect(await screen.findByText("Short 3 x BOLT at MAIN")).toBeTruthy();
    expect(calls.some((c) => c.method === "POST" && c.path === "/work-orders/wo-1/start")).toBe(true);
  });

  it("cancels a job after confirmation, and Never mind does nothing", async () => {
    const { calls } = api();
    renderPage(<JobsPanel />);
    await screen.findByText("WO-4");
    await userEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]);
    expect(screen.getByText(/WO-4 will be cancelled/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Never mind" }));
    expect(screen.queryByText(/WO-4 will be cancelled/)).toBeNull();

    await userEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]);
    await userEvent.click(screen.getByRole("button", { name: "Cancel job" }));
    await waitFor(() => expect(screen.queryByText(/WO-4 will be cancelled/)).toBeNull());
    expect(calls.some((c) => c.method === "POST" && c.path === "/work-orders/wo-1/cancel")).toBe(true);
  });

  it("shows a cancel error", async () => {
    api({ "POST /work-orders/wo-1/cancel": new Response(JSON.stringify({ detail: "Already started" }), { status: 409 }) });
    renderPage(<JobsPanel />);
    await screen.findByText("WO-4");
    await userEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]);
    await userEvent.click(screen.getByRole("button", { name: "Cancel job" }));
    expect(await screen.findByText("Already started")).toBeTruthy();
  });

  it("raises a scheduled job with the chosen item, quantity, date and notes", async () => {
    const { calls } = api();
    renderPage(<JobsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "New job" }));
    const d = dialog("Raise a job");
    await within(d).findByRole("option", { name: /PUMP-1/ });
    await userEvent.selectOptions(within(d).getByLabelText("Product"), "item-1");
    const qty = within(d).getByLabelText("Quantity");
    await userEvent.clear(qty);
    await userEvent.type(qty, "12");
    await userEvent.type(within(d).getByLabelText("Start date"), "2026-11-01");
    await userEvent.type(within(d).getByLabelText("Notes"), "for Acme");
    await userEvent.click(within(d).getByRole("button", { name: "Raise job" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Raise a job" })).toBeNull());
    expect(calls.find((c) => c.method === "POST" && c.path === "/work-orders")!.body).toEqual({
      item_id: "item-1",
      quantity_planned: "12",
      scheduled_start: "2026-11-01",
      notes: "for Acme",
      start_now: false,
    });
  });

  it("closes the raise dialog and shows the error when raising fails", async () => {
    api({ "POST /work-orders": new Response(JSON.stringify({ detail: "Item has no recipe" }), { status: 400 }) });
    renderPage(<JobsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "New job" }));
    const d = dialog("Raise a job");
    await within(d).findByRole("option", { name: /PUMP-1/ });
    await userEvent.selectOptions(within(d).getByLabelText("Product"), "item-1");
    await userEvent.click(within(d).getByRole("button", { name: "Raise job" }));
    expect(await screen.findByText("Item has no recipe")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Raise a job" })).toBeNull();
  });

  it("finishes an under-run, lot-tracked job with scrap, lot and disposition", async () => {
    const { calls } = api();
    renderPage(<JobsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Finish" }));
    const d = dialog("Finish WO-5");
    expect(within(d).getByText(/Coolant pump, planned for 50/)).toBeTruthy();
    await userEvent.type(within(d).getByLabelText("Good units"), "40");
    const scrapped = within(d).getByLabelText("Scrapped");
    await userEvent.clear(scrapped);
    await userEvent.type(scrapped, "2");
    await userEvent.type(within(d).getByLabelText("Why was it scrapped?"), "Porosity");
    await userEvent.type(within(d).getByLabelText("Lot number"), "LOT-77");
    await userEvent.selectOptions(within(d).getByLabelText("The units you did not make"), "consumed");
    await userEvent.click(within(d).getByRole("button", { name: "Finish job" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Finish WO-5" })).toBeNull());
    expect(calls.find((c) => c.path === "/work-orders/wo-2/finish")!.body).toEqual({
      quantity_good: "40",
      quantity_scrapped: "2",
      scrap_reason: "Porosity",
      lot_number: "LOT-77",
      remainder_disposition: "consumed",
    });
  });

  it("does not ask about the remainder when the full quantity was made, and reports finish errors", async () => {
    const { calls } = api({
      "/work-orders": [{ ...RUNNING, item_is_lot_tracked: false }],
      "POST /work-orders/wo-2/finish": new Response(JSON.stringify({ detail: "Location closed" }), { status: 409 }),
    });
    renderPage(<JobsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Finish" }));
    const d = dialog("Finish WO-5");
    await userEvent.type(within(d).getByLabelText("Good units"), "50");
    expect(within(d).queryByLabelText("The units you did not make")).toBeNull();
    expect(within(d).queryByLabelText("Lot number")).toBeNull();
    await userEvent.click(within(d).getByRole("button", { name: "Finish job" }));
    expect(await screen.findByText("Location closed")).toBeTruthy();
    expect(calls.find((c) => c.path === "/work-orders/wo-2/finish")!.body).toMatchObject({
      quantity_good: "50",
      quantity_scrapped: "0",
      lot_number: null,
      remainder_disposition: null,
    });
  });

  it("edits a scheduled job's fields and operator", async () => {
    const { calls } = api();
    renderPage(<JobsPanel />);
    await screen.findByText("WO-4");
    await userEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const d = dialog("Edit WO-4");
    const qty = within(d).getByLabelText("Quantity") as HTMLInputElement;
    expect(qty.value).toBe("50");
    expect((within(d).getByLabelText("Notes") as HTMLInputElement).value).toBe("rush");
    await userEvent.clear(qty);
    await userEvent.type(qty, "60");
    await userEvent.clear(within(d).getByLabelText("Notes"));
    await within(d).findByRole("option", { name: "Ana" });
    await userEvent.selectOptions(within(d).getByLabelText("Assigned operator"), "op1");
    await userEvent.click(within(d).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Edit WO-4" })).toBeNull());
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({
      assigned_operator_id: "op1",
      quantity_planned: "60",
      scheduled_start: "2020-01-02",
      notes: null,
    });
  });

  it("only sends the operator when editing a running job, and reports edit errors", async () => {
    const { calls } = api({
      "PATCH /work-orders/wo-2": new Response(JSON.stringify({ detail: "Operator inactive" }), { status: 409 }),
    });
    renderPage(<JobsPanel />);
    await screen.findByText("WO-5");
    await userEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]);
    const d = dialog("Edit WO-5");
    expect(within(d).queryByLabelText("Quantity")).toBeNull();
    await within(d).findByRole("option", { name: "Ana" });
    await userEvent.selectOptions(within(d).getByLabelText("Assigned operator"), "op1");
    await userEvent.click(within(d).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Operator inactive")).toBeTruthy();
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ assigned_operator_id: "op1" });
  });

  it("closes dialogs with Never mind without sending anything", async () => {
    const { calls } = api();
    renderPage(<JobsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Finish" }));
    await userEvent.click(within(dialog("Finish WO-5")).getByRole("button", { name: "Never mind" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await userEvent.click(within(dialog("Edit WO-4")).getByRole("button", { name: "Never mind" }));
    await userEvent.click(screen.getByRole("button", { name: "New job" }));
    await userEvent.click(within(dialog("Raise a job")).getByRole("button", { name: "Never mind" }));
    expect(screen.queryByRole("heading", { level: 2 })).toBeNull();
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });
});
