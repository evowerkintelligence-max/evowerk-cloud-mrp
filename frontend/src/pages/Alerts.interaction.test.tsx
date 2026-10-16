// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Alerts } from "./Alerts";

const DATASET = {
  id: "ds1",
  name: "Downtime log",
  original_filename: "downtime.csv",
  fmt: "csv",
  row_count: 500,
  size_bytes: 2048,
  created_at: "2026-09-01T00:00:00Z",
  columns: ["ts", "machine", "minutes", "good", "total", "scrap", "qty", "item", "actual", "target"].map((name) => ({
    name,
    dtype: "string",
  })),
};

const RULE = {
  id: "r1",
  dataset_id: "ds1",
  name: "Line 2 downtime",
  type: "machine_downtime",
  severity: "critical",
  enabled: true,
  config: { duration_col: "minutes", machine_col: "machine", threshold: 45, time_col: null },
};

const TRIGGER = {
  rule_id: "r1",
  rule_name: "Line 2 downtime",
  type: "machine_downtime",
  severity: "critical",
  triggered: true,
  value: 90,
  threshold: 45,
  message: "M2 was down 90 min",
  offenders: ["M2", "M5"],
  evaluated_at: "2026-09-10T08:00:00Z",
};

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: true, media: query, addEventListener() {}, removeEventListener() {} }));
});

function control(label: string, tag = "select") {
  return screen.getByText(label, { selector: "label" }).parentElement!.querySelector(tag) as HTMLElement;
}

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/alerts/rules": [RULE],
    "/datasets": [DATASET],
    "/sync-schedules": { schedules: [{ schedule_id: "sch1", name: "MES hourly" }] },
    "POST /alerts/rules": { id: "r2" },
    "PUT /alerts/rules/r1": RULE,
    "DELETE /alerts/rules/r1": new Response(null, { status: 204 }),
    "POST /alerts/scan": { results: [TRIGGER], triggered_count: 1, evaluated_count: 1 },
    "POST /alerts/scan/r1": { ...TRIGGER, triggered: false, message: "All clear", offenders: [] },
    ...overrides,
  });
}

async function newRule(type: string) {
  await userEvent.click(screen.getByRole("button", { name: "+ New Rule" }));
  await userEvent.type(control("Name", "input"), "My rule");
  await userEvent.selectOptions(control("Type"), type);
}

describe("Alerts", () => {
  it("lists rules with dataset names, and the empty state", async () => {
    api();
    renderPage(<Alerts />);
    expect(await screen.findByText("Line 2 downtime")).toBeTruthy();
    expect(await screen.findByText("Downtime log")).toBeTruthy();
    expect(screen.getByText("Machine Downtime")).toBeTruthy();
  });

  it("shows the empty rules message", async () => {
    api({ "/alerts/rules": [] });
    renderPage(<Alerts />);
    expect(await screen.findByText("No alert rules yet. Click + New Rule to add one.")).toBeTruthy();
  });

  it("scans all rules and shows triggered results with offenders", async () => {
    api();
    renderPage(<Alerts />);
    await userEvent.click(screen.getByRole("button", { name: "Scan All" }));
    expect(await screen.findByText("1 triggered / 1 evaluated")).toBeTruthy();
    expect(screen.getByText("M2 was down 90 min")).toBeTruthy();
    expect(screen.getByText("M2 · M5")).toBeTruthy();
  });

  it("shows the no-rules scan result", async () => {
    api({ "POST /alerts/scan": { results: [], triggered_count: 0, evaluated_count: 0 } });
    renderPage(<Alerts />);
    await userEvent.click(screen.getByRole("button", { name: "Scan All" }));
    expect(await screen.findByText("No enabled rules evaluated.")).toBeTruthy();
    expect(screen.getByText("Enable at least one rule and click Scan All.")).toBeTruthy();
  });

  it("tests one rule and replaces its earlier scan result", async () => {
    const { calls } = api();
    renderPage(<Alerts />);
    await userEvent.click(screen.getByRole("button", { name: "Scan All" }));
    await screen.findByText("M2 was down 90 min");
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText("All clear")).toBeTruthy();
    expect(screen.queryByText("M2 was down 90 min")).toBeNull();
    expect(screen.getByText("0 triggered / 1 evaluated")).toBeTruthy();
    expect(calls.some((c) => c.method === "POST" && c.path === "/alerts/scan/r1")).toBe(true);
  });

  it("toggles enabled and deletes a rule", async () => {
    const { calls } = api();
    renderPage(<Alerts />);
    await screen.findByText("Line 2 downtime");
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/alerts/rules/r1")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ enabled: false });
  });

  it("validates name/type and dataset before saving", async () => {
    const { calls } = api();
    renderPage(<Alerts />);
    await userEvent.click(screen.getByRole("button", { name: "+ New Rule" }));
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    expect(await screen.findByText("Fill in Name and Type.")).toBeTruthy();
    await userEvent.type(control("Name", "input"), "x");
    await userEvent.selectOptions(control("Type"), "scrap");
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    expect(await screen.findByText("Choose a dataset.")).toBeTruthy();
    expect(calls.some((c) => c.path === "/alerts/rules" && c.method === "POST")).toBe(false);
  });

  it("creates a machine downtime rule with columns, threshold, lookback and webhook", async () => {
    const { calls } = api({ "/alerts/rules": [] });
    renderPage(<Alerts />);
    await screen.findByText(/No alert rules yet/);
    await newRule("machine_downtime");
    await screen.findByRole("option", { name: "Downtime log" });
    await userEvent.selectOptions(control("Dataset"), "ds1");
    await userEvent.selectOptions(control("Severity"), "critical");
    await userEvent.selectOptions(control("Duration Column *"), "minutes");
    await userEvent.selectOptions(control("Machine Column (optional)"), "machine");
    await userEvent.type(control("Threshold (minutes) *", "input"), "30");
    await userEvent.selectOptions(control("Timestamp Column (optional — limits data window)"), "ts");
    await userEvent.type(control("Lookback (days)", "input"), "7");
    await userEvent.type(control("Andon / relay webhook URL (optional)", "input"), "https://andon.local/hook");
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/alerts/rules")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/alerts/rules")!.body).toEqual({
      name: "My rule",
      type: "machine_downtime",
      severity: "critical",
      dataset_id: "ds1",
      config: {
        duration_col: "minutes",
        machine_col: "machine",
        threshold: 30,
        time_col: "ts",
        lookback_days: 7,
        webhook_url: "https://andon.local/hook",
      },
    });
    await waitFor(() => expect(screen.queryByText("New alert rule")).toBeNull());
  }, 15_000); // long typing sequence; slow under a loaded parallel run

  it("creates a stream metric rule against a scheduled sync with no dataset", async () => {
    const { calls } = api({ "/alerts/rules": [] });
    renderPage(<Alerts />);
    await newRule("stream_metric");
    expect(screen.queryByText("Dataset", { selector: "label" })).toBeNull();
    await screen.findByRole("option", { name: "MES hourly" });
    await userEvent.selectOptions(control("Scheduled sync"), "sch1");
    await userEvent.type(control("Metric Name *", "input"), "oee_pct");
    await userEvent.selectOptions(control("Comparison"), "lt");
    await userEvent.type(control("Threshold *", "input"), "65");
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/alerts/rules")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/alerts/rules")!.body).toEqual({
      name: "My rule",
      type: "stream_metric",
      severity: "warning",
      dataset_id: null,
      config: { schedule_id: "sch1", metric_name: "oee_pct", comparison: "lt", threshold: 65 },
    });
  });

  it.each([
    ["quality", { good_units_col: "", total_units_col: "", defect_threshold_pct: 5 }],
    ["scrap", { scrap_col: "", total_units_col: "", scrap_threshold_pct: 5 }],
    ["inventory", { quantity_col: "", min_threshold: 100 }],
    ["production_target", { actual_col: "", miss_threshold_pct: 5 }],
  ])("defaults the %s thresholds when left blank", async (type, config) => {
    const { calls } = api({ "/alerts/rules": [] });
    renderPage(<Alerts />);
    await newRule(type);
    await screen.findByRole("option", { name: "Downtime log" });
    await userEvent.selectOptions(control("Dataset"), "ds1");
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/alerts/rules")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/alerts/rules")!.body).toMatchObject({ type, config });
  });

  it("fills type-specific fields for a production target rule", async () => {
    const { calls } = api({ "/alerts/rules": [] });
    renderPage(<Alerts />);
    await newRule("production_target");
    await screen.findByRole("option", { name: "Downtime log" });
    await userEvent.selectOptions(control("Dataset"), "ds1");
    await userEvent.selectOptions(control("Actual Output Column *"), "actual");
    await userEvent.selectOptions(control("Target Column (or use flat value)"), "target");
    await userEvent.type(control("Flat Target Value", "input"), "500");
    await userEvent.selectOptions(control("Group Column (optional)"), "machine");
    await userEvent.type(control("Miss Threshold % *", "input"), "10");
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/alerts/rules")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/alerts/rules")!.body).toMatchObject({
      config: { actual_col: "actual", target_col: "target", target_value: 500, group_col: "machine", miss_threshold_pct: 10 },
    });
  });

  it("edits an existing rule with its config prefilled and the type locked", async () => {
    const { calls } = api();
    renderPage(<Alerts />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByText("Edit rule")).toBeTruthy();
    expect((control("Type") as HTMLSelectElement).disabled).toBe(true);
    expect((control("Name", "input") as HTMLInputElement).value).toBe("Line 2 downtime");
    expect((control("Threshold (minutes) *", "input") as HTMLInputElement).value).toBe("45");
    await waitFor(() => expect((control("Duration Column *") as HTMLSelectElement).value).toBe("minutes"));
    await userEvent.click(screen.getByRole("button", { name: "Update rule" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({
      name: "Line 2 downtime",
      type: "machine_downtime",
      dataset_id: "ds1",
      config: { duration_col: "minutes", machine_col: "machine", threshold: 45 },
    });
  });

  it("shows a save error from the API and cancels the form", async () => {
    api({ "/alerts/rules": [], "POST /alerts/rules": new Response(JSON.stringify({ detail: "Duplicate name" }), { status: 409 }) });
    renderPage(<Alerts />);
    await newRule("inventory");
    await screen.findByRole("option", { name: "Downtime log" });
    await userEvent.selectOptions(control("Dataset"), "ds1");
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    expect(await screen.findByText("Duplicate name")).toBeTruthy();
    const form = screen.getByText("New alert rule").closest("div")!.parentElement!;
    await userEvent.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("New alert rule")).toBeNull();
  });

  it("shows the toggle error and leaves the checkbox as it was", async () => {
    api({ "PUT /alerts/rules/r1": new Response(JSON.stringify({ detail: "Rule is read-only" }), { status: 403 }) });
    renderPage(<Alerts />);
    await screen.findByText("Line 2 downtime");
    await userEvent.click(screen.getByRole("checkbox"));
    expect(await screen.findByText("Rule is read-only")).toBeTruthy();
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
  });

  it("shows the delete error, falling back when the body isn't JSON, and keeps the row", async () => {
    api({ "DELETE /alerts/rules/r1": new Response("<html>oops</html>", { status: 500 }) });
    renderPage(<Alerts />);
    await screen.findByText("Line 2 downtime");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Request failed")).toBeTruthy();
    expect(screen.getByText("Line 2 downtime")).toBeTruthy();
  });
});
