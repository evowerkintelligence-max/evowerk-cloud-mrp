// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { QueryPanel, SqlPanel } from "./QueryPanels";

// CodeMirror is unreliable under jsdom; a textarea stand-in also exposes the column dtypes it was given.
vi.mock("@/components/SqlCodeEditor", () => ({
  SqlCodeEditor: ({ value, onChange, columns }: { value: string; onChange: (v: string) => void; columns: { name: string; dtype: string }[] }) => (
    <textarea
      aria-label="SQL editor"
      data-columns={columns.map((c) => `${c.name}:${c.dtype}`).join(",")}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

const D = {
  id: "d1", name: "Line 1", original_filename: "l.csv", fmt: "csv", row_count: 10, size_bytes: 1, created_at: "",
  columns: [{ name: "machine", dtype: "VARCHAR" }, { name: "qty", dtype: "VARCHAR" }],
  preview: [],
};

const HEALTH = { columns: [{ name: "machine", inferred_dtype: "text" }, { name: "qty", inferred_dtype: "numeric" }] };

const RESULT = { columns: ["machine", "sum_qty"], rows: [{ machine: "M1", sum_qty: 40 }, { machine: "M2", sum_qty: null }] };

function fields(label: string) {
  return screen.getAllByText(label, { selector: "label" }).map((l) => l.parentElement!.querySelector("select, input") as HTMLInputElement);
}

describe("QueryPanel", () => {
  it("builds a grouped, filtered query, renders results and sends them to a report", async () => {
    const api = mockApi({
      "/datasets/d1/health": HEALTH,
      "POST /datasets/d1/query": RESULT,
      "POST /reports/inbox": { id: "i1" },
    });
    renderPage(<QueryPanel d={D} />);
    const run = screen.getByRole("button", { name: "Run query" }) as HTMLButtonElement;
    await waitFor(() => expect(run.disabled).toBe(false));

    await userEvent.click(screen.getByRole("button", { name: "+ Add field" }));
    const [, secondColumn] = fields("Column");
    await userEvent.selectOptions(secondColumn, "qty");
    await userEvent.selectOptions(fields("Aggregate")[1], "sum");
    await userEvent.click(screen.getAllByTitle("machine")[0].closest("label")!.querySelector("input")!);
    await userEvent.click(screen.getByRole("button", { name: "+ Add filter" }));
    // Filter rows come after the two select-field rows.
    await userEvent.selectOptions(fields("Column")[2], "qty");
    await userEvent.selectOptions(fields("Op")[0], "gt");
    await userEvent.type(fields("Value")[0], "5");
    await userEvent.selectOptions(fields("Order by")[0], "machine");
    await userEvent.click(screen.getByText("Descending").querySelector("input")!);
    await userEvent.clear(fields("Limit")[0]);
    await userEvent.type(fields("Limit")[0], "50");
    await userEvent.click(run);

    expect(await screen.findByText("Results (2 rows)")).toBeTruthy();
    const queryBody = api.calls.find((c) => c.path === "/datasets/d1/query")!.body;
    expect(queryBody).toEqual({
      select: [{ column: "machine" }, { column: "qty", agg: "sum" }],
      group_by: ["machine"],
      filters: [{ column: "qty", op: "gt", value: 5 }],
      order_by: "machine",
      order_desc: true,
      limit: 50,
    });
    const m2 = screen.getByText("M2").closest("tr") as HTMLElement;
    expect(within(m2).getByText("—")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Send to report" }));
    expect(await screen.findByText("Sent to report inbox.")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/reports/inbox")!.body).toEqual({
      label: "Line 1", type: "table", dataset_id: "d1", query: queryBody,
    });
  });

  it("splits 'in' filters, keeps text values as strings, and supports the items join", async () => {
    const api = mockApi({ "/datasets/d1/health": HEALTH, "POST /datasets/d1/query": { columns: [], rows: [] } });
    renderPage(<QueryPanel d={D} />);
    const run = screen.getByRole("button", { name: "Run query" }) as HTMLButtonElement;
    await waitFor(() => expect(run.disabled).toBe(false));

    await userEvent.selectOptions(fields("Join Items by column (optional)")[0], "machine");
    expect(screen.getAllByText("items.on_hand", { selector: "option" }).length).toBeGreaterThan(0);
    for (let i = 0; i < 4; i++) await userEvent.click(screen.getByRole("button", { name: "+ Add filter" }));
    await userEvent.selectOptions(fields("Op")[0], "in");
    await userEvent.type(fields("Value")[0], "M1, M2,");
    await userEvent.type(fields("Value")[1], "7");
    await userEvent.selectOptions(fields("Column")[3], "items.on_hand");
    await userEvent.type(fields("Value")[2], "3");
    await userEvent.type(fields("Value")[3], "dropped");
    // Remove the last filter row again (Remove #0 belongs to the select-field row).
    await userEvent.click(screen.getAllByRole("button", { name: "Remove" })[4]);
    expect(fields("Value")).toHaveLength(3);
    await userEvent.click(run);

    await screen.findByText("Results (0 rows)");
    expect(api.calls.find((c) => c.method === "POST")!.body).toMatchObject({
      join_items_on: "machine",
      filters: [
        { column: "machine", op: "in", value: ["M1", "M2"] },
        { column: "machine", op: "eq", value: "7" },
        { column: "items.on_hand", op: "eq", value: 3 },
      ],
      limit: 500,
    });
  });

  it("shows query errors", async () => {
    mockApi({
      "/datasets/d1/health": HEALTH,
      "POST /datasets/d1/query": new Response(JSON.stringify({ detail: "Unknown column" }), { status: 400 }),
    });
    renderPage(<QueryPanel d={D} />);
    const run = screen.getByRole("button", { name: "Run query" }) as HTMLButtonElement;
    await waitFor(() => expect(run.disabled).toBe(false));
    await userEvent.click(run);
    expect(await screen.findByText("Unknown column")).toBeTruthy();
  });
});

describe("SqlPanel", () => {
  it("passes inferred dtypes to the editor, runs the typed SQL and renders rows", async () => {
    const api = mockApi({ "/datasets/d1/health": HEALTH, "POST /datasets/d1/sql": RESULT });
    renderPage(<SqlPanel d={D} />);
    const editor = screen.getByLabelText("SQL editor") as HTMLTextAreaElement;
    expect(editor.value).toBe("SELECT * FROM dataset LIMIT 100");
    await waitFor(() => expect(editor.dataset.columns).toBe("machine:text,qty:numeric"));

    await userEvent.clear(editor);
    await userEvent.type(editor, "SELECT machine FROM dataset");
    await userEvent.click(screen.getByRole("button", { name: "Run SQL" }));

    expect(await screen.findByText("Results (2 rows)")).toBeTruthy();
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({ sql: "SELECT machine FROM dataset", limit: 500 });
    expect(screen.getByText("40")).toBeTruthy();
  });

  it("strips DuckDB error-class jargon from errors", async () => {
    mockApi({
      "/datasets/d1/health": HEALTH,
      "POST /datasets/d1/sql": new Response(
        JSON.stringify({ detail: 'Query failed: Binder Error: Referenced column "nope" not found' }),
        { status: 400 },
      ),
    });
    renderPage(<SqlPanel d={D} />);
    await userEvent.click(screen.getByRole("button", { name: "Run SQL" }));
    expect(await screen.findByText('Query failed: Referenced column "nope" not found')).toBeTruthy();
  });
});
