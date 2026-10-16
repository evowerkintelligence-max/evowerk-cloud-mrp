// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Items } from "./Items";
import type { ItemResponse } from "./items/types";

const OWNER = { id: "o1", name: "Acme Works", role: "owner" };
const MEMBER = { id: "o1", name: "Acme Works", role: "member" };

function item(overrides: Partial<ItemResponse>): ItemResponse {
  return {
    id: "i1",
    sku: "BOLT-10",
    name: "Hex bolt",
    item_type: "component",
    unit_of_measure: "ea",
    is_lot_tracked: false,
    preferred_supplier_id: null,
    lead_time_days: 5,
    min_order_qty: "100",
    safety_stock_qty: "20",
    is_active: true,
    description: null,
    barcode: null,
    ...overrides,
  };
}

const BOLT = item({});
const STEEL = item({ id: "i2", sku: "STEEL-1", name: "Steel bar", item_type: "raw_material", is_lot_tracked: true });
const FRAME = item({ id: "i3", sku: "FRAME-1", name: "Frame", item_type: "finished_good" });
const OLD = item({ id: "i4", sku: "OLD-1", name: "Retired part", is_active: false });
const SUPPLIERS = [
  { id: "s1", name: "Acme Steel" },
  { id: "s2", name: "Bolt Co" },
];

function routes(extra: Record<string, unknown> = {}) {
  return {
    "/orgs/me": OWNER,
    "/items": [BOLT, STEEL, FRAME, OLD],
    "/suppliers": SUPPLIERS,
    "/datasets": [{ id: "d1", name: "Q3 catalog" }],
    ...extra,
  };
}

function rowFor(sku: string) {
  return screen.getByText(sku).closest("tr")!;
}

// Climbs from a StatCard label to the nearest ancestor that also holds its value.
function statText(label: string) {
  let el = screen.getByText(label);
  while (!/\d/.test(el.textContent ?? "")) el = el.parentElement!;
  return el.textContent;
}

function patches(api: ReturnType<typeof mockApi>) {
  return api.calls.filter((c) => c.method === "PATCH");
}

describe("Items", () => {
  // PageHeader's Typewriter reads prefers-reduced-motion; jsdom has no matchMedia.
  beforeEach(() => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
  });

  it("shows per-type stats and hides inactive items until toggled", async () => {
    mockApi(routes());
    renderPage(<Items />);
    await screen.findByText("BOLT-10");

    expect(statText("Total items")).toBe("Total items4");
    expect(statText("Raw materials")).toBe("Raw materials1");
    expect(statText("Components")).toBe("Components2");
    expect(statText("Finished goods")).toBe("Finished goods1");
    expect(screen.queryByText("OLD-1")).toBeNull();
    // Lot tracked column renders Yes for the steel bar.
    expect(within(rowFor("STEEL-1")).getByText("Yes")).toBeTruthy();

    await userEvent.click(screen.getByRole("checkbox", { name: "Show inactive" }));
    expect(within(rowFor("OLD-1")).getByText("Inactive")).toBeTruthy();
    expect(within(rowFor("OLD-1")).getByRole("button", { name: "Activate" })).toBeTruthy();
  });

  it("filters by SKU or name and shows a no-match state", async () => {
    mockApi(routes());
    renderPage(<Items />);
    await screen.findByText("BOLT-10");

    const search = screen.getByPlaceholderText("Search by SKU or name...");
    await userEvent.type(search, "steel");
    expect(screen.getByText("STEEL-1")).toBeTruthy();
    expect(screen.queryByText("BOLT-10")).toBeNull();

    await userEvent.clear(search);
    await userEvent.type(search, "zzz");
    expect(screen.getByText("No matching items")).toBeTruthy();
  });

  it("shows the empty state when the catalog is empty", async () => {
    mockApi(routes({ "/items": [] }));
    renderPage(<Items />);
    expect(await screen.findByText("No items yet")).toBeTruthy();
    expect(screen.queryByText("Total items")).toBeNull();
  });

  it("adds an item manually and posts every field with nulls for blanks", async () => {
    const api = mockApi(routes({ "POST /items": item({ id: "new", sku: "NUT-5" }) }));
    renderPage(<Items />);
    await userEvent.click(await screen.findByRole("button", { name: "+ Add item manually" }));

    await userEvent.type(screen.getByLabelText("SKU"), "NUT-5");
    await userEvent.type(screen.getByLabelText("Name"), "Hex nut");
    await userEvent.selectOptions(screen.getByLabelText("Type"), "raw_material");
    await userEvent.clear(screen.getByLabelText("Unit of measure"));
    await userEvent.type(screen.getByLabelText("Unit of measure"), "kg");
    await userEvent.type(screen.getByLabelText("Lead time (days)"), "7");
    await userEvent.type(screen.getByLabelText("Min order qty"), "50");
    await userEvent.type(screen.getByLabelText("Barcode"), "123456");
    await userEvent.click(screen.getByRole("checkbox", { name: "Lot tracked" }));
    await userEvent.click(screen.getByRole("button", { name: "Add item" }));

    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path === "/items")).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      sku: "NUT-5",
      name: "Hex nut",
      item_type: "raw_material",
      unit_of_measure: "kg",
      is_lot_tracked: true,
      lead_time_days: 7,
      min_order_qty: "50",
      safety_stock_qty: null,
      description: null,
      barcode: "123456",
    });
    // Form collapses back to the button on success.
    expect(await screen.findByRole("button", { name: "+ Add item manually" })).toBeTruthy();
  });

  it("surfaces the server detail when creating an item fails", async () => {
    mockApi(
      routes({ "POST /items": new Response(JSON.stringify({ detail: "SKU already exists" }), { status: 409 }) }),
    );
    renderPage(<Items />);
    await userEvent.click(await screen.findByRole("button", { name: "+ Add item manually" }));
    await userEvent.type(screen.getByLabelText("SKU"), "BOLT-10");
    await userEvent.type(screen.getByLabelText("Name"), "Dup");
    await userEvent.click(screen.getByRole("button", { name: "Add item" }));
    expect(await screen.findByText("SKU already exists")).toBeTruthy();
    // Cancel closes the form.
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("SKU")).toBeNull();
  });

  it("is read-only for non-owners", async () => {
    mockApi(routes({ "/orgs/me": MEMBER }));
    renderPage(<Items />);
    await screen.findByText("BOLT-10");
    expect(screen.getByText(/Only organization owners can add or edit items/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "+ Add item manually" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Deactivate" })).toBeNull();
    expect(screen.queryByText("Import from Dataset")).toBeNull();
    expect((screen.getByDisplayValue("Hex bolt") as HTMLInputElement).disabled).toBe(true);
  });

  it("patches text fields on blur only when the value changed", async () => {
    const api = mockApi(routes({ "PATCH /items/i1": BOLT }));
    renderPage(<Items />);
    await screen.findByText("BOLT-10");

    const name = screen.getByDisplayValue("Hex bolt");
    await userEvent.click(name);
    await userEvent.tab();
    expect(patches(api)).toHaveLength(0);

    await userEvent.clear(name);
    await userEvent.type(name, "Hex bolt M10");
    await userEvent.tab();
    await waitFor(() => expect(patches(api)).toHaveLength(1));
    expect(patches(api)[0]).toMatchObject({
      path: "/items/i1",
      body: { preferred_supplier_id: null, name: "Hex bolt M10" },
    });

    const row = rowFor("BOLT-10");
    // Text inputs in column order: name, unit of measure, description, barcode.
    const boxes = within(row).getAllByRole("textbox");
    await userEvent.clear(boxes[1]);
    await userEvent.type(boxes[1], "box");
    await userEvent.tab();
    await waitFor(() => expect(patches(api)).toHaveLength(2));
    expect(patches(api)[1].body).toEqual({ preferred_supplier_id: null, unit_of_measure: "box" });

    await userEvent.type(boxes[2], "Zinc plated");
    await userEvent.tab();
    await waitFor(() => expect(patches(api)).toHaveLength(3));
    expect(patches(api)[2].body).toEqual({ preferred_supplier_id: null, description: "Zinc plated" });

    await userEvent.type(boxes[3], "999");
    await userEvent.tab();
    await waitFor(() => expect(patches(api)).toHaveLength(4));
    expect(patches(api)[3].body).toEqual({ preferred_supplier_id: null, barcode: "999" });
  });

  it("patches numeric planning fields, sending null for a cleared value", async () => {
    const api = mockApi(routes({ "PATCH /items/i1": BOLT }));
    renderPage(<Items />);
    await screen.findByText("BOLT-10");
    const [lead, moq, safety] = within(rowFor("BOLT-10")).getAllByRole("spinbutton");

    await userEvent.clear(lead);
    await userEvent.type(lead, "12");
    await userEvent.tab();
    await waitFor(() => expect(patches(api)).toHaveLength(1));
    expect(patches(api)[0].body).toEqual({ preferred_supplier_id: null, lead_time_days: 12 });

    await userEvent.clear(moq);
    await userEvent.tab();
    await waitFor(() => expect(patches(api)).toHaveLength(2));
    expect(patches(api)[1].body).toEqual({ preferred_supplier_id: null, min_order_qty: null });

    await userEvent.clear(safety);
    await userEvent.type(safety, "35");
    await userEvent.tab();
    await waitFor(() => expect(patches(api)).toHaveLength(3));
    expect(patches(api)[2].body).toEqual({ preferred_supplier_id: null, safety_stock_qty: "35" });
  });

  it("sets the preferred supplier and toggles active status", async () => {
    const api = mockApi(routes({ "PATCH /items/i1": BOLT }));
    renderPage(<Items />);
    await screen.findByText("BOLT-10");
    const row = rowFor("BOLT-10");

    await userEvent.selectOptions(within(row).getByRole("combobox"), "s2");
    await waitFor(() => expect(patches(api)).toHaveLength(1));
    expect(patches(api)[0].body).toEqual({ preferred_supplier_id: "s2" });

    await userEvent.click(within(row).getByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(patches(api)).toHaveLength(2));
    expect(patches(api)[1].body).toEqual({ preferred_supplier_id: null, is_active: false });
  });

  it("shows the error when an inline update is rejected", async () => {
    mockApi(
      routes({ "PATCH /items/i1": new Response(JSON.stringify({ detail: "Item is locked" }), { status: 409 }) }),
    );
    renderPage(<Items />);
    await screen.findByText("BOLT-10");
    await userEvent.click(within(rowFor("BOLT-10")).getByRole("button", { name: "Deactivate" }));
    expect(await screen.findByText("Item is locked")).toBeTruthy();
  });

  it("expands an item's audit history and collapses it again", async () => {
    const api = mockApi(
      routes({
        "/items/i1/audit-log": [
          { action: "item.updated", actor_email: "ana@acme.test", metadata: {}, created_at: "2026-10-01T10:00:00Z" },
          { action: "item.created", actor_email: null, metadata: {}, created_at: "2026-09-01T10:00:00Z" },
        ],
        "/items/i2/audit-log": [],
      }),
    );
    renderPage(<Items />);
    await screen.findByText("BOLT-10");

    await userEvent.click(within(rowFor("BOLT-10")).getByRole("button", { name: "History" }));
    expect(await screen.findByText(/ana@acme\.test/)).toBeTruthy();
    expect(screen.getByText("Updated")).toBeTruthy();
    expect(screen.getByText(/by unknown/)).toBeTruthy();
    expect(api.calls.some((c) => c.path === "/items/i1/audit-log")).toBe(true);

    await userEvent.click(within(rowFor("STEEL-1")).getByRole("button", { name: "History" }));
    expect(await screen.findByText("No history yet.")).toBeTruthy();

    await userEvent.click(within(rowFor("BOLT-10")).getByRole("button", { name: "Hide" }));
    expect(screen.queryByText(/ana@acme\.test/)).toBeNull();
  });

  it("spans the expanded history row across every table column", async () => {
    mockApi(routes({ "/items/i2/audit-log": [] }));
    renderPage(<Items />);
    await screen.findByText("STEEL-1");
    await userEvent.click(within(rowFor("STEEL-1")).getByRole("button", { name: "History" }));
    const cell = (await screen.findByText("No history yet.")).closest("td")!;
    const headers = cell.closest("table")!.querySelectorAll("thead th").length;
    expect(headers).toBe(rowFor("STEEL-1").querySelectorAll("td").length);
    expect(cell.colSpan).toBe(headers);
  });

  it("imports from a chosen dataset and lists failed rows", async () => {
    const api = mockApi(
      routes({
        "POST /items/import": {
          created: [{ row: 2, sku: "A" }, { row: 3, sku: "B" }],
          errors: [
            { row: 4, sku: "C", message: "duplicate SKU" },
            { row: 5, sku: null, message: "missing SKU" },
          ],
        },
      }),
    );
    renderPage(<Items />);
    await screen.findByText("BOLT-10");
    const importBtn = screen.getByRole("button", { name: "Import from Dataset" });
    expect((importBtn as HTMLButtonElement).disabled).toBe(true);

    await userEvent.selectOptions(screen.getByDisplayValue("Choose a dataset..."), "d1");
    await userEvent.click(importBtn);

    expect(await screen.findByText(/2 item\(s\) imported/)).toBeTruthy();
    expect(screen.getByText(/2 row\(s\) failed/)).toBeTruthy();
    expect(screen.getByText("Row 4 (C): duplicate SKU")).toBeTruthy();
    expect(screen.getByText("Row 5: missing SKU")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/items/import")!.body).toEqual({ dataset_id: "d1" });
  });

  it("points owners to upload a dataset when none exist", async () => {
    mockApi(routes({ "/datasets": [] }));
    renderPage(<Items />);
    await screen.findByText("BOLT-10");
    expect(screen.getByRole("link", { name: "upload a CSV or Excel file" }).getAttribute("href")).toBe("/datasets");
  });

  it("downloads the CSV export and reports export failures", async () => {
    const createObjectURL = vi.fn(() => "blob:items");
    const revokeObjectURL = vi.fn();
    // jsdom has no object URLs; add them for this test only.
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    let fail = false;
    mockApi(
      routes({
        "/items/export": () => (fail ? new Response("", { status: 500 }) : new Response("sku,name\n", { status: 200 })),
      }),
    );
    renderPage(<Items />);
    await screen.findByText("BOLT-10");

    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect(createObjectURL).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:items");

    fail = true;
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(await screen.findByText("Failed to export items")).toBeTruthy();
    click.mockRestore();
    delete (URL as Partial<typeof URL>).createObjectURL;
    delete (URL as Partial<typeof URL>).revokeObjectURL;
  });
});
