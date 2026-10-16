// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { EngineScreen } from "./EngineScreen";
import type { EngineField } from "./datasets";

const FIELDS: EngineField[] = [
  { key: "units_col", label: "Units", required: true, hints: [/units/i] },
  { key: "machine_col", label: "Machine (optional)", hints: [/machine/i] },
  { key: "target_rate", label: "Target rate", kind: "num" },
  { key: "unit", label: "Unit", kind: "select", options: ["minutes", "hours"] },
];

const DS = {
  id: "ds1",
  name: "Line data",
  original_filename: "line.csv",
  fmt: "csv",
  row_count: 1200,
  size_bytes: 2048,
  created_at: "2026-01-01T00:00:00Z",
  columns: ["units_produced", "machine", "shift_date"].map((name) => ({ name, dtype: "VARCHAR" })),
};
const OTHER = { ...DS, id: "ds2", name: "Other", row_count: 5, columns: [{ name: "qty", dtype: "VARCHAR" }] };
const PREVIEW = {
  preview: [
    { units_produced: 120, machine: "Press 1", shift_date: "2026-01-01" },
    { units_produced: 95, machine: "Press 2", shift_date: "2026-01-02" },
  ],
};

function control(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector("select, input") as HTMLElement;
}

function Screen({ showHeader }: { showHeader?: boolean }) {
  return (
    <EngineScreen<{ total: number }>
      title="Widget"
      description="Widget analysis"
      path="/analytics/widget"
      fields={FIELDS}
      runLabel="Run widget"
      showHeader={showHeader}
    >
      {(r, body) => (
        <p>
          Total {r.total} for {String(body.dataset_id)}
        </p>
      )}
    </EngineScreen>
  );
}

function routes(extra: Record<string, unknown> = {}) {
  return {
    "/datasets": [DS, OTHER],
    "/datasets/ds1": PREVIEW,
    "/datasets/ds2": { preview: [] },
    "POST /analytics/widget": { total: 42 },
    ...extra,
  };
}

describe("EngineScreen", () => {
  // PageHeader's Typewriter reads prefers-reduced-motion.
  beforeEach(() => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
  });

  it("lists datasets with row counts and shows the header", async () => {
    mockApi(routes());
    renderPage(<Screen />);
    expect(await screen.findByRole("option", { name: "Line data (1,200 rows)" })).toBeTruthy();
    expect(screen.getAllByText("Widget analysis").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Upload one in Datasets" }).getAttribute("href")).toBe("/datasets");
    // No mapping card until a dataset is picked.
    expect(screen.queryByText("Column mapping")).toBeNull();
  });

  it("hides the page header when showHeader is false", async () => {
    mockApi(routes());
    renderPage(<Screen showHeader={false} />);
    await screen.findByRole("option", { name: "Line data (1,200 rows)" });
    expect(screen.queryAllByText("Widget analysis")).toHaveLength(0);
  });

  it("auto-maps columns by hint, previews mapped columns, and posts the typed body", async () => {
    const api = mockApi(routes());
    renderPage(<Screen />);
    await screen.findByRole("option", { name: "Line data (1,200 rows)" });

    await userEvent.selectOptions(control("Dataset"), "ds1");
    expect((control("Units") as HTMLSelectElement).value).toBe("units_produced");
    expect((control("Machine (optional)") as HTMLSelectElement).value).toBe("machine");

    // Preview shows only the mapped columns, labelled with field + column.
    const preview = (await screen.findByText("Data preview")).closest("div")!.parentElement!;
    expect(within(preview).getByText("First 2 rows for the columns you mapped", { exact: false })).toBeTruthy();
    expect(within(preview).getByText("Press 2")).toBeTruthy();
    expect(within(preview).queryByText("2026-01-01")).toBeNull();

    await userEvent.type(control("Target rate"), "2.5");
    await userEvent.click(screen.getByRole("button", { name: "Run widget" }));

    expect(await screen.findByText("Total 42 for ds1")).toBeTruthy();
    const post = api.calls.find((c) => c.method === "POST" && c.path === "/analytics/widget")!;
    expect(post.body).toEqual({
      dataset_id: "ds1",
      units_col: "units_produced",
      machine_col: "machine",
      target_rate: 2.5,
      unit: "minutes",
    });
  });

  it("sends null for a blank numeric field, the chosen select option, and omits an unmapped column", async () => {
    const api = mockApi(routes());
    renderPage(<Screen />);
    await screen.findByRole("option", { name: "Line data (1,200 rows)" });
    await userEvent.selectOptions(control("Dataset"), "ds1");
    await userEvent.selectOptions(control("Machine (optional)"), "");
    await userEvent.selectOptions(control("Unit"), "hours");
    await userEvent.click(screen.getByRole("button", { name: "Run widget" }));

    await screen.findByText("Total 42 for ds1");
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      dataset_id: "ds1",
      units_col: "units_produced",
      target_rate: null,
      unit: "hours",
    });
  });

  it("keeps the run button disabled until every required field is mapped", async () => {
    mockApi(routes());
    renderPage(<Screen />);
    await screen.findByRole("option", { name: "Other (5 rows)" });
    await userEvent.selectOptions(control("Dataset"), "ds2");

    const run = screen.getByRole("button", { name: "Run widget" }) as HTMLButtonElement;
    expect((control("Units") as HTMLSelectElement).value).toBe("");
    expect(run.disabled).toBe(true);
    // A dataset with no preview rows shows no preview card.
    await userEvent.selectOptions(control("Units"), "qty");
    expect(run.disabled).toBe(false);
    expect(screen.queryByText("Data preview")).toBeNull();
  });

  it("shows the server's detail when compute fails", async () => {
    mockApi(
      routes({
        "POST /analytics/widget": new Response(JSON.stringify({ detail: "Column units_produced is not numeric" }), { status: 422 }),
      }),
    );
    renderPage(<Screen />);
    await screen.findByRole("option", { name: "Line data (1,200 rows)" });
    await userEvent.selectOptions(control("Dataset"), "ds1");
    await userEvent.click(screen.getByRole("button", { name: "Run widget" }));

    expect(await screen.findByText("Column units_produced is not numeric")).toBeTruthy();
    expect(screen.queryByText(/Total 42/)).toBeNull();
  });

  it("clears a previous result when another dataset is picked", async () => {
    mockApi(routes());
    renderPage(<Screen />);
    await screen.findByRole("option", { name: "Line data (1,200 rows)" });
    await userEvent.selectOptions(control("Dataset"), "ds1");
    await userEvent.click(screen.getByRole("button", { name: "Run widget" }));
    await screen.findByText("Total 42 for ds1");

    await userEvent.selectOptions(control("Dataset"), "ds2");
    expect(screen.queryByText(/Total 42/)).toBeNull();
  });

  it("preselects and auto-maps the dataset from ?dataset_id=", async () => {
    mockApi(routes());
    renderPage(<Screen />, { route: "/analytics/widget?dataset_id=ds1" });
    await waitFor(() => expect((control("Units") as HTMLSelectElement).value).toBe("units_produced"));
    expect((control("Dataset") as HTMLSelectElement).value).toBe("ds1");
    expect((control("Machine (optional)") as HTMLSelectElement).value).toBe("machine");
  });

  it("sends the result to the report inbox with the engine spec", async () => {
    const api = mockApi(routes({ "POST /reports/inbox": { id: "i1" } }));
    renderPage(<Screen />);
    await screen.findByRole("option", { name: "Line data (1,200 rows)" });
    await userEvent.selectOptions(control("Dataset"), "ds1");
    await userEvent.click(screen.getByRole("button", { name: "Run widget" }));
    await userEvent.click(await screen.findByRole("button", { name: "Send to report" }));

    expect(await screen.findByText("Sent to report inbox.")).toBeTruthy();
    const sent = api.calls.find((c) => c.path === "/reports/inbox")!.body as Record<string, unknown>;
    expect(sent).toMatchObject({
      type: "engine",
      engine_path: "/analytics/widget",
      engine_body: { dataset_id: "ds1", units_col: "units_produced" },
    });
    expect(String(sent.label)).toMatch(/^Widget — /);
  });

  it("shows the inbox error when sending to report fails", async () => {
    mockApi(routes({ "POST /reports/inbox": new Response(JSON.stringify({ detail: "Inbox full" }), { status: 400 }) }));
    renderPage(<Screen />);
    await screen.findByRole("option", { name: "Line data (1,200 rows)" });
    await userEvent.selectOptions(control("Dataset"), "ds1");
    await userEvent.click(screen.getByRole("button", { name: "Run widget" }));
    await userEvent.click(await screen.findByRole("button", { name: "Send to report" }));
    expect(await screen.findByText("Inbox full")).toBeTruthy();
  });

  describe("TV mode", () => {
    let requestFullscreen: ReturnType<typeof vi.fn>;
    let exitFullscreen: ReturnType<typeof vi.fn>;
    beforeEach(() => {
      requestFullscreen = vi.fn();
      exitFullscreen = vi.fn();
      HTMLElement.prototype.requestFullscreen = requestFullscreen as never;
      document.exitFullscreen = exitFullscreen as never;
    });
    afterEach(() => {
      Object.defineProperty(document, "fullscreenElement", { configurable: true, value: null });
      vi.useRealTimers();
    });

    it("requests fullscreen on the results and toggles the label on fullscreenchange", async () => {
      mockApi(routes());
      renderPage(<Screen />);
      await screen.findByRole("option", { name: "Line data (1,200 rows)" });
      await userEvent.selectOptions(control("Dataset"), "ds1");
      await userEvent.click(screen.getByRole("button", { name: "Run widget" }));
      await userEvent.click(await screen.findByRole("button", { name: "TV mode" }));
      expect(requestFullscreen).toHaveBeenCalledTimes(1);

      const results = screen.getByText("Total 42 for ds1").parentElement!;
      Object.defineProperty(document, "fullscreenElement", { configurable: true, value: results });
      fireEvent(document, new Event("fullscreenchange"));
      await userEvent.click(await screen.findByRole("button", { name: "Exit TV mode" }));
      expect(exitFullscreen).toHaveBeenCalledTimes(1);
    });

    it("re-posts the last compute every 30s while auto-refresh is on", async () => {
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
      const api = mockApi(routes());
      renderPage(<Screen />);
      await screen.findByRole("option", { name: "Line data (1,200 rows)" });
      await userEvent.selectOptions(control("Dataset"), "ds1");
      await userEvent.click(screen.getByRole("button", { name: "Run widget" }));
      await screen.findByText("Total 42 for ds1");
      const posts = () => api.calls.filter((c) => c.method === "POST" && c.path === "/analytics/widget").length;
      expect(posts()).toBe(1);

      await userEvent.click(screen.getByRole("checkbox"));
      act(() => {
        vi.advanceTimersByTime(30_000);
      });
      await waitFor(() => expect(posts()).toBe(2));

      await userEvent.click(screen.getByRole("checkbox"));
      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      expect(posts()).toBe(2);
    });
  });
});
