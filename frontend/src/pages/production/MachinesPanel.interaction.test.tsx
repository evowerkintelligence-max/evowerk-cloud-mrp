// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { MachinesPanel } from "./MachinesPanel";

const WC = {
  id: "wc1",
  code: "CNC",
  name: "CNC cell",
  status: "active",
  machine_capacity: 2,
  default_cycle_time_seconds: null,
  default_mtbf_hours: null,
  default_mttr_hours: null,
};
const MACHINE = {
  id: "m1",
  work_center_id: "wc1",
  code: "M-01",
  name: "Haas VF2",
  status: "active",
  mtbf_hours: 120,
  mttr_hours: null,
  needs_review: true,
};

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/work-centers": [WC],
    "/machines": [MACHINE, { ...MACHINE, id: "m2", code: "M-02", name: "Old lathe", work_center_id: "gone", mtbf_hours: null, needs_review: false }],
    "POST /machines": { ...MACHINE, id: "m3" },
    "/machines/m1/checkin-qr": new Response(new Blob(["png"], { type: "image/png" }), { status: 200 }),
    ...overrides,
  });
}

const createObjectURL = URL.createObjectURL;
const revokeObjectURL = URL.revokeObjectURL;
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:qr");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  URL.createObjectURL = createObjectURL;
  URL.revokeObjectURL = revokeObjectURL;
});

function fakeTab() {
  const tab = { document: document.implementation.createHTMLDocument(""), close: vi.fn() };
  vi.stubGlobal("open", vi.fn(() => tab));
  return tab;
}

describe("MachinesPanel", () => {
  it("lists machines with work center labels and review badges", async () => {
    api();
    renderPage(<MachinesPanel />);
    expect(await screen.findByText("Haas VF2", { selector: "td" })).toBeTruthy();
    expect(await screen.findByText("CNC - CNC cell", { selector: "td" })).toBeTruthy();
    // unknown work center falls back to the raw id
    expect(screen.getByText("gone")).toBeTruthy();
    expect(screen.getByText("Needs review")).toBeTruthy();
    expect(screen.getByText("120")).toBeTruthy();
  });

  it("asks for a work center first, and shows the empty machines state", async () => {
    api({ "/work-centers": [], "/machines": [] });
    renderPage(<MachinesPanel />);
    expect(await screen.findByText("Add a work center first - every machine belongs to one.")).toBeTruthy();
    expect(screen.getByText("No machines yet")).toBeTruthy();
  });

  it("creates a machine and sends blank MTTR as null", async () => {
    const { calls } = api();
    renderPage(<MachinesPanel />);
    await screen.findByRole("option", { name: "CNC - CNC cell" });
    await userEvent.selectOptions(screen.getByLabelText("Work center"), "wc1");
    await userEvent.type(screen.getByLabelText("Code"), "M-03");
    await userEvent.type(screen.getByLabelText("Name"), "Mazak");
    await userEvent.type(screen.getByLabelText("MTBF (hours)"), "200");
    await userEvent.click(screen.getByRole("button", { name: "Add machine" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      work_center_id: "wc1",
      code: "M-03",
      name: "Mazak",
      mtbf_hours: 200,
      mttr_hours: null,
    });
    await waitFor(() => expect((screen.getByLabelText("Code") as HTMLInputElement).value).toBe(""));
  });

  it("shows a create error", async () => {
    api({ "POST /machines": new Response(JSON.stringify({ detail: "Code already used" }), { status: 409 }) });
    renderPage(<MachinesPanel />);
    await screen.findByRole("option", { name: "CNC - CNC cell" });
    await userEvent.selectOptions(screen.getByLabelText("Work center"), "wc1");
    await userEvent.type(screen.getByLabelText("Code"), "M-01");
    await userEvent.type(screen.getByLabelText("Name"), "Dup");
    await userEvent.click(screen.getByRole("button", { name: "Add machine" }));
    expect(await screen.findByText("Code already used")).toBeTruthy();
  });

  it("prints a labelled QR page into a new tab", async () => {
    api();
    const tab = fakeTab();
    renderPage(<MachinesPanel />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Print QR" }))[0]);
    await waitFor(() => expect(tab.document.querySelector("img")).not.toBeNull());
    expect(tab.document.title).toBe("Check-in QR - M-01");
    expect(tab.document.querySelector("h1")!.textContent).toBe("M-01 - Haas VF2");
    expect(tab.document.querySelector("img")!.getAttribute("src")).toBe("blob:qr");
    expect(tab.close).not.toHaveBeenCalled();
  });

  it("reports a blocked pop-up without requesting the QR", async () => {
    const { calls } = api();
    vi.stubGlobal("open", vi.fn(() => null));
    renderPage(<MachinesPanel />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Print QR" }))[0]);
    expect(await screen.findByText(/Your browser blocked the print tab/)).toBeTruthy();
    expect(calls.some((c) => c.path.endsWith("/checkin-qr"))).toBe(false);
  });

  it("closes the tab and reports when the QR fails to load", async () => {
    api({ "/machines/m1/checkin-qr": new Response("{}", { status: 500 }) });
    const tab = fakeTab();
    renderPage(<MachinesPanel />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Print QR" }))[0]);
    expect(await screen.findByText("Failed to load the check-in QR for M-01")).toBeTruthy();
    expect(tab.close).toHaveBeenCalled();
  });

  it("closes the tab and reports when the request throws", async () => {
    api({
      "/machines/m1/checkin-qr": () => {
        throw new Error("network down");
      },
    });
    const tab = fakeTab();
    renderPage(<MachinesPanel />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Print QR" }))[0]);
    expect(await screen.findByText("Failed to load the check-in QR for M-01")).toBeTruthy();
    expect(tab.close).toHaveBeenCalled();
  });
});
