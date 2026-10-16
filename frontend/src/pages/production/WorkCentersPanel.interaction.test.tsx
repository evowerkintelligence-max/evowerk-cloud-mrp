// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { WorkCentersPanel } from "./WorkCentersPanel";

const WC = {
  id: "wc1",
  code: "CNC",
  name: "CNC cell",
  status: "active",
  machine_capacity: 2,
  default_cycle_time_seconds: 42.5,
  default_mtbf_hours: null,
  default_mttr_hours: 1.5,
};

describe("WorkCentersPanel", () => {
  it("lists work centers with dashes for missing defaults", async () => {
    mockApi({ "/work-centers": [WC] });
    renderPage(<WorkCentersPanel />);
    expect(await screen.findByText("CNC cell")).toBeTruthy();
    expect(screen.getByText("42.5")).toBeTruthy();
    expect(screen.getByText("1.5")).toBeTruthy();
    expect(screen.getByText("-")).toBeTruthy();
  });

  it("shows the empty state", async () => {
    mockApi({ "/work-centers": [] });
    renderPage(<WorkCentersPanel />);
    expect(await screen.findByText("No work centers yet")).toBeTruthy();
  });

  it("creates a work center with optional numbers as null when blank, then resets the form", async () => {
    const { calls } = mockApi({ "/work-centers": [], "POST /work-centers": WC });
    renderPage(<WorkCentersPanel />);
    await userEvent.type(screen.getByLabelText("Code"), "ASM");
    await userEvent.type(screen.getByLabelText("Name"), "Assembly");
    const cap = screen.getByLabelText("Machines in parallel");
    await userEvent.clear(cap);
    await userEvent.type(cap, "3");
    await userEvent.type(screen.getByLabelText("Cycle time (s)"), "30");
    await userEvent.type(screen.getByLabelText("MTTR (hours)"), "2");
    await userEvent.click(screen.getByRole("button", { name: "Add work center" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      code: "ASM",
      name: "Assembly",
      machine_capacity: 3,
      default_cycle_time_seconds: 30,
      default_mtbf_hours: null,
      default_mttr_hours: 2,
    });
    await waitFor(() => expect((screen.getByLabelText("Machines in parallel") as HTMLInputElement).value).toBe("1"));
    expect((screen.getByLabelText("Code") as HTMLInputElement).value).toBe("");
  });

  it("shows a create error", async () => {
    mockApi({
      "/work-centers": [],
      "POST /work-centers": new Response(JSON.stringify({ detail: "Code taken" }), { status: 409 }),
    });
    renderPage(<WorkCentersPanel />);
    await userEvent.type(screen.getByLabelText("Code"), "CNC");
    await userEvent.type(screen.getByLabelText("Name"), "Dup");
    await userEvent.click(screen.getByRole("button", { name: "Add work center" }));
    expect(await screen.findByText("Code taken")).toBeTruthy();
  });
});
