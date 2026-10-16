// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Lots } from "./Lots";
import { daysUntil } from "./lots/ExpiryCell";

/** YYYY-MM-DD `days` from today. Offsets stay well clear of the 3d/14d
 * thresholds so a timezone-induced one-day shift can't flip a badge. */
function isoInDays(days: number) {
  return new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);
}

const ITEMS = [{ id: "it-1", sku: "MLK-1", name: "Milk powder" }];
const LOCATIONS = [
  { id: "loc-a", code: "MAIN", name: "Main store", is_default: true },
  { id: "loc-b", code: "COLD", name: "Cold room", is_default: false },
];

function lot(over: Record<string, unknown>) {
  return {
    id: "lot-1",
    item_id: "it-1",
    item_sku: "MLK-1",
    item_name: "Milk powder",
    location_id: "loc-a",
    location_code: "MAIN",
    lot_number: "L-001",
    quantity_received: "50",
    quantity_remaining: "40",
    status: "active",
    expiry_date: isoInDays(60),
    ...over,
  };
}

const SOON = lot({ id: "lot-2", lot_number: "L-SOON", expiry_date: isoInDays(7) });
const EXPIRED = lot({ id: "lot-3", lot_number: "L-OLD", expiry_date: isoInDays(-10) });
const NO_EXPIRY = lot({ id: "lot-4", lot_number: "L-NONE", expiry_date: null, status: "depleted" });
const FAR = lot({});

const DETAIL = {
  ...FAR,
  transactions: [
    { id: "t1", transaction_type: "po_receipt", quantity: "50", reason: null, created_by: "u1", created_at: "2026-10-01T10:00:00Z" },
    { id: "t2", transaction_type: "consume", quantity: "-10", reason: "Batch 7", created_by: "u1", created_at: "2026-10-02T10:00:00Z" },
  ],
};

function routes(extra: Record<string, unknown> = {}) {
  return {
    "/items": ITEMS,
    "/locations": LOCATIONS,
    "/lots": [FAR, SOON, EXPIRED, NO_EXPIRY],
    "/lots/lot-1": DETAIL,
    ...extra,
  };
}

async function openLot() {
  await userEvent.click(await screen.findByText("L-001"));
  await screen.findByText("40 / 50 remaining");
}

function formOf(heading: string) {
  return within(screen.getByText(heading, { selector: "p" }).closest("form") as HTMLFormElement);
}

// PageHeader's Typewriter reads prefers-reduced-motion; jsdom has no matchMedia.
beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: true, media: query, addEventListener() {}, removeEventListener() {} }));
});

describe("Lots", () => {
  it("renders lots with expiry badges and filters to expiring-soon", async () => {
    const api = mockApi(routes());
    renderPage(<Lots />);

    expect(await screen.findByText("L-001")).toBeTruthy();
    expect(screen.getByText(/d left$/)).toBeTruthy();
    expect(screen.getByText(/^Expired \d+d ago$/)).toBeTruthy();
    const undated = within(screen.getByText("L-NONE").closest("tr")!);
    expect(undated.getByText("Depleted")).toBeTruthy();
    expect(undated.getByText("—")).toBeTruthy();
    const firstLotsCall = api.calls.find((c) => c.path === "/lots")!;
    expect(new URLSearchParams(firstLotsCall.search).get("limit")).toBe("25");
    expect(new URLSearchParams(firstLotsCall.search).get("offset")).toBe("0");

    await userEvent.click(screen.getByRole("checkbox", { name: /Expiring within 14d only/ }));
    // Expired and soon-to-expire lots stay; the far-off and undated ones drop.
    expect(screen.getByText("L-SOON")).toBeTruthy();
    expect(screen.getByText("L-OLD")).toBeTruthy();
    expect(screen.queryByText("L-001")).toBeNull();
    expect(screen.queryByText("L-NONE")).toBeNull();
  });

  it("sends status and location filters as query params and shows the empty state", async () => {
    const api = mockApi(
      routes({
        "/lots": ({ url }: { url: URL }) => (url.searchParams.get("status") === "on_hold" ? [] : [FAR]),
      }),
    );
    renderPage(<Lots />);
    await screen.findByText("L-001");

    await userEvent.selectOptions(screen.getByLabelText("Status"), "on_hold");
    expect(await screen.findByText("No lots found")).toBeTruthy();
    await userEvent.selectOptions(screen.getAllByLabelText("Location")[1], "loc-b");

    await waitFor(() => {
      const last = api.calls.filter((c) => c.path === "/lots").slice(-1)[0];
      const params = new URLSearchParams(last.search);
      expect(params.get("status")).toBe("on_hold");
      expect(params.get("location_id")).toBe("loc-b");
    });
  });

  it("pages forward only when a full page came back", async () => {
    const full = Array.from({ length: 25 }, (_, i) => lot({ id: `p${i}`, lot_number: `P-${i}` }));
    const api = mockApi(
      routes({ "/lots": ({ url }: { url: URL }) => (url.searchParams.get("offset") === "0" ? full : [SOON]) }),
    );
    renderPage(<Lots />);
    await screen.findByText("P-0");
    expect((screen.getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("L-SOON")).toBeTruthy();
    expect(screen.getByText("Page 2")).toBeTruthy();
    expect(api.calls.some((c) => c.path === "/lots" && new URLSearchParams(c.search).get("offset") === "25")).toBe(true);
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("Page 1")).toBeTruthy();
  });

  it("FEFO consume validates the selection, then posts and lists the consumed lots", async () => {
    const api = mockApi(
      routes({
        "POST /lots/consume-fefo": {
          lines: [
            { lot_id: "lot-2", lot_number: "L-SOON", quantity_consumed: "4", quantity_remaining: "36", expiry_date: null },
            { lot_id: "lot-1", lot_number: "L-001", quantity_consumed: "2", quantity_remaining: "38", expiry_date: null },
          ],
        },
      }),
    );
    renderPage(<Lots />);
    await screen.findByText("L-001");

    const submit = screen.getByRole("button", { name: "Consume earliest-expiring stock first" });
    await userEvent.click(submit);
    expect(await screen.findByText("Pick an item and a location")).toBeTruthy();
    expect(api.calls.some((c) => c.path === "/lots/consume-fefo")).toBe(false);

    await userEvent.selectOptions(screen.getByLabelText("Item"), "it-1");
    await userEvent.selectOptions(screen.getAllByLabelText("Location")[0], "loc-b");
    const qty = screen.getAllByLabelText("Quantity")[0];
    await userEvent.clear(qty);
    await userEvent.type(qty, "6");
    await userEvent.type(screen.getAllByLabelText("Reason (optional)")[0], "Production run");
    await userEvent.click(submit);

    expect(await screen.findByText(/Consumed 2 lots: L-SOON \(4\), L-001 \(2\)/)).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/lots/consume-fefo")!.body).toEqual({
      item_id: "it-1",
      location_id: "loc-b",
      quantity: "6",
      reason: "Production run",
    });
    expect(screen.queryByText("Pick an item and a location")).toBeNull();
  });

  it("shows the FEFO server error", async () => {
    mockApi(
      routes({
        "POST /lots/consume-fefo": new Response(JSON.stringify({ detail: "Not enough stock in lots" }), { status: 409 }),
      }),
    );
    renderPage(<Lots />);
    await screen.findByText("L-001");
    await userEvent.selectOptions(screen.getByLabelText("Item"), "it-1");
    await userEvent.selectOptions(screen.getAllByLabelText("Location")[0], "loc-a");
    await userEvent.click(screen.getByRole("button", { name: "Consume earliest-expiring stock first" }));
    expect(await screen.findByText("Not enough stock in lots")).toBeTruthy();
  });

  it("opens a lot and shows its header and transactions", async () => {
    mockApi(routes());
    renderPage(<Lots />);
    await openLot();
    expect(screen.getByText(/MLK-1 · Lot L-001 · MAIN/)).toBeTruthy();
    expect(screen.getByText("Po Receipt")).toBeTruthy();
    expect(screen.getByText("Batch 7")).toBeTruthy();
    // Transfer destinations exclude the lot's own location.
    const dest = formOf("Transfer").getByLabelText("To location");
    expect(within(dest).queryByRole("option", { name: "MAIN" })).toBeNull();
    expect(within(dest).getByRole("option", { name: "COLD" })).toBeTruthy();
  });

  it("consumes from the selected lot with the typed quantity and reason", async () => {
    const api = mockApi(routes({ "POST /lots/lot-1/consume": DETAIL }));
    renderPage(<Lots />);
    await openLot();

    const form = formOf("Consume");
    const qty = form.getByLabelText("Quantity") as HTMLInputElement;
    await userEvent.clear(qty);
    await userEvent.type(qty, "3");
    await userEvent.type(form.getByLabelText("Reason (optional)"), "QA sample");
    await userEvent.click(form.getByRole("button", { name: "Consume" }));

    await waitFor(() => expect(qty.value).toBe("1"));
    expect(api.calls.find((c) => c.path === "/lots/lot-1/consume")!.body).toEqual({ quantity: "3", reason: "QA sample" });
  });

  it("shows the server error when consuming from a lot fails", async () => {
    mockApi(routes({ "POST /lots/lot-1/consume": new Response("{}", { status: 400 }) }));
    renderPage(<Lots />);
    await openLot();
    await userEvent.click(formOf("Consume").getByRole("button", { name: "Consume" }));
    expect(await screen.findByText("Failed to consume")).toBeTruthy();
  });

  it("requires a destination before transferring, then posts it", async () => {
    const api = mockApi(routes({ "POST /lots/lot-1/transfer": DETAIL }));
    renderPage(<Lots />);
    await openLot();

    const form = formOf("Transfer");
    await userEvent.click(form.getByRole("button", { name: "Transfer" }));
    expect(await screen.findByText("Pick a destination location")).toBeTruthy();

    await userEvent.selectOptions(form.getByLabelText("To location"), "loc-b");
    await userEvent.click(form.getByRole("button", { name: "Transfer" }));
    await waitFor(() => expect(api.calls.some((c) => c.path === "/lots/lot-1/transfer")).toBe(true));
    expect(api.calls.find((c) => c.path === "/lots/lot-1/transfer")!.body).toEqual({
      to_location_id: "loc-b",
      quantity: "1",
    });
  });

  it("holds an active lot, hiding the consume/transfer forms once it is on hold", async () => {
    let status = "active";
    const api = mockApi(
      routes({
        "/lots/lot-1": () => ({ ...DETAIL, status }),
        "POST /lots/lot-1/hold": () => {
          status = "on_hold";
          return { ...DETAIL, status };
        },
      }),
    );
    renderPage(<Lots />);
    await openLot();

    await userEvent.click(screen.getByRole("button", { name: "Hold" }));
    expect(await screen.findByRole("button", { name: "Release hold" })).toBeTruthy();
    expect(api.calls.some((c) => c.method === "POST" && c.path === "/lots/lot-1/hold")).toBe(true);
    expect(screen.queryByText("Consume", { selector: "p" })).toBeNull();
  });

  it("releases an on-hold lot and surfaces a release failure", async () => {
    mockApi(
      routes({
        "/lots/lot-1": { ...DETAIL, status: "on_hold" },
        "POST /lots/lot-1/release": new Response(JSON.stringify({ detail: "QA has not signed off" }), { status: 400 }),
      }),
    );
    renderPage(<Lots />);
    await userEvent.click(await screen.findByText("L-001"));
    await userEvent.click(await screen.findByRole("button", { name: "Release hold" }));
    expect(await screen.findByText("QA has not signed off")).toBeTruthy();
  });

  it("surfaces a hold failure with the fallback message", async () => {
    mockApi(routes({ "POST /lots/lot-1/hold": new Response("not json", { status: 500 }) }));
    renderPage(<Lots />);
    await openLot();
    await userEvent.click(screen.getByRole("button", { name: "Hold" }));
    expect(await screen.findByText("Failed to hold lot")).toBeTruthy();
  });
});

describe("daysUntil", () => {
  /** YYYY-MM-DD for the local calendar day `days` from today. */
  function localIso(days: number) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  it("counts whole local days", () => {
    expect(daysUntil(localIso(0))).toBe(0);
    expect(daysUntil(localIso(1))).toBe(1);
    expect(daysUntil(localIso(-1))).toBe(-1);
  });

  it("reads the date as a local day west of UTC", () => {
    // Node re-reads TZ when it is assigned at runtime. A bare "YYYY-MM-DD"
    // parses as UTC midnight, which is the previous evening here.
    const tz = process.env.TZ;
    process.env.TZ = "America/Los_Angeles";
    try {
      expect(new Date().getTimezoneOffset()).toBeGreaterThan(0);
      expect(daysUntil(localIso(0))).toBe(0);
      expect(daysUntil(localIso(1))).toBe(1);
    } finally {
      if (tz === undefined) delete process.env.TZ;
      else process.env.TZ = tz;
    }
  });
});
