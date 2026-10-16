// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Integrations } from "./Integrations";

const TYPES = {
  types: [
    { id: "erp_rest", label: "ERP REST", category: "api" },
    { id: "postgres", label: "PostgreSQL", category: "database" },
    { id: "csv", label: "CSV file", category: "file" },
  ],
};
const REST = {
  id: "i1",
  name: "Odoo",
  type: "erp_rest",
  config: {},
  status: "connected",
  last_synced_at: "2026-09-10T08:00:00Z",
  last_sync_rows: 120,
};
const FILE = { ...REST, id: "i2", name: "Shift sheet", type: "csv", status: "untested", last_synced_at: null, last_sync_rows: null };
const UNKNOWN = { ...REST, id: "i3", name: "Legacy", type: "foo_bar", status: "error" };

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: true, media: query, addEventListener() {}, removeEventListener() {} }));
});

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/integrations/types": TYPES,
    "/integrations": [REST, FILE, UNKNOWN],
    "POST /integrations": { ...REST, id: "i9" },
    "POST /integrations/i1/test": { ok: true, message: "Connected in 120 ms" },
    "POST /integrations/i3/test": { ok: false },
    "POST /integrations/i1/preview": { rows: [{ sku: "A1", qty: 3 }, { sku: "B2", qty: null }] },
    "POST /integrations/i1/sync": { rows_synced: 42, dataset: { name: "Odoo export" } },
    "POST /integrations/i2/upload": { ok: true },
    "DELETE /integrations/i1": new Response(null, { status: 204 }),
    ...overrides,
  });
}

function row(name: string) {
  return screen.getByText(name).closest("tr") as HTMLElement;
}

function dialog(title: string) {
  return screen.getByRole("heading", { name: title }).parentElement!.parentElement as HTMLElement;
}

describe("Integrations", () => {
  it("lists integrations with type labels and last sync", async () => {
    api();
    renderPage(<Integrations />);
    expect(await screen.findByText("Odoo")).toBeTruthy();
    expect(await within(row("Odoo")).findByText("ERP REST")).toBeTruthy();
    expect(within(row("Odoo")).getByText(/\(120 rows\)/)).toBeTruthy();
    expect(within(row("Legacy")).getByText("foo_bar")).toBeTruthy();
    // file integrations get Upload instead of Test
    expect(within(row("Shift sheet")).getByRole("button", { name: "Upload file" })).toBeTruthy();
    expect(within(row("Shift sheet")).queryByRole("button", { name: "Test" })).toBeNull();
    expect(within(row("Shift sheet")).getByText("—")).toBeTruthy();
  });

  it("shows the empty state", async () => {
    api({ "/integrations": [] });
    renderPage(<Integrations />);
    expect(await screen.findByText("No integrations yet")).toBeTruthy();
  });

  it("tests a connection and shows the message, falling back to Failed", async () => {
    api();
    renderPage(<Integrations />);
    await screen.findByText("Odoo");
    await userEvent.click(within(row("Odoo")).getByRole("button", { name: "Test" }));
    expect(await within(row("Odoo")).findByText("Connected in 120 ms")).toBeTruthy();
    await userEvent.click(within(row("Legacy")).getByRole("button", { name: "Test" }));
    expect(await within(row("Legacy")).findByText("Failed")).toBeTruthy();
  });

  it("syncs and shows the row count and dataset, or the sync error", async () => {
    api({
      "POST /integrations/i3/sync": new Response(JSON.stringify({ detail: "Auth expired" }), { status: 403 }),
    });
    renderPage(<Integrations />);
    await screen.findByText("Odoo");
    await userEvent.click(within(row("Odoo")).getByRole("button", { name: "Sync" }));
    expect(await within(row("Odoo")).findByText('Synced 42 rows → dataset "Odoo export"')).toBeTruthy();
    await userEvent.click(within(row("Legacy")).getByRole("button", { name: "Sync" }));
    expect(await within(row("Legacy")).findByText("Auth expired")).toBeTruthy();
  });

  it("previews rows in a dialog", async () => {
    const { calls } = api();
    renderPage(<Integrations />);
    await screen.findByText("Odoo");
    await userEvent.click(within(row("Odoo")).getByRole("button", { name: "Preview" }));
    const d = dialog("Preview — Odoo");
    expect(await within(d).findByText("A1")).toBeTruthy();
    expect(within(d).getByRole("columnheader", { name: "qty" })).toBeTruthy();
    expect(within(d).getByText("3")).toBeTruthy();
    expect(calls.find((c) => c.path === "/integrations/i1/preview")!.search).toBe("?n=10");
    await userEvent.click(within(d).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("heading", { name: "Preview — Odoo" })).toBeNull();
  });

  it("shows the preview error inside the dialog", async () => {
    api({ "POST /integrations/i1/preview": new Response(JSON.stringify({ detail: "Query timed out" }), { status: 504 }) });
    renderPage(<Integrations />);
    await screen.findByText("Odoo");
    await userEvent.click(within(row("Odoo")).getByRole("button", { name: "Preview" }));
    expect(await within(dialog("Preview — Odoo")).findByText("Query timed out")).toBeTruthy();
  });

  it("shows No rows returned for an empty preview", async () => {
    api({ "POST /integrations/i1/preview": { rows: [] } });
    renderPage(<Integrations />);
    await screen.findByText("Odoo");
    await userEvent.click(within(row("Odoo")).getByRole("button", { name: "Preview" }));
    expect(await within(dialog("Preview — Odoo")).findByText("No rows returned.")).toBeTruthy();
  });

  it("uploads a file for a file integration as multipart form data", async () => {
    const { calls } = api();
    const { container } = renderPage(<Integrations />);
    await screen.findByText("Shift sheet");
    await userEvent.click(within(row("Shift sheet")).getByRole("button", { name: "Upload file" }));
    const file = new File(["a,b\n1,2"], "shift.csv", { type: "text/csv" });
    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, file);
    expect(await within(row("Shift sheet")).findByText("File uploaded — ready to sync")).toBeTruthy();
    const body = calls.find((c) => c.path === "/integrations/i2/upload")!.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect((body.get("file") as File).name).toBe("shift.csv");
  });

  it("deletes an integration", async () => {
    const { calls } = api();
    renderPage(<Integrations />);
    await screen.findByText("Odoo");
    await userEvent.click(within(row("Odoo")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/integrations/i1")).toBe(true));
  });

  it("creates an integration with parsed JSON config", async () => {
    const { calls } = api();
    renderPage(<Integrations />);
    await userEvent.click(screen.getByRole("button", { name: "New integration" }));
    const d = dialog("New integration");
    await within(d).findByRole("option", { name: "PostgreSQL (database)" });
    const name = within(d).getByText("Name", { selector: "label" }).parentElement!.querySelector("input")!;
    await userEvent.type(name, "Plant DB");
    await userEvent.selectOptions(within(d).getByRole("combobox"), "postgres");
    expect(within(d).getByText(/"port": 5432/)).toBeTruthy();
    const textarea = d.querySelector("textarea")!;
    await userEvent.clear(textarea);
    await userEvent.type(textarea, '{{"host": "db1", "port": 5432}');
    await userEvent.click(within(d).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "New integration" })).toBeNull());
    expect(calls.find((c) => c.method === "POST" && c.path === "/integrations")!.body).toEqual({
      name: "Plant DB",
      type: "postgres",
      config: { host: "db1", port: 5432 },
    });
  });

  it("rejects invalid JSON config without calling the API, and hides config for file types", async () => {
    const { calls } = api();
    renderPage(<Integrations />);
    await userEvent.click(screen.getByRole("button", { name: "New integration" }));
    const d = dialog("New integration");
    await within(d).findByRole("option", { name: "CSV file (file)" });
    const name = within(d).getByText("Name", { selector: "label" }).parentElement!.querySelector("input")!;
    await userEvent.type(name, "Broken");
    const textarea = d.querySelector("textarea")!;
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "not json");
    await userEvent.click(within(d).getByRole("button", { name: "Create" }));
    expect(await within(d).findByText("Config must be valid JSON")).toBeTruthy();
    expect(calls.some((c) => c.method === "POST" && c.path === "/integrations")).toBe(false);

    await userEvent.selectOptions(within(d).getByRole("combobox"), "csv");
    expect(d.querySelector("textarea")).toBeNull();
    expect(within(d).getByText(/upload a file after creating this integration/)).toBeTruthy();
    await userEvent.click(within(d).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("heading", { name: "New integration" })).toBeNull();
  });

  it("shows the API's create error in the form", async () => {
    api({ "POST /integrations": new Response(JSON.stringify({ detail: "Name already used" }), { status: 409 }) });
    renderPage(<Integrations />);
    await userEvent.click(screen.getByRole("button", { name: "New integration" }));
    const d = dialog("New integration");
    const name = within(d).getByText("Name", { selector: "label" }).parentElement!.querySelector("input")!;
    await userEvent.type(name, "Odoo");
    await userEvent.click(within(d).getByRole("button", { name: "Create" }));
    expect(await within(d).findByText("Name already used")).toBeTruthy();
  });

  it("shows the API's error when a connection test request fails", async () => {
    api({ "POST /integrations/i1/test": new Response(JSON.stringify({ detail: "Host unreachable" }), { status: 502 }) });
    renderPage(<Integrations />);
    await screen.findByText("Odoo");
    await userEvent.click(within(row("Odoo")).getByRole("button", { name: "Test" }));
    expect(await within(row("Odoo")).findByText("Host unreachable")).toBeTruthy();
  });

  it("shows the upload error instead of the success message", async () => {
    api({ "POST /integrations/i2/upload": new Response(JSON.stringify({ detail: "Unsupported file" }), { status: 400 }) });
    const { container } = renderPage(<Integrations />);
    await screen.findByText("Shift sheet");
    await userEvent.click(within(row("Shift sheet")).getByRole("button", { name: "Upload file" }));
    const file = new File(["x"], "shift.csv", { type: "text/csv" });
    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, file);
    expect(await within(row("Shift sheet")).findByText("Unsupported file")).toBeTruthy();
    expect(within(row("Shift sheet")).queryByText("File uploaded — ready to sync")).toBeNull();
  });

  it("shows the delete error and keeps the row", async () => {
    api({ "DELETE /integrations/i1": new Response(JSON.stringify({ detail: "Integration in use" }), { status: 409 }) });
    renderPage(<Integrations />);
    await screen.findByText("Odoo");
    await userEvent.click(within(row("Odoo")).getByRole("button", { name: "Delete" }));
    expect(await within(row("Odoo")).findByText("Integration in use")).toBeTruthy();
  });

  it("falls back to a readable message when the error body isn't JSON", async () => {
    api({ "POST /integrations/i1/sync": new Response("<html>oops</html>", { status: 500 }) });
    renderPage(<Integrations />);
    await screen.findByText("Odoo");
    await userEvent.click(within(row("Odoo")).getByRole("button", { name: "Sync" }));
    expect(await within(row("Odoo")).findByText("Sync failed")).toBeTruthy();
  });
});
