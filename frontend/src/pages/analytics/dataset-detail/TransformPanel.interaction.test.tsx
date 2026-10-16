// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { TransformPanel } from "./TransformPanel";

const D = {
  id: "d1", name: "Historian", original_filename: "h.csv", fmt: "csv", row_count: 100, size_bytes: 1, created_at: "",
  columns: [{ name: "ts", dtype: "VARCHAR" }, { name: "temp", dtype: "VARCHAR" }, { name: "pressure", dtype: "VARCHAR" }],
  preview: [],
};

function field(label: string, nth = 0) {
  return screen.getAllByText(label, { selector: "label" })[nth].parentElement!.querySelector("select, input") as HTMLInputElement;
}

// Each column name appears once per checklist; the subset checklist comes first.
function check(name: string, nth = 0) {
  return screen.getAllByTitle(name)[nth].closest("label")!.querySelector("input") as HTMLInputElement;
}

describe("TransformPanel", () => {
  it("creates a column subset with the selected columns", async () => {
    const onCreated = vi.fn();
    const api = mockApi({
      "POST /datasets/d1/transform/select-columns": {
        dataset: { ...D, id: "d2", name: "Temps", columns: D.columns.slice(0, 2) }, dropped_columns: ["pressure"],
      },
    });
    renderPage(<TransformPanel d={D} onCreated={onCreated} />);

    await userEvent.click(check("pressure"));
    await userEvent.clear(field("Output name"));
    await userEvent.type(field("Output name"), "Temps");
    await userEvent.click(screen.getByRole("button", { name: "Create subset dataset" }));

    expect(await screen.findByText('Created "Temps" — 100 rows × 2 columns')).toBeTruthy();
    expect(api.calls[0].body).toEqual({ columns: ["ts", "temp"], output_name: "Temps" });
    await userEvent.click(screen.getByRole("button", { name: "Open subset dataset" }));
    expect(onCreated).toHaveBeenCalledWith("d2");
  });

  it("disables the subset with no columns and shows subset errors", async () => {
    mockApi({
      "POST /datasets/d1/transform/select-columns": new Response(JSON.stringify({ detail: "Unknown column" }), { status: 400 }),
    });
    renderPage(<TransformPanel d={D} onCreated={() => {}} />);
    const create = screen.getByRole("button", { name: "Create subset dataset" }) as HTMLButtonElement;
    for (const c of ["ts", "temp", "pressure"]) await userEvent.click(check(c));
    expect(create.disabled).toBe(true);

    await userEvent.click(check("ts"));
    await userEvent.click(create);
    expect(await screen.findByText("Unknown column")).toBeTruthy();
  });

  it("pivots wide to tall with a timestamp and chosen tag columns", async () => {
    const onCreated = vi.fn();
    const api = mockApi({ "POST /datasets/d1/transform/pivot": { ...D, id: "d3", name: "Tall", row_count: 200, columns: D.columns } });
    renderPage(<TransformPanel d={D} onCreated={onCreated} />);

    await userEvent.selectOptions(field("Timestamp column"), "ts");
    // The tag checklist excludes the timestamp column.
    expect(screen.getAllByTitle("ts")).toHaveLength(1);
    await userEvent.click(check("temp", 1));
    await userEvent.click(screen.getByRole("button", { name: "Run transform" }));

    expect(await screen.findByText('Created "Tall" — 200 rows × 3 columns')).toBeTruthy();
    expect(api.calls[0].body).toEqual({
      direction: "wide_to_tall", output_name: "Historian (wide_to_tall)", timestamp_col: "ts", tag_cols: ["temp"],
    });
    await userEvent.click(screen.getByRole("button", { name: "Open transformed dataset" }));
    expect(onCreated).toHaveBeenCalledWith("d3");
  });

  it("pivots tall to wide with the chosen tag and value columns", async () => {
    const api = mockApi({ "POST /datasets/d1/transform/pivot": new Response(JSON.stringify({ detail: "Duplicate readings" }), { status: 400 }) });
    renderPage(<TransformPanel d={D} onCreated={() => {}} />);

    await userEvent.click(screen.getByLabelText("Tall → Wide"));
    await userEvent.selectOptions(field("Tag column"), "temp");
    await userEvent.selectOptions(field("Value column"), "pressure");
    await userEvent.click(screen.getByRole("button", { name: "Run transform" }));

    expect(await screen.findByText("Duplicate readings")).toBeTruthy();
    expect(api.calls[0].body).toMatchObject({ direction: "tall_to_wide", tag_col: "temp", value_col: "pressure" });
  });
});
