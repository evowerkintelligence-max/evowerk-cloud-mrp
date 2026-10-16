// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { ApprovalsPanel } from "./Approvals";

const OWNER = { role: "owner", adjust_approval_threshold: "50", demand_policy: "consume" };
const COUNT = {
  id: "req1",
  item_sku: "BOLT-8",
  location_code: "MAIN",
  quantity_delta: "-75",
  counted_quantity: "25",
  expected_quantity_at_submit: "100",
  reason_code: "cycle_count",
  reason_note: "shelf B mislabeled",
  status: "pending",
  requested_at: "2026-09-10T08:00:00Z",
};
const ADJUST = { ...COUNT, id: "req2", item_sku: "NUT-8", counted_quantity: null, reason_code: "damage", reason_note: null };

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/inventory/adjustment-requests": [COUNT, ADJUST],
    "POST /inventory/adjustment-requests/req1/approve": { ...COUNT, status: "approved" },
    "POST /inventory/adjustment-requests/req2/reject": { ...ADJUST, status: "rejected" },
    "PATCH /orgs/me": OWNER,
    ...overrides,
  });
}

function row(sku: string) {
  return screen.getByText(sku).closest("tr") as HTMLElement;
}

describe("ApprovalsPanel", () => {
  it("lists pending requests with counted/expected and reason note", async () => {
    const { calls } = api();
    renderPage(<ApprovalsPanel org={OWNER} />);
    expect(await screen.findByText("BOLT-8")).toBeTruthy();
    expect(within(row("BOLT-8")).getByText("25 / 100")).toBeTruthy();
    expect(within(row("BOLT-8")).getByText("cycle_count: shelf B mislabeled")).toBeTruthy();
    expect(within(row("NUT-8")).getByText("—")).toBeTruthy();
    expect(calls[0].search).toBe("?status=pending");
  });

  it("shows the empty state", async () => {
    api({ "/inventory/adjustment-requests": [] });
    renderPage(<ApprovalsPanel org={OWNER} />);
    expect(await screen.findByText("No pending approvals")).toBeTruthy();
  });

  it("approves with the typed review note and rejects with a null note", async () => {
    const { calls } = api();
    renderPage(<ApprovalsPanel org={OWNER} />);
    await screen.findByText("BOLT-8");
    await userEvent.type(within(row("BOLT-8")).getByPlaceholderText("Note (optional)"), "recounted");
    await userEvent.click(within(row("BOLT-8")).getByRole("button", { name: "Approve" }));
    await userEvent.click(within(row("NUT-8")).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/reject"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/req1/approve"))!.body).toEqual({ review_note: "recounted" });
    expect(calls.find((c) => c.path.endsWith("/req2/reject"))!.body).toEqual({ review_note: null });
  });

  it("shows approve and reject errors", async () => {
    api({
      "POST /inventory/adjustment-requests/req1/approve": new Response(JSON.stringify({ detail: "Stock moved since" }), { status: 409 }),
      "POST /inventory/adjustment-requests/req2/reject": new Response("{}", { status: 500 }),
    });
    renderPage(<ApprovalsPanel org={OWNER} />);
    await screen.findByText("BOLT-8");
    await userEvent.click(within(row("BOLT-8")).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Stock moved since")).toBeTruthy();
    await userEvent.click(within(row("NUT-8")).getByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Reject failed")).toBeTruthy();
  });

  it("saves a new approval threshold and clears the field", async () => {
    const { calls } = api();
    renderPage(<ApprovalsPanel org={OWNER} />);
    expect(screen.getByText(/Current: 50\./)).toBeTruthy();
    const input = screen.getByLabelText("Quantity") as HTMLInputElement;
    await userEvent.type(input, "120");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ adjust_approval_threshold: "120" });
    await waitFor(() => expect(input.value).toBe(""));
  });

  it("shows a threshold error", async () => {
    api({ "PATCH /orgs/me": new Response(JSON.stringify({ detail: "Must be positive" }), { status: 422 }) });
    renderPage(<ApprovalsPanel org={OWNER} />);
    await userEvent.type(screen.getByLabelText("Quantity"), "1");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Must be positive")).toBeTruthy();
  });

  it("patches the demand policy, and shows its error", async () => {
    const { calls } = api({ "PATCH /orgs/me": new Response("{}", { status: 500 }) });
    renderPage(<ApprovalsPanel org={OWNER} />);
    await userEvent.selectOptions(screen.getByLabelText(/When real orders come in/), "add");
    expect(await screen.findByText("Failed to update the setting")).toBeTruthy();
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ demand_policy: "add" });
  });

  it("gives managers a read-only list without settings or actions", async () => {
    api();
    renderPage(<ApprovalsPanel org={{ ...OWNER, role: "manager" }} />);
    await screen.findByText("BOLT-8");
    expect(screen.queryByText("Approval threshold")).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByText("Actions")).toBeNull();
  });
});
