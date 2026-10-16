// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { CheckIn } from "./CheckIn";

const ROSTER = {
  machine_id: "m1",
  machine_name: "Haas VF2",
  operators: [
    { id: "op1", name: "Ana" },
    { id: "op2", name: "Ben" },
  ],
};
const KEY = "evowerk:checkin:m1";

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: true, media: query, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => localStorage.clear());

function render() {
  return renderPage(
    <Routes>
      <Route path="/checkin/:machineId" element={<CheckIn />} />
    </Routes>,
    { route: "/checkin/m1" },
  );
}

describe("CheckIn", () => {
  it("checks in from the roster and remembers the operator", async () => {
    const { calls } = mockApi({
      "/checkin/m1": ROSTER,
      "POST /checkin/m1": { operator_id: "op2", operator_name: "Ben" },
    });
    render();
    expect(await screen.findByText("Check in at Haas VF2")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Ben" }));
    expect(await screen.findByText("Checked in as Ben. You're all set.")).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ operator_id: "op2" });
    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({ id: "op2", name: "Ben" });

    await userEvent.click(screen.getByRole("button", { name: "Not you? Check in again" }));
    // "Not you?" goes back to the roster, not the remembered shortcut
    expect(screen.getByRole("button", { name: "Ana" })).toBeTruthy();
    expect(screen.queryByText("Checked in as Ben. You're all set.")).toBeNull();
  });

  it("offers Continue for a remembered operator, or the list on request", async () => {
    localStorage.setItem(KEY, JSON.stringify({ id: "op1", name: "Ana" }));
    mockApi({ "/checkin/m1": ROSTER });
    render();
    await screen.findByText("Check in at Haas VF2");
    expect(screen.queryByRole("button", { name: "Ben" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Not you? Choose from the list" }));
    expect(screen.getByRole("button", { name: "Ben" })).toBeTruthy();
  });

  it("continues as the remembered operator", async () => {
    localStorage.setItem(KEY, JSON.stringify({ id: "op1", name: "Ana" }));
    const { calls } = mockApi({
      "/checkin/m1": ROSTER,
      "POST /checkin/m1": { operator_id: "op1", operator_name: "Ana" },
    });
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Continue as Ana" }));
    expect(await screen.findByText("Checked in as Ana. You're all set.")).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ operator_id: "op1" });
  });

  it("ignores a corrupt remembered entry", async () => {
    localStorage.setItem(KEY, "{not json");
    mockApi({ "/checkin/m1": ROSTER });
    render();
    expect(await screen.findByRole("button", { name: "Ana" })).toBeTruthy();
  });

  it("adds a new name (trimmed), and ignores a blank one", async () => {
    const { calls } = mockApi({
      "/checkin/m1": { ...ROSTER, operators: [] },
      "POST /checkin/m1": { operator_id: "op9", operator_name: "Cara" },
    });
    render();
    expect(await screen.findByText("No one on the roster yet - add your name below.")).toBeTruthy();
    await userEvent.type(screen.getByLabelText("Not on the list?"), "   ");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await userEvent.type(screen.getByLabelText("Not on the list?"), "Cara ");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText("Checked in as Cara. You're all set.")).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ name: "Cara" });
  });

  it("shows unlinked-QR and failed check-in errors", async () => {
    mockApi({
      "/checkin/m1": new Response("{}", { status: 404 }),
      "POST /checkin/m1": new Response("{}", { status: 500 }),
    });
    render();
    expect(await screen.findByText("This QR code isn't linked to a machine anymore.")).toBeTruthy();
    await userEvent.type(screen.getByLabelText("Not on the list?"), "Dan");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText("Check-in failed. Try again.")).toBeTruthy();
    await waitFor(() => expect(localStorage.getItem(KEY)).toBeNull());
  });
});
