// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { BuildCalculatorPanel } from "./BuildCalculator";

const ITEMS = [
  { id: "i1", sku: "BIKE-1", name: "Bike", item_type: "finished_good", unit_of_measure: "ea" },
  { id: "i2", sku: "FRAME-1", name: "Frame", item_type: "component", unit_of_measure: "ea" },
];

const PLAN = {
  item_id: "i1",
  item_sku: "BIKE-1",
  item_name: "Bike",
  build_quantity: "10",
  yield_loss_pct: "5",
  lines: [
    { item_id: "i2", item_sku: "FRAME-1", item_name: "Frame", is_manufactured: true, on_hand: "0", net_requirement: "10", suggested_order_quantity: "10" },
    { item_id: "i3", item_sku: "TUBE-1", item_name: "Tube", is_manufactured: false, on_hand: "4", net_requirement: "36", suggested_order_quantity: "40" },
    { item_id: "i4", item_sku: "BOLT-10", item_name: "Hex bolt", is_manufactured: false, on_hand: "500", net_requirement: "0", suggested_order_quantity: "0" },
    { item_id: "i5", item_sku: "PAINT-1", item_name: "Paint", is_manufactured: false, on_hand: "0", net_requirement: "2", suggested_order_quantity: "5" },
  ],
};

const BODY = { item_id: "i1", build_quantity: "10", yield_loss_pct: "5" };

function statText(label: string) {
  let el = screen.getByText(label);
  while (!/\d/.test(el.textContent ?? "")) el = el.parentElement!;
  return el.textContent;
}

async function fillAndCalculate() {
  await screen.findByRole("option", { name: "BIKE-1 - Bike" });
  await userEvent.selectOptions(screen.getByLabelText("Product"), "i1");
  await userEvent.type(screen.getByLabelText("Build quantity"), "10");
  await userEvent.clear(screen.getByLabelText("Expected yield loss %"));
  await userEvent.type(screen.getByLabelText("Expected yield loss %"), "5");
  await userEvent.click(screen.getByRole("button", { name: "Calculate" }));
}

describe("BuildCalculatorPanel", () => {
  // Typewriter-animated text reads prefers-reduced-motion; jsdom has no matchMedia.
  beforeEach(() => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
  });

  it("asks for a product and a positive quantity before calling the API", async () => {
    const api = mockApi({ "/items": ITEMS });
    renderPage(<BuildCalculatorPanel />);
    await screen.findByRole("option", { name: "BIKE-1 - Bike" });

    await userEvent.click(screen.getByRole("button", { name: "Calculate" }));
    expect(screen.getByText("Pick a product to build")).toBeTruthy();

    await userEvent.selectOptions(screen.getByLabelText("Product"), "i1");
    await userEvent.type(screen.getByLabelText("Build quantity"), "0");
    await userEvent.click(screen.getByRole("button", { name: "Calculate" }));
    expect(screen.getByText("Enter a build quantity greater than 0")).toBeTruthy();
    expect(api.calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("explodes the recipe and shows what to order versus what stock covers", async () => {
    const api = mockApi({ "/items": ITEMS, "POST /mrp/build-plan": PLAN });
    renderPage(<BuildCalculatorPanel />);
    await fillAndCalculate();

    await screen.findByText("TUBE-1 - Tube");
    expect(api.calls.find((c) => c.path === "/mrp/build-plan")!.body).toEqual(BODY);
    expect(statText("Components")).toBe("Components4");
    expect(statText("To order")).toBe("To order2");
    expect(statText("Covered by stock")).toBe("Covered by stock1");

    const frameRow = screen.getByText(/FRAME-1 - Frame/, { selector: "td" }).closest("tr")!;
    expect(within(frameRow).getByText("make")).toBeTruthy();
    expect(within(frameRow).queryByText(/^Order/)).toBeNull();
    expect(within(screen.getByText("TUBE-1 - Tube").closest("tr")!).getByText("Order 40")).toBeTruthy();
    expect(within(screen.getByText("BOLT-10 - Hex bolt").closest("tr")!).queryByText(/^Order/)).toBeNull();

    // Editing an input invalidates the shown plan.
    await userEvent.type(screen.getByLabelText("Build quantity"), "0");
    expect(screen.queryByText("TUBE-1 - Tube")).toBeNull();
  });

  it("drops a plan response that no longer matches the inputs", async () => {
    let release!: () => void;
    mockApi({
      "/items": ITEMS,
      "POST /mrp/build-plan": () => new Promise((resolve) => (release = () => resolve(PLAN))),
    });
    renderPage(<BuildCalculatorPanel />);
    await fillAndCalculate();
    await waitFor(() => expect(release).toBeDefined());

    await userEvent.type(screen.getByLabelText("Build quantity"), "0");
    release();
    await waitFor(() => expect((screen.getByRole("button", { name: "Calculate" }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByText("TUBE-1 - Tube")).toBeNull();
  });

  it("shows the API error when the calculation fails", async () => {
    mockApi({
      "/items": ITEMS,
      "POST /mrp/build-plan": new Response(JSON.stringify({ detail: "Bike has no active BOM" }), { status: 400 }),
    });
    renderPage(<BuildCalculatorPanel />);
    await fillAndCalculate();
    expect(await screen.findByText("Bike has no active BOM")).toBeTruthy();
  });

  it("reviews, confirms and reports generated POs and unassigned items", async () => {
    const api = mockApi({
      "/items": ITEMS,
      "POST /mrp/build-plan": PLAN,
      "POST /mrp/build-plan/generate-pos": {
        created: [
          { po_id: "p1", po_number: 101, supplier_name: "Tube Co", line_count: 1 },
          { po_id: "p2", po_number: 102, supplier_name: "Mega Supply", line_count: 3 },
        ],
        unassigned: [{ item_id: "i5", item_sku: "PAINT-1", item_name: "Paint", net_requirement: "2" }],
      },
    });
    renderPage(<BuildCalculatorPanel />);
    await fillAndCalculate();

    await userEvent.click(await screen.findByRole("button", { name: "Review and generate purchase orders" }));
    expect(screen.getByText("About to order 2 items:")).toBeTruthy();
    expect(screen.getByText("TUBE-1 - Tube: 40")).toBeTruthy();
    expect(screen.getByText("PAINT-1 - Paint: 5")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("About to order 2 items:")).toBeNull();
    expect(api.calls.some((c) => c.path === "/mrp/build-plan/generate-pos")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Review and generate purchase orders" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm & create" }));

    expect(await screen.findByText("Draft PO-101")).toBeTruthy();
    expect(screen.getByText(/Tube Co · 1 line$/)).toBeTruthy();
    expect(screen.getByText(/Mega Supply · 3 lines/)).toBeTruthy();
    expect(screen.getByText(/No supplier set for: PAINT-1\./)).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/mrp/build-plan/generate-pos")!.body).toEqual(BODY);
  });

  it("says so when no POs were created, and surfaces a generate failure", async () => {
    let fail = false;
    mockApi({
      "/items": ITEMS,
      "POST /mrp/build-plan": { ...PLAN, lines: [PLAN.lines[1]] },
      "POST /mrp/build-plan/generate-pos": () =>
        fail
          ? new Response(JSON.stringify({ detail: "Supplier inactive" }), { status: 409 })
          : { created: [], unassigned: [] },
    });
    renderPage(<BuildCalculatorPanel />);
    await fillAndCalculate();
    await userEvent.click(await screen.findByRole("button", { name: "Review and generate purchase orders" }));
    expect(screen.getByText("About to order 1 item:")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Confirm & create" }));
    expect(await screen.findByText("No POs created.")).toBeTruthy();

    // Recalculate to clear the result, then fail the generate call.
    fail = true;
    await userEvent.click(screen.getByRole("button", { name: "Calculate" }));
    await userEvent.click(await screen.findByRole("button", { name: "Review and generate purchase orders" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm & create" }));
    expect(await screen.findByText("Supplier inactive")).toBeTruthy();
  });

  it("hides the PO section when nothing needs ordering", async () => {
    mockApi({ "/items": ITEMS, "POST /mrp/build-plan": { ...PLAN, lines: [PLAN.lines[2]] } });
    renderPage(<BuildCalculatorPanel />);
    await fillAndCalculate();
    await screen.findByText("BOLT-10 - Hex bolt");
    expect(screen.queryByText("Generate purchase orders")).toBeNull();
  });
});
