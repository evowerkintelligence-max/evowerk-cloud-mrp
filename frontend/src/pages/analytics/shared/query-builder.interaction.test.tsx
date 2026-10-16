// @vitest-environment jsdom
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { QueryBuilderFields, useQueryBuilder } from "./query-builder";

const NAMES = ["machine", "units", "reason"];
const HEALTH = {
  columns: [
    { name: "machine", inferred_dtype: "text" },
    { name: "units", inferred_dtype: "numeric" },
    { name: "reason", inferred_dtype: "text" },
  ],
};

// Mirrors how the dataset Query tab drives the builder: render the fields,
// then serialize with toRequestBody() on submit.
function Harness({ showOrderLimit }: { showOrderLimit?: boolean }) {
  const qb = useQueryBuilder("ds1", NAMES);
  const [body, setBody] = useState<unknown>(null);
  return (
    <div>
      <QueryBuilderFields qb={qb} names={NAMES} showOrderLimit={showOrderLimit} />
      <button onClick={() => setBody(qb.toRequestBody())}>Build</button>
      {qb.healthLoading && <p>loading health</p>}
      <pre data-testid="body">{body ? JSON.stringify(body) : ""}</pre>
    </div>
  );
}

function control(scope: HTMLElement, label: string) {
  return within(scope).getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLElement;
}
const built = () => JSON.parse(screen.getByTestId("body").textContent!);

async function ready() {
  mockApi({ "/datasets/ds1/health": HEALTH });
  renderPage(<Harness />);
  await waitFor(() => expect(screen.queryByText("loading health")).toBeNull());
}

describe("QueryBuilderFields + useQueryBuilder", () => {
  it("builds the default body: first column, no grouping, limit 500", async () => {
    await ready();
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(built()).toEqual({
      select: [{ column: "machine" }],
      group_by: [],
      filters: [],
      order_desc: false,
      limit: 500,
    });
  });

  it("serializes aggregates, aliases, group-by, ordering, and a custom limit", async () => {
    await ready();
    const row = screen.getByText("Aggregate", { selector: "label" }).closest("div.flex") as HTMLElement;
    await userEvent.selectOptions(control(row, "Column"), "units");
    await userEvent.selectOptions(control(row, "Aggregate"), "sum");
    await userEvent.type(control(row, "Alias"), "  total  ");

    await userEvent.click(screen.getByRole("button", { name: "+ Add field" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "machine" }));
    await userEvent.selectOptions(screen.getByText("Order by", { selector: "label" }).parentElement!.querySelector("select")!, "units");
    await userEvent.click(screen.getByRole("checkbox", { name: "Descending" }));
    const limit = screen.getByText("Limit", { selector: "label" }).parentElement!.querySelector("input")!;
    await userEvent.clear(limit);
    await userEvent.type(limit, "50");

    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(built()).toEqual({
      select: [{ column: "units", agg: "sum", alias: "total" }, { column: "machine" }],
      group_by: ["machine"],
      filters: [],
      order_by: "units",
      order_desc: true,
      limit: 50,
    });
  });

  it("coerces filter values by column type and op", async () => {
    await ready();
    const addFilter = screen.getByRole("button", { name: "+ Add filter" });
    await userEvent.click(addFilter);
    await userEvent.click(addFilter);
    await userEvent.click(addFilter);
    const rows = screen.getAllByText("Op", { selector: "label" }).map((l) => l.closest("div.flex") as HTMLElement);

    // numeric column + comparison op -> Number
    await userEvent.selectOptions(control(rows[0], "Column"), "units");
    await userEvent.selectOptions(control(rows[0], "Op"), "gte");
    await userEvent.type(control(rows[0], "Value"), "10");
    // "in" -> trimmed list, blanks dropped
    await userEvent.selectOptions(control(rows[1], "Op"), "in");
    expect(control(rows[1], "Value").getAttribute("placeholder")).toBe("comma,separated,values");
    await userEvent.type(control(rows[1], "Value"), "M1, M2,,");
    // contains on a numeric column stays a string
    await userEvent.selectOptions(control(rows[2], "Column"), "units");
    await userEvent.selectOptions(control(rows[2], "Op"), "contains");
    await userEvent.type(control(rows[2], "Value"), "12");

    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(built().filters).toEqual([
      { column: "units", op: "gte", value: 10 },
      { column: "machine", op: "in", value: ["M1", "M2"] },
      { column: "units", op: "contains", value: "12" },
    ]);

    // Removing the middle filter drops just that row.
    await userEvent.click(within(rows[1]).getByRole("button", { name: "Remove" }));
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(built().filters.map((f: { op: string }) => f.op)).toEqual(["gte", "contains"]);
  });

  it("offers items.* columns once a join is picked and treats numeric item columns as numbers", async () => {
    await ready();
    expect(screen.queryByRole("option", { name: "items.sku" })).toBeNull();
    await userEvent.selectOptions(screen.getByText("Join Items by column (optional)", { selector: "label" }).parentElement!.querySelector("select")!, "machine");
    expect(screen.getAllByRole("option", { name: "items.on_hand" }).length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: "+ Add filter" }));
    const row = screen.getByText("Op", { selector: "label" }).closest("div.flex") as HTMLElement;
    await userEvent.selectOptions(control(row, "Column"), "items.on_hand");
    await userEvent.type(control(row, "Value"), "5");
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(built()).toMatchObject({ join_items_on: "machine", filters: [{ column: "items.on_hand", op: "eq", value: 5 }] });

    await userEvent.selectOptions(control(row, "Column"), "items.sku");
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(built().filters[0]).toEqual({ column: "items.sku", op: "eq", value: "5" });
  });

  it("can't remove the last select field, and hides order/limit when asked", async () => {
    mockApi({ "/datasets/ds1/health": HEALTH });
    renderPage(<Harness showOrderLimit={false} />);
    const remove = await screen.findByRole("button", { name: "Remove" });
    expect((remove as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText("Order by", { selector: "label" })).toBeNull();
    expect(screen.queryByText("Limit", { selector: "label" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "+ Add field" }));
    const removes = screen.getAllByRole("button", { name: "Remove" }) as HTMLButtonElement[];
    expect(removes.every((b) => !b.disabled)).toBe(true);
    await userEvent.click(removes[1]);
    expect(screen.getAllByRole("button", { name: "Remove" })).toHaveLength(1);
  });

  it("unticking a group-by column removes it", async () => {
    await ready();
    const box = screen.getByRole("checkbox", { name: "reason" });
    await userEvent.click(box);
    await userEvent.click(box);
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(built().group_by).toEqual([]);
  });
});
