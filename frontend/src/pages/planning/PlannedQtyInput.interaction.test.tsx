// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { PlannedQtyInput } from "./PlannedQtyInput";
import type { GridProduct } from "./types";

function product(over: Partial<GridProduct> = {}): GridProduct {
  return {
    item_id: "p1",
    item_sku: "WID-1",
    item_name: "Widget",
    planned_qty: "10.0000",
    needed_by: "2026-11-01",
    planned_bucket_count: 1,
    committed_qty: "0",
    committed_overdue: false,
    on_hand: "0",
    materials_status: "in_stock",
    missing_materials: [],
    arriving_is_drafted: false,
    ready_by: null,
    ready_by_unknown: false,
    suggested_qty: null,
    in_production: "0",
    outstanding_qty: "10",
    can_make: true,
    cannot_make_reason: null,
    detail: [],
    ...over,
  };
}

function setup(p: GridProduct, routes: Record<string, unknown> = {}, readOnly = false) {
  const api = mockApi({ "PUT /planning/grid/p1": { ok: true }, ...routes });
  const onError = vi.fn();
  const onSaved = vi.fn();
  const view = renderPage(<PlannedQtyInput product={p} onError={onError} onSaved={onSaved} readOnly={readOnly} />);
  const puts = () => api.calls.filter((c) => c.method === "PUT");
  return { api, onError, onSaved, puts, view };
}

const qtyInput = () => screen.getByLabelText("Planned quantity for WID-1") as HTMLInputElement;
const dateInput = () => screen.getByLabelText("Needed by for WID-1") as HTMLInputElement;

describe("PlannedQtyInput", () => {
  it("saves the latest typed quantity once on blur and reports success", async () => {
    const { puts, onSaved } = setup(product());
    expect(qtyInput().value).toBe("10");

    await userEvent.clear(qtyInput());
    await userEvent.type(qtyInput(), "25");
    fireEvent.blur(qtyInput());

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(puts()).toHaveLength(1);
    expect(puts()[0].body).toEqual({ quantity: "25", needed_by: "2026-11-01" });
  });

  it("saves on its own after the debounce without a blur", async () => {
    const { puts } = setup(product());
    await userEvent.clear(qtyInput());
    await userEvent.type(qtyInput(), "4");
    await waitFor(() => expect(puts()).toHaveLength(1), { timeout: 2000 });
    expect(puts()[0].body).toEqual({ quantity: "4", needed_by: "2026-11-01" });
  });

  it("holds a first quantity until a date is picked, then saves both", async () => {
    const { puts } = setup(product({ planned_qty: "0", needed_by: null, planned_bucket_count: 0, outstanding_qty: "0" }));

    await userEvent.clear(qtyInput());
    await userEvent.type(qtyInput(), "8");
    fireEvent.blur(qtyInput());
    expect(screen.getByText("Pick a date to save this quantity.")).toBeTruthy();
    expect(dateInput().getAttribute("aria-invalid")).toBe("true");
    expect(puts()).toHaveLength(0);

    fireEvent.change(dateInput(), { target: { value: "2026-12-01" } });
    fireEvent.blur(dateInput());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).toEqual({ quantity: "8", needed_by: "2026-12-01" });
    expect(screen.queryByText("Pick a date to save this quantity.")).toBeNull();
  });

  it("reverts to the server value and reports the error when a save is refused", async () => {
    const { onError, onSaved } = setup(product(), {
      "PUT /planning/grid/p1": new Response(JSON.stringify({ detail: "Quantity too large" }), { status: 400 }),
    });
    await userEvent.clear(qtyInput());
    await userEvent.type(qtyInput(), "99999");
    fireEvent.blur(qtyInput());

    await waitFor(() => expect(onError).toHaveBeenCalledWith("Widget: Quantity too large"));
    expect(qtyInput().value).toBe("10");
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("asks before collapsing a multi-date plan; cancel restores, confirm saves and stops asking", async () => {
    const { puts } = setup(product({ planned_bucket_count: 3 }));

    await userEvent.type(qtyInput(), "5");
    expect(screen.getByText(/spread across 3 dates/)).toBeTruthy();
    fireEvent.blur(qtyInput());
    expect(puts()).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/spread across 3 dates/)).toBeNull();
    expect(qtyInput().value).toBe("10");

    await userEvent.clear(qtyInput());
    await userEvent.type(qtyInput(), "30");
    await userEvent.click(screen.getByRole("button", { name: "Save one date" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).toEqual({ quantity: "30", needed_by: "2026-11-01" });

    await userEvent.type(qtyInput(), "1");
    expect(screen.queryByText(/spread across 3 dates/)).toBeNull();
    fireEvent.blur(qtyInput());
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1].body).toEqual({ quantity: "301", needed_by: "2026-11-01" });
  });

  it("raises a job for the outstanding quantity", async () => {
    const { api, onSaved } = setup(product({ outstanding_qty: "6" }), { "POST /work-orders": { id: "wo1" } });
    await userEvent.click(screen.getByRole("button", { name: "Make 6 of WID-1" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(api.calls.find((c) => c.path === "/work-orders")!.body).toEqual({
      item_id: "p1",
      quantity_planned: "6",
      start_now: true,
    });
  });

  it("reports the backend's reason when raising a job fails", async () => {
    const { onError } = setup(product(), {
      "POST /work-orders": new Response(JSON.stringify({ detail: "Bolt short by 4 at MAIN" }), { status: 409 }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Make 10 of WID-1" }));
    await waitFor(() => expect(onError).toHaveBeenCalledWith("Widget: Bolt short by 4 at MAIN"));
  });

  it("disables Make and explains why when the product cannot be made", () => {
    setup(product({ can_make: false, cannot_make_reason: "No active recipe" }));
    expect((screen.getByRole("button", { name: "Make 10 of WID-1" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("No active recipe")).toBeTruthy();
  });

  it("says a job is covering the row, or waiting on materials, when nothing is outstanding", () => {
    setup(product({ outstanding_qty: "0" }));
    expect(screen.getByText("A job is covering this row.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Make/ })).toBeNull();
  });

  it("flags a covering job that is short of materials", () => {
    setup(product({ outstanding_qty: "0", materials_status: "missing" }));
    expect(screen.getByText("A job is waiting on materials for this row.")).toBeTruthy();
  });

  it("accepts the suggested quantity from history in one click", async () => {
    const { puts } = setup(product({ planned_qty: "0", suggested_qty: "12.0000", outstanding_qty: "0" }));
    await userEvent.click(screen.getByRole("button", { name: "Use suggested quantity for WID-1" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).toEqual({ quantity: "12", needed_by: "2026-11-01" });
    expect(qtyInput().value).toBe("12");
  });

  it("fills the suggestion but waits for a date when nothing is planned yet", async () => {
    const { puts } = setup(
      product({ planned_qty: "0", needed_by: null, planned_bucket_count: 0, suggested_qty: "7", outstanding_qty: "0" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Use suggested quantity for WID-1" }));
    expect(qtyInput().value).toBe("7");
    expect(screen.getByText("Pick a date to save this quantity.")).toBeTruthy();
    expect(puts()).toHaveLength(0);
  });

  it("read-only shows the quantity with committed and in-production lines, no inputs", () => {
    setup(product({ committed_qty: "4.000", committed_overdue: true, in_production: "2" }), {}, true);
    expect(screen.getByText("10")).toBeTruthy();
    expect(screen.getByText("4 ordered, late")).toBeTruthy();
    expect(screen.getByText("2 in production")).toBeTruthy();
    expect(screen.queryByRole("spinbutton")).toBeNull();
  });

  it("flushes a pending edit when the row unmounts", async () => {
    const { puts, view } = setup(product());
    await userEvent.clear(qtyInput());
    await userEvent.type(qtyInput(), "3");
    view.unmount();
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).toEqual({ quantity: "3", needed_by: "2026-11-01" });
  });
});
