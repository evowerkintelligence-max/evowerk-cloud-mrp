// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { CleanPanel } from "./CleanPanel";

const D = {
  id: "d1", name: "Line 1", original_filename: "l.csv", fmt: "csv", row_count: 10, size_bytes: 1, created_at: "",
  columns: [{ name: "qty", dtype: "VARCHAR" }], preview: [],
};

function box(label: string) {
  return screen.getByText(label).querySelector("input") as HTMLInputElement;
}

function field(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLInputElement;
}

describe("CleanPanel", () => {
  it("posts the default ops and reports the created dataset", async () => {
    const onCreated = vi.fn();
    const api = mockApi({
      "POST /datasets/d1/clean": { dataset: { ...D, id: "d2", name: "Line 1 (cleaned)", row_count: 9 }, ops: ["trim strings", "drop duplicates"] },
    });
    renderPage(<CleanPanel d={D} onCreated={onCreated} />);

    await userEvent.click(screen.getByRole("button", { name: "Run cleaning" }));
    expect(await screen.findByText('Created "Line 1 (cleaned)" — 9 rows')).toBeTruthy();
    expect(screen.getByText("Applied: trim strings, drop duplicates")).toBeTruthy();
    expect(api.calls[0].body).toEqual({
      trim_strings: true, drop_duplicates: true, fill_missing: false, remove_outliers: false,
      standardize_numeric: false, output_name: "Line 1 (cleaned)",
    });

    await userEvent.click(screen.getByRole("button", { name: "Open cleaned dataset" }));
    expect(onCreated).toHaveBeenCalledWith("d2");
  });

  it("sends fill and outlier options only when enabled", async () => {
    const api = mockApi({ "POST /datasets/d1/clean": { dataset: D, ops: [] } });
    renderPage(<CleanPanel d={D} onCreated={() => {}} />);

    await userEvent.click(box("Fill missing values"));
    await userEvent.type(screen.getByPlaceholderText("e.g. 0"), "0");
    await userEvent.click(box("Handle outliers (IQR method, across all numeric columns)"));
    await userEvent.clear(field("IQR factor"));
    await userEvent.type(field("IQR factor"), "3");
    await userEvent.selectOptions(field("Action"), "cap");
    await userEvent.click(box("Standardize numeric columns (z-score)"));
    await userEvent.clear(field("Output dataset name"));
    await userEvent.click(screen.getByRole("button", { name: "Run cleaning" }));

    await waitFor(() => expect(api.calls).toHaveLength(1));
    expect(api.calls[0].body).toEqual({
      trim_strings: true, drop_duplicates: true, fill_missing: true, fill_strategy: "custom", fill_value: "0",
      remove_outliers: true, outlier_iqr_factor: 3, outlier_strategy: "cap", standardize_numeric: true,
    });
  });

  it("drops the custom fill value for statistical strategies and falls back to factor 1.5", async () => {
    const api = mockApi({ "POST /datasets/d1/clean": { dataset: D, ops: [] } });
    renderPage(<CleanPanel d={D} onCreated={() => {}} />);

    await userEvent.click(box("Fill missing values"));
    await userEvent.selectOptions(field("Strategy"), "median");
    expect(screen.queryByPlaceholderText("e.g. 0")).toBeNull();
    await userEvent.click(box("Handle outliers (IQR method, across all numeric columns)"));
    await userEvent.clear(field("IQR factor"));
    await userEvent.click(screen.getByRole("button", { name: "Run cleaning" }));

    await waitFor(() => expect(api.calls).toHaveLength(1));
    expect(api.calls[0].body).toMatchObject({ fill_strategy: "median", outlier_iqr_factor: 1.5, outlier_strategy: "remove" });
    expect(api.calls[0].body).not.toHaveProperty("fill_value");
  });

  it("disables the run with no ops selected and shows server errors", async () => {
    mockApi({ "POST /datasets/d1/clean": new Response(JSON.stringify({ detail: "Dataset is empty" }), { status: 400 }) });
    renderPage(<CleanPanel d={D} onCreated={() => {}} />);
    const run = screen.getByRole("button", { name: "Run cleaning" }) as HTMLButtonElement;

    await userEvent.click(box("Trim whitespace from text values"));
    await userEvent.click(box("Drop duplicate rows"));
    expect(run.disabled).toBe(true);

    await userEvent.click(box("Drop duplicate rows"));
    await userEvent.click(run);
    expect(await screen.findByText("Dataset is empty")).toBeTruthy();
  });
});
