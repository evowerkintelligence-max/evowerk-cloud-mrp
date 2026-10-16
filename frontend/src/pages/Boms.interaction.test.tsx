// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Boms } from "./Boms";
import type { BomDetailResponse, BomLineResponse, BomResponse } from "./boms/types";

const ITEMS = [
  { id: "i1", sku: "FRAME-1", name: "Frame", item_type: "finished_good", unit_of_measure: "ea" },
  { id: "i2", sku: "WHEEL-1", name: "Wheel", item_type: "component", unit_of_measure: "ea" },
  { id: "i3", sku: "BOLT-10", name: "Hex bolt", item_type: "component", unit_of_measure: "ea" },
  { id: "i4", sku: "RIM-1", name: "Rim", item_type: "raw_material", unit_of_measure: "ea" },
];

function bom(overrides: Partial<BomResponse>): BomResponse {
  return {
    id: "b1",
    item_id: "i1",
    item_sku: "FRAME-1",
    item_name: "Frame",
    version: 2,
    status: "draft",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function line(overrides: Partial<BomLineResponse>): BomLineResponse {
  return {
    id: "l1",
    component_item_id: "i2",
    component_sku: "WHEEL-1",
    component_name: "Wheel",
    unit_of_measure: "ea",
    quantity: "2",
    sequence: 0,
    scrap_pct: "5",
    unit_id: null,
    unit_quantity: null,
    unit_code: null,
    ...overrides,
  };
}

const DRAFT = bom({});
const WHEEL_ACTIVE = bom({ id: "b2", item_id: "i2", item_sku: "WHEEL-1", item_name: "Wheel", version: 1, status: "active" });
const ARCHIVED = bom({ id: "b3", version: 1, status: "archived" });
const WHEEL_LINE = line({});
const BOLT_LINE = line({
  id: "l2",
  component_item_id: "i3",
  component_sku: "BOLT-10",
  component_name: "Hex bolt",
  quantity: "48",
  sequence: 1,
  scrap_pct: "0",
  unit_id: "u1",
  unit_quantity: "4",
  unit_code: "box",
});
const DRAFT_DETAIL: BomDetailResponse = { ...DRAFT, lines: [WHEEL_LINE, BOLT_LINE] };
const WHEEL_DETAIL: BomDetailResponse = {
  ...WHEEL_ACTIVE,
  lines: [line({ id: "l9", component_item_id: "i4", component_sku: "RIM-1", component_name: "Rim", scrap_pct: "0" })],
};

// The wire shape every PATCH sends for an existing line.
const WHEEL_WIRE = { component_item_id: "i2", quantity: "2", scrap_pct: "5", unit_id: null, unit_quantity: null };
const BOLT_WIRE = { component_item_id: "i3", quantity: "48", scrap_pct: "0", unit_id: "u1", unit_quantity: "4" };

function routes(extra: Record<string, unknown> = {}) {
  return {
    "/items": ITEMS,
    "/boms": [DRAFT, WHEEL_ACTIVE, ARCHIVED],
    "/boms/b1": DRAFT_DETAIL,
    "/boms/b2": WHEEL_DETAIL,
    "PATCH /boms/b1": DRAFT_DETAIL,
    ...extra,
  };
}

// Stat cards render above the table, so the first match is the card label, not a status badge.
function statText(label: string) {
  let el = screen.getAllByText(label)[0];
  while (!/\d/.test(el.textContent ?? "")) el = el.parentElement!;
  return el.textContent;
}

// BOM line labels also appear as <option>s and in the BOM list; the line row puts them in a <span>.
function lineRow(text: string) {
  return screen.getByText(text, { selector: "span" }).closest("tr")!;
}

function lastPatch(api: ReturnType<typeof mockApi>) {
  const patches = api.calls.filter((c) => c.method === "PATCH");
  return patches[patches.length - 1]?.body;
}

async function openDraft() {
  await userEvent.click(await screen.findByText("v2"));
  await screen.findByText("FRAME-1 · v2");
}

describe("Boms", () => {
  beforeEach(() => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
  });

  it("counts BOMs by status", async () => {
    mockApi(routes());
    renderPage(<Boms />);
    await screen.findByText("v2");
    expect(statText("Total BOMs")).toBe("Total BOMs3");
    expect(statText("Active")).toBe("Active1");
    expect(statText("Draft")).toBe("Draft1");
    expect(statText("Archived")).toBe("Archived1");
  });

  it("shows the empty state when there are no BOMs", async () => {
    mockApi(routes({ "/boms": [] }));
    renderPage(<Boms />);
    expect(await screen.findByText("No BOMs yet")).toBeTruthy();
  });

  it("creates a draft for the chosen item and opens it", async () => {
    const api = mockApi(routes({ "POST /boms": DRAFT }));
    renderPage(<Boms />);
    await screen.findByText("v2");
    await userEvent.selectOptions(screen.getByLabelText("For item"), "i1");
    await userEvent.click(screen.getByRole("button", { name: "Create draft" }));

    expect(await screen.findByText("FRAME-1 · v2")).toBeTruthy();
    expect(api.calls.find((c) => c.method === "POST" && c.path === "/boms")!.body).toEqual({ item_id: "i1" });
  });

  it("shows the server error when creating a draft fails", async () => {
    mockApi(
      routes({ "POST /boms": new Response(JSON.stringify({ detail: "Item already has a draft" }), { status: 409 }) }),
    );
    renderPage(<Boms />);
    await screen.findByText("v2");
    await userEvent.selectOptions(screen.getByLabelText("For item"), "i1");
    await userEvent.click(screen.getByRole("button", { name: "Create draft" }));
    expect(await screen.findByText("Item already has a draft")).toBeTruthy();
  });

  it("renders draft lines with unit and scrap columns, excluding the parent item from components", async () => {
    mockApi(routes());
    renderPage(<Boms />);
    await openDraft();

    const wheelRow = lineRow("WHEEL-1 - Wheel")!;
    expect(within(wheelRow).getByText("5%")).toBeTruthy();
    const boltRow = lineRow("BOLT-10 - Hex bolt")!;
    expect(within(boltRow).getByText("4 box")).toBeTruthy();
    const options = within(screen.getByLabelText("Component")).getAllByRole("option").map((o) => o.textContent);
    expect(options).not.toContain("FRAME-1 - Frame");
    expect(options).toContain("RIM-1 - Rim");
  });

  it("adds a base-unit line, appending to existing lines with contiguous sequences", async () => {
    const api = mockApi(routes({ "/items/i4/units": [] }));
    renderPage(<Boms />);
    await openDraft();

    await userEvent.selectOptions(screen.getByLabelText("Component"), "i4");
    await userEvent.clear(screen.getByLabelText("Quantity"));
    await userEvent.type(screen.getByLabelText("Quantity"), "3");
    await userEvent.clear(screen.getByLabelText("Scrap %"));
    await userEvent.type(screen.getByLabelText("Scrap %"), "2.5");
    await userEvent.click(screen.getByRole("button", { name: "Add line" }));

    await waitFor(() => expect(lastPatch(api)).toBeDefined());
    expect(lastPatch(api)).toEqual({
      expected_updated_at: "2026-10-01T00:00:00Z",
      lines: [
        { ...WHEEL_WIRE, sequence: 0 },
        { ...BOLT_WIRE, sequence: 1 },
        { component_item_id: "i4", quantity: "3", scrap_pct: "2.5", unit_id: null, unit_quantity: null, sequence: 2 },
      ],
    });
  });

  it("adds a line in an alternate unit, sending the unit quantity instead of base quantity", async () => {
    const api = mockApi(routes({ "/items/i4/units": [{ id: "u7", item_id: "i4", code: "roll", factor_to_base: "50" }] }));
    renderPage(<Boms />);
    await openDraft();

    expect((screen.getByLabelText("Unit") as HTMLSelectElement).disabled).toBe(true);
    await userEvent.selectOptions(screen.getByLabelText("Component"), "i4");
    await screen.findByRole("option", { name: "roll" });
    await userEvent.selectOptions(screen.getByLabelText("Unit"), "u7");
    await userEvent.type(screen.getByLabelText("Quantity (in unit)"), "3");
    await userEvent.click(screen.getByRole("button", { name: "Add line" }));

    await waitFor(() => expect(lastPatch(api)).toBeDefined());
    expect((lastPatch(api) as { lines: unknown[] }).lines[2]).toEqual({
      component_item_id: "i4",
      quantity: "0",
      scrap_pct: "0",
      unit_id: "u7",
      unit_quantity: "3",
      sequence: 2,
    });
  });

  it("creates a new unit inline and selects it", async () => {
    let units: unknown[] = [];
    const api = mockApi(
      routes({
        "/items/i4/units": () => units,
        "POST /items/i4/units": () => {
          units = [{ id: "u8", item_id: "i4", code: "pack", factor_to_base: "12" }];
          return units[0];
        },
      }),
    );
    renderPage(<Boms />);
    await openDraft();

    await userEvent.selectOptions(screen.getByLabelText("Component"), "i4");
    await userEvent.selectOptions(screen.getByLabelText("Unit"), "__new__");
    await userEvent.type(screen.getByPlaceholderText("code"), "pack");
    await userEvent.type(screen.getByPlaceholderText("factor"), "12");
    await userEvent.click(within(screen.getByPlaceholderText("factor").parentElement!).getByRole("button"));

    await waitFor(() => expect((screen.getByLabelText("Unit") as HTMLSelectElement).value).toBe("u8"));
    expect(api.calls.find((c) => c.method === "POST" && c.path === "/items/i4/units")!.body).toEqual({
      code: "pack",
      factor_to_base: "12",
    });
    expect(screen.getByLabelText("Quantity (in unit)")).toBeTruthy();
  });

  it("removes a line by re-sending the remaining lines", async () => {
    const api = mockApi(routes());
    renderPage(<Boms />);
    await openDraft();
    const wheelRow = lineRow("WHEEL-1 - Wheel")!;
    await userEvent.click(within(wheelRow).getByTitle("Remove line"));

    await waitFor(() => expect(lastPatch(api)).toBeDefined());
    expect(lastPatch(api)).toEqual({
      expected_updated_at: "2026-10-01T00:00:00Z",
      lines: [{ ...BOLT_WIRE, sequence: 0 }],
    });
  });

  it("refuses to remove the last line of a draft", async () => {
    const api = mockApi(routes({ "/boms/b1": { ...DRAFT_DETAIL, lines: [WHEEL_LINE] } }));
    renderPage(<Boms />);
    await openDraft();
    await userEvent.click(screen.getByTitle("Remove line"));
    expect(await screen.findByText("A BOM needs at least one line - delete the draft instead")).toBeTruthy();
    expect(api.calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("reorders lines with the move buttons and by drag and drop", async () => {
    const api = mockApi(routes());
    renderPage(<Boms />);
    await openDraft();
    const wheelRow = lineRow("WHEEL-1 - Wheel")!;
    const boltRow = lineRow("BOLT-10 - Hex bolt")!;
    expect((within(wheelRow).getByTitle("Move up") as HTMLButtonElement).disabled).toBe(true);
    expect((within(boltRow).getByTitle("Move down") as HTMLButtonElement).disabled).toBe(true);

    await userEvent.click(within(wheelRow).getByTitle("Move down"));
    await waitFor(() => expect(lastPatch(api)).toBeDefined());
    const swapped = { expected_updated_at: "2026-10-01T00:00:00Z", lines: [{ ...BOLT_WIRE, sequence: 0 }, { ...WHEEL_WIRE, sequence: 1 }] };
    expect(lastPatch(api)).toEqual(swapped);

    // Native drag and drop: the dragged id travels through dataTransfer.
    const data = new Map<string, string>();
    const dataTransfer = { setData: (k: string, v: string) => data.set(k, v), getData: (k: string) => data.get(k) ?? "" };
    fireEvent.dragStart(within(boltRow).getByTitle("Drag to reorder"), { dataTransfer });
    fireEvent.dragOver(wheelRow, { dataTransfer });
    fireEvent.drop(wheelRow, { dataTransfer });
    await waitFor(() => expect(api.calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(lastPatch(api)).toEqual(swapped);
  });

  it("validates pasted spreadsheet rows before sending anything", async () => {
    const api = mockApi(routes());
    renderPage(<Boms />);
    await openDraft();
    await userEvent.click(screen.getByRole("button", { name: "Paste from spreadsheet" }));
    const textarea = screen.getByRole("textbox");
    const importBtn = screen.getByRole("button", { name: "Import lines" });

    await userEvent.click(importBtn);
    expect(await screen.findByText('Paste rows as "SKU, quantity" - one per line')).toBeTruthy();

    await userEvent.type(textarea, "NOPE-1, 2");
    await userEvent.click(importBtn);
    expect(await screen.findByText("SKU(s) not found: NOPE-1")).toBeTruthy();

    await userEvent.clear(textarea);
    await userEvent.type(textarea, "rim-1, -3");
    await userEvent.click(importBtn);
    expect(await screen.findByText("Quantity must be a positive number - check: rim-1, -3")).toBeTruthy();

    await userEvent.clear(textarea);
    await userEvent.type(textarea, "RIM-1, 3, 150");
    await userEvent.click(importBtn);
    expect(await screen.findByText("Scrap % must be between 0 and 100 - check: RIM-1, 3, 150")).toBeTruthy();
    expect(api.calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("imports pasted rows (comma or tab separated) after the existing lines", async () => {
    const api = mockApi(routes());
    renderPage(<Boms />);
    await openDraft();
    await userEvent.click(screen.getByRole("button", { name: "Paste from spreadsheet" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "rim-1, 3, 1.5\nWHEEL-1\t4\n" } });
    await userEvent.click(screen.getByRole("button", { name: "Import lines" }));

    await waitFor(() => expect(lastPatch(api)).toBeDefined());
    expect((lastPatch(api) as { lines: unknown[] }).lines.slice(2)).toEqual([
      { component_item_id: "i4", quantity: "3", scrap_pct: "1.5", unit_id: null, unit_quantity: null, sequence: 2 },
      { component_item_id: "i2", quantity: "4", scrap_pct: "0", unit_id: null, unit_quantity: null, sequence: 3 },
    ]);
    // Panel closes on success.
    await waitFor(() => expect(screen.queryByRole("button", { name: "Import lines" })).toBeNull());
  });

  it("activates a draft", async () => {
    const api = mockApi(routes({ "POST /boms/b1/activate": { ...DRAFT, status: "active" } }));
    renderPage(<Boms />);
    await openDraft();
    await userEvent.click(screen.getByRole("button", { name: "Activate" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path === "/boms/b1/activate")).toBe(true));
  });

  it("deletes a draft only after confirmation", async () => {
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    const api = mockApi(routes({ "DELETE /boms/b1": new Response(null, { status: 204 }) }));
    renderPage(<Boms />);
    await openDraft();

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(confirm).toHaveBeenCalledWith("Delete this draft BOM for FRAME-1? This cannot be undone.");
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);

    confirm.mockReturnValue(true);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByText("FRAME-1 · v2")).toBeNull());
    expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/boms/b1")).toBe(true);
  });

  it("revises an active BOM into a new draft and opens it", async () => {
    const api = mockApi(
      routes({
        "POST /boms/b2/revise": { ...WHEEL_DETAIL, id: "b4", version: 2, status: "draft" },
        "/boms/b4": { ...WHEEL_DETAIL, id: "b4", version: 2, status: "draft" },
      }),
    );
    renderPage(<Boms />);
    await userEvent.click(await screen.findByText("WHEEL-1 - Wheel", { selector: "td" }));
    await screen.findByText("WHEEL-1 · v1");
    // Active BOMs are read-only: no add-line form.
    expect(screen.queryByRole("button", { name: "Add line" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Edit (new version)" }));
    expect(await screen.findByText("WHEEL-1 · v2")).toBeTruthy();
    expect(api.calls.some((c) => c.method === "POST" && c.path === "/boms/b2/revise")).toBe(true);
  });

  it("expands a component's own active BOM inline", async () => {
    const api = mockApi(routes());
    renderPage(<Boms />);
    await openDraft();
    const expand = screen.getByTitle("Show this component's own BOM");
    await userEvent.hover(expand);
    await userEvent.click(expand);

    expect(await screen.findByText("RIM-1 - Rim", { selector: "span" })).toBeTruthy();
    expect(api.calls.some((c) => c.path === "/boms/b2")).toBe(true);
    await userEvent.click(screen.getByTitle("Collapse"));
    expect(screen.queryByText("RIM-1 - Rim", { selector: "span" })).toBeNull();
  });
});
