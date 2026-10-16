// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { ReportBuilder } from "./ReportBuilder";

/** Fields here use un-associated labels; find the control next to the label. */
function field(label: string, root: HTMLElement = document.body) {
  return within(root)
    .getByText(label, { selector: "label" })
    .parentElement!.querySelector<HTMLInputElement>("input, select, textarea")!;
}

function dialog(title: string) {
  return screen.getByRole("heading", { name: title }).parentElement!.parentElement!;
}

const ME = { id: "o1", name: "Org", role: "member", user_id: "u1", enabled_features: [] };
const REPORT = {
  id: "r1",
  name: "Weekly ops",
  sections: [
    { type: "text", title: "Intro", text: "Hello world" },
    { type: "findings", title: "Findings", items: ["a", "b"] },
  ],
  updated_at: "2026-03-01T09:00:00Z",
};

function base(extra: Record<string, unknown> = {}) {
  return mockApi({
    "/datasets": [{ id: "d1", name: "Orders", original_filename: "o.csv", fmt: "csv", row_count: 1, size_bytes: 1, created_at: "", columns: [] }],
    "/bi/charts": [{ id: "c1", name: "Throughput by line" }],
    "/reports/inbox": [],
    "/orgs/me": ME,
    "/reports/custom": [REPORT],
    ...extra,
  });
}

/** Section rows in the Sections card, as [type badge, title] pairs. */
function sectionRows() {
  const card = screen.getByText("Sections").closest("div")!.parentElement!;
  return Array.from(card.querySelectorAll("div.rounded-md.border")).map((row) => {
    const spans = row.querySelectorAll("span");
    return `${spans[0].textContent}:${spans[1].textContent}`;
  });
}

async function addSection(fill: (d: HTMLElement) => Promise<void>) {
  await userEvent.click(screen.getByRole("button", { name: "+ Add section" }));
  const d = dialog("Add section");
  await fill(d);
  await userEvent.click(within(d).getByRole("button", { name: "Save section" }));
}

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => vi.restoreAllMocks());

describe("ReportBuilder", () => {
  it("shows the empty state with no saved reports", async () => {
    base({ "/reports/custom": [] });
    renderPage(<ReportBuilder />);
    expect(await screen.findByText("No saved reports yet")).toBeTruthy();
  });

  it("lists saved reports and deletes one", async () => {
    const api = base({
      "/reports/custom": [REPORT, { ...REPORT, id: "r2", name: "", sections: [] }],
      "DELETE /reports/custom/r2": new Response(null, { status: 204 }),
    });
    renderPage(<ReportBuilder />);
    const row = (await screen.findByRole("button", { name: "Untitled report" })).closest("tr")!;
    expect(within(row).getByText("0")).toBeTruthy();
    expect(within(screen.getByRole("button", { name: "Weekly ops" }).closest("tr")!).getByText("2")).toBeTruthy();
    await userEvent.click(within(row).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/reports/custom/r2")).toBe(true));
  });

  it("builds a new report with every section kind and saves it", async () => {
    const api = base({ "POST /reports/custom": { ...REPORT, id: "r9", name: "Monthly", sections: [] } });
    renderPage(<ReportBuilder />);
    await userEvent.click(await screen.findByRole("button", { name: "New report" }));
    expect(screen.getByText("No sections yet.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
    await userEvent.type(screen.getByPlaceholderText("e.g. Weekly ops summary"), "Monthly");

    await addSection(async (d) => {
      await userEvent.type(field("Title", d), "Intro");
      await userEvent.type(field("Text", d), "Plant ran well.");
    });
    await addSection(async (d) => {
      await userEvent.selectOptions(field("Type", d), "risks");
      await userEvent.type(field("Items", d), "Supplier delay{Enter}{Enter}  Power cut  ");
    });
    await addSection(async (d) => {
      await userEvent.selectOptions(field("Type", d), "table");
      await userEvent.selectOptions(field("Dataset", d), "d1");
      const q = field("Query (JSON)", d);
      await userEvent.clear(q);
      await userEvent.type(q, "{{bad");
      expect(within(d).getByText(/^Invalid JSON:/)).toBeTruthy();
      expect(within(d).getByRole("button", { name: "Save section" }).hasAttribute("disabled")).toBe(true);
      fireEvent.change(q, { target: { value: '{"select":[{"column":"sku"}]}' } });
    });
    await addSection(async (d) => {
      await userEvent.selectOptions(field("Type", d), "bi_chart");
      await userEvent.selectOptions(field("Chart", d), "c1");
    });
    await addSection(async (d) => {
      await userEvent.selectOptions(field("Type", d), "title");
      await userEvent.type(field("Title", d), "Cover");
    });

    expect(sectionRows()).toEqual(["text:Intro", "risks:Untitled", "table:Untitled", "bi_chart:Untitled", "title:Cover"]);
    expect(screen.getByText("Plant ran well.")).toBeTruthy();
    expect(screen.getByText("2 item(s)")).toBeTruthy();
    expect(screen.getByText("dataset d1")).toBeTruthy();
    expect(screen.getByText("chart c1")).toBeTruthy();

    // move the cover to the top, then drop the chart
    const rowButtons = (label: string) => screen.getAllByRole("button", { name: label });
    expect(rowButtons("Up")[0].hasAttribute("disabled")).toBe(true);
    expect(rowButtons("Down")[4].hasAttribute("disabled")).toBe(true);
    for (const i of [4, 3, 2, 1]) await userEvent.click(rowButtons("Up")[i]);
    await userEvent.click(rowButtons("Down")[1]);
    await userEvent.click(rowButtons("Up")[2]);
    await userEvent.click(rowButtons("Remove")[4]);
    expect(sectionRows()).toEqual(["title:Cover", "text:Intro", "risks:Untitled", "table:Untitled"]);

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    // after the first save the page switches to the saved report's toolbar
    expect(await screen.findByRole("button", { name: "Export PDF" })).toBeTruthy();
    expect(api.calls.find((c) => c.method === "POST" && c.path === "/reports/custom")!.body).toEqual({
      name: "Monthly",
      sections: [
        { type: "title", title: "Cover", text: "" },
        { type: "text", title: "Intro", text: "Plant ran well." },
        { type: "risks", items: ["Supplier delay", "Power cut"] },
        { type: "table", dataset_id: "d1", query: { select: [{ column: "sku" }] } },
      ],
    });
  }, 20_000);

  it("cancelling the section dialog adds nothing", async () => {
    base();
    renderPage(<ReportBuilder />);
    await userEvent.click(await screen.findByRole("button", { name: "New report" }));
    await userEvent.click(screen.getByRole("button", { name: "+ Add section" }));
    await userEvent.click(within(dialog("Add section")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("heading", { name: "Add section" })).toBeNull();
    expect(screen.getByText("No sections yet.")).toBeTruthy();
  });

  it("edits an existing report's section and saves with PUT", async () => {
    const api = base({ "PUT /reports/custom/r1": REPORT });
    renderPage(<ReportBuilder />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByRole("heading", { name: "Weekly ops" })).toBeTruthy();
    expect(sectionRows()).toEqual(["text:Intro", "findings:Findings"]);

    await userEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]);
    const d = dialog("Edit section");
    expect(field("Items", d).value).toBe("a\nb");
    await userEvent.type(field("Items", d), "{Enter}c");
    await userEvent.click(within(d).getByRole("button", { name: "Save section" }));
    expect(screen.getByText("3 item(s)")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PUT")).toBe(true));
    expect(api.calls.find((c) => c.method === "PUT")!.body).toEqual({
      name: "Weekly ops",
      sections: [REPORT.sections[0], { type: "findings", title: "Findings", items: ["a", "b", "c"] }],
    });

    await userEvent.click(screen.getByRole("button", { name: "Back to list" }));
    expect(await screen.findByRole("button", { name: "New report" })).toBeTruthy();
  });

  it("shows the save error", async () => {
    base({ "POST /reports/custom": new Response(JSON.stringify({ detail: "Name taken" }), { status: 409 }) });
    renderPage(<ReportBuilder />);
    await userEvent.click(await screen.findByRole("button", { name: "New report" }));
    await userEvent.type(screen.getByPlaceholderText("e.g. Weekly ops summary"), "Dup");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Name taken")).toBeTruthy();
  });

  it("narrates in the chosen style and turns the output into sections", async () => {
    const api = base({
      "POST /reports/custom/r1/narrate": {
        executive_summary: "Output rose 4%.",
        sections: [{ title: "Line 2", narrative: "Line 2 led." }],
        error: "llm timeout",
      },
    });
    renderPage(<ReportBuilder />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.selectOptions(screen.getByDisplayValue("Paragraph"), "brief");
    await userEvent.click(screen.getByRole("button", { name: "Narrate" }));

    expect(await screen.findByText("AI narrative (brief)")).toBeTruthy();
    expect(screen.getByText(/AI narration was unavailable/)).toBeTruthy();
    expect(screen.getByText("Output rose 4%.")).toBeTruthy();
    expect(api.calls.find((c) => c.path.endsWith("/narrate"))!.search).toBe("?style=brief");

    await userEvent.click(screen.getByRole("button", { name: "Use as Exec Summary section" }));
    await userEvent.click(screen.getByRole("button", { name: "Add as text section" }));
    // a second "use" replaces the existing exec summary instead of adding another
    await userEvent.click(screen.getByRole("button", { name: "Use as Exec Summary section" }));
    expect(sectionRows()).toEqual([
      "exec_summary:Executive Summary",
      "text:Intro",
      "findings:Findings",
      "text:Line 2",
    ]);
  });

  it("drops the narrative when switching to another report or a new one", async () => {
    base({
      "/reports/custom": [REPORT, { ...REPORT, id: "r2", name: "Daily ops" }],
      "POST /reports/custom/r1/narrate": { executive_summary: "Output rose 4%.", sections: [] },
    });
    renderPage(<ReportBuilder />);
    await userEvent.click(await screen.findByRole("button", { name: "Weekly ops" }));
    await userEvent.click(screen.getByRole("button", { name: "Narrate" }));
    expect(await screen.findByText("Output rose 4%.")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Back to list" }));
    await userEvent.click(await screen.findByRole("button", { name: "Daily ops" }));
    expect(screen.getByRole("heading", { name: "Daily ops" })).toBeTruthy();
    expect(screen.queryByText("Output rose 4%.")).toBeNull();
    expect(screen.queryByRole("button", { name: "Use as Exec Summary section" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Back to list" }));
    await userEvent.click(await screen.findByRole("button", { name: "Weekly ops" }));
    await userEvent.click(screen.getByRole("button", { name: "Narrate" }));
    expect(await screen.findByText("Output rose 4%.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Back to list" }));
    await userEvent.click(await screen.findByRole("button", { name: "New report" }));
    expect(screen.queryByText("Output rose 4%.")).toBeNull();
  });

  it("shows the narration error", async () => {
    base({ "POST /reports/custom/r1/narrate": new Response(JSON.stringify({ detail: "No data" }), { status: 400 }) });
    renderPage(<ReportBuilder />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Narrate" }));
    expect(await screen.findByText("No data")).toBeTruthy();
  });

  it("says when the inbox is empty", async () => {
    base();
    renderPage(<ReportBuilder />);
    await userEvent.click(await screen.findByRole("button", { name: "New report" }));
    await userEvent.click(screen.getByRole("button", { name: "Add from inbox" }));
    expect(await screen.findByText(/Nothing captured yet/)).toBeTruthy();
    await userEvent.click(within(dialog("Add from inbox")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("heading", { name: "Add from inbox" })).toBeNull();
  });

  it("adds checked inbox items as sections and only lets the author delete theirs", async () => {
    const inbox = [
      { id: "x1", label: "OEE snapshot", spec: { type: "text", text: "OEE 71%" }, created_by: "u1", created_at: "2026-03-01T00:00:00Z" },
      { id: "x2", label: "Scrap", spec: { type: "findings", title: "Scrap notes", items: ["x"] }, created_by: "u2", created_at: "2026-03-01T00:00:00Z" },
      { id: "x3", label: "Unused", spec: { type: "text", text: "" }, created_by: "u2", created_at: "2026-03-01T00:00:00Z" },
    ];
    const api = base({
      "/reports/inbox": inbox,
      "DELETE /reports/inbox/x1": new Response(JSON.stringify({ detail: "Already consumed" }), { status: 409 }),
    });
    renderPage(<ReportBuilder />);
    await userEvent.click(await screen.findByRole("button", { name: "New report" }));
    await userEvent.click(screen.getByRole("button", { name: "Add from inbox" }));
    const d = dialog("Add from inbox");
    await within(d).findByText("OEE snapshot");
    // member u1 sees Delete only on the item they created
    expect(within(d).getAllByRole("button", { name: "Delete" })).toHaveLength(1);

    const [c1, c2, c3] = within(d).getAllByRole("checkbox");
    expect(within(d).getByRole("button", { name: /selected/ }).hasAttribute("disabled")).toBe(true);
    await userEvent.click(c1);
    await userEvent.click(c2);
    await userEvent.click(c3);
    await userEvent.click(c3);
    await userEvent.click(within(d).getByRole("button", { name: "Delete" }));
    expect(await within(d).findByText("Already consumed")).toBeTruthy();
    expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/reports/inbox/x1")).toBe(true);

    await userEvent.click(within(d).getByRole("button", { name: "Add 2 selected" }));
    expect(screen.queryByRole("heading", { name: "Add from inbox" })).toBeNull();
    // the label stands in for a missing spec title
    expect(sectionRows()).toEqual(["text:OEE snapshot", "findings:Scrap notes"]);
  });

  it("lets an owner delete anyone's inbox item", async () => {
    const api = base({
      "/orgs/me": { ...ME, role: "owner" },
      "/reports/inbox": [{ id: "x2", label: "Scrap", spec: { type: "text" }, created_by: "u2", created_at: "2026-03-01T00:00:00Z" }],
      "DELETE /reports/inbox/x2": new Response(null, { status: 204 }),
    });
    renderPage(<ReportBuilder />);
    await userEvent.click(await screen.findByRole("button", { name: "New report" }));
    await userEvent.click(screen.getByRole("button", { name: "Add from inbox" }));
    await userEvent.click(await within(dialog("Add from inbox")).findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/reports/inbox/x2")).toBe(true));
  });

  it("exports a saved report as a named file and reports render failures", async () => {
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:r"), revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const api = base({
      "/reports/custom/r1/render": ({ url }: { url: URL }) =>
        url.searchParams.get("format") === "pdf" ? new Response("err", { status: 500 }) : new Response("a,b", { status: 200 }),
    });
    renderPage(<ReportBuilder />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect((click.mock.contexts[0] as HTMLAnchorElement).download).toBe("Weekly ops.csv");

    await userEvent.click(screen.getByRole("button", { name: "Export XLSX" }));
    await userEvent.click(screen.getByRole("button", { name: "Export HTML" }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(3));
    expect(click.mock.contexts.map((a) => (a as HTMLAnchorElement).download)).toEqual([
      "Weekly ops.csv",
      "Weekly ops.xlsx",
      "Weekly ops.html",
    ]);

    await userEvent.click(screen.getByRole("button", { name: "Export PDF" }));
    expect(await screen.findByText("Render failed — save the report first.")).toBeTruthy();
    expect(api.calls.filter((c) => c.path === "/reports/custom/r1/render").map((c) => c.search)).toEqual([
      "?format=csv",
      "?format=xlsx",
      "?format=html",
      "?format=pdf",
    ]);
  });

  it("opens the rendered report in a new tab, closing it if rendering fails", async () => {
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:view"), revokeObjectURL: vi.fn() });
    const tab = { location: { href: "" }, close: vi.fn() };
    const open = vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    let fail = false;
    base({
      "/reports/custom/r1/render": () => (fail ? new Response("x", { status: 500 }) : new Response("<html></html>", { status: 200 })),
    });
    renderPage(<ReportBuilder />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "View" }));
    await waitFor(() => expect(tab.location.href).toBe("blob:view"));
    expect(open).toHaveBeenCalledWith("", "_blank");

    fail = true;
    await userEvent.click(screen.getByRole("button", { name: "View" }));
    expect(await screen.findByText("Render failed — save the report first.")).toBeTruthy();
    expect(tab.close).toHaveBeenCalled();
  });
});
