// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { OperatorsPanel } from "./OperatorsPanel";

// The performance card under the roster draws an echarts chart; jsdom has no canvas.
vi.mock("@/pages/analytics/shared", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/pages/analytics/shared")>()),
  EChart: () => null,
}));

const ANA = {
  id: "op1",
  name: "Ana Silva",
  code: "A1",
  badge_id: "B-9",
  status: "active",
  needs_review: false,
  duplicate_of_id: null,
  duplicate_of_name: null,
};
const DUP = {
  ...ANA,
  id: "op2",
  name: "Ana S.",
  code: null,
  badge_id: null,
  needs_review: true,
  duplicate_of_id: "op1",
  duplicate_of_name: "Ana Silva",
};

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/orgs/me": { id: "u1", name: "Owner", role: "owner", user_id: "u1", enabled_features: ["production"] },
    "/operators": [ANA, DUP],
    "/operators/capture-events": [],
    "POST /operators": { ...ANA, id: "op3" },
    "POST /operators/op2/merge": ANA,
    "PATCH /operators/op2": { ...DUP, needs_review: false },
    ...overrides,
  });
}

describe("OperatorsPanel", () => {
  it("puts operators needing review first, with the suggested duplicate preselected", async () => {
    api();
    renderPage(<OperatorsPanel />);
    expect(await screen.findByText("Possible duplicate of Ana Silva.")).toBeTruthy();
    const names = screen.getAllByRole("row").map((r) => r.querySelector("td")?.textContent).filter(Boolean);
    expect(names[0]).toBe("Ana S.");
    expect(screen.getByText("Assigned")).toBeTruthy();
    expect((screen.getByRole("option", { name: "Ana Silva" }) as HTMLOptionElement).selected).toBe(true);
  });

  it("shows the generic duplicate text when there's no named match, and the empty state", async () => {
    api({ "/operators": [{ ...DUP, duplicate_of_id: null, duplicate_of_name: null }] });
    renderPage(<OperatorsPanel />);
    expect(await screen.findByText("Possible duplicate of an existing operator.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Merge" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the empty roster state", async () => {
    api({ "/operators": [] });
    renderPage(<OperatorsPanel />);
    expect(await screen.findByText("No operators yet")).toBeTruthy();
  });

  it("merges a duplicate into the chosen operator", async () => {
    const { calls } = api();
    renderPage(<OperatorsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Merge" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/operators/op2/merge")).toBe(true));
    expect(calls.find((c) => c.path === "/operators/op2/merge")!.body).toEqual({ target_operator_id: "op1" });
  });

  it("keeps both by clearing the review flag", async () => {
    const { calls } = api();
    renderPage(<OperatorsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Keep both" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ needs_review: false });
  });

  it("surfaces merge and keep-both errors", async () => {
    api({
      "POST /operators/op2/merge": new Response(JSON.stringify({ detail: "Target is inactive" }), { status: 409 }),
      "PATCH /operators/op2": new Response("{}", { status: 500 }),
    });
    renderPage(<OperatorsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Merge" }));
    expect(await screen.findByText("Target is inactive")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Keep both" }));
    expect(await screen.findByText("Failed to update operator")).toBeTruthy();
  });

  it("adds an operator, trimming blank optional fields to null", async () => {
    const { calls } = api();
    renderPage(<OperatorsPanel />);
    await userEvent.type(screen.getByLabelText("Name"), "Ben");
    await userEvent.type(screen.getByLabelText("Code"), "   ");
    await userEvent.type(screen.getByLabelText("Badge ID"), " B-12 ");
    await userEvent.click(screen.getByRole("button", { name: "Add operator" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/operators")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/operators")!.body).toEqual({
      name: "Ben",
      code: null,
      badge_id: "B-12",
    });
    await waitFor(() => expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(""));
  });

  it("shows an add error", async () => {
    api({ "POST /operators": new Response(JSON.stringify({ detail: "Badge in use" }), { status: 409 }) });
    renderPage(<OperatorsPanel />);
    await userEvent.type(screen.getByLabelText("Name"), "Ben");
    await userEvent.click(screen.getByRole("button", { name: "Add operator" }));
    expect(await screen.findByText("Badge in use")).toBeTruthy();
  });
});
