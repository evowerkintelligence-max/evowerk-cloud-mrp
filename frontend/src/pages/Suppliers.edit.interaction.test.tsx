// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { SuppliersPanel } from "./Suppliers";

function supplier(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    name: "Acme Steel",
    email: "sales@acme.test",
    phone: null,
    is_active: true,
    address_line1: "1 Forge Rd",
    address_line2: null,
    city: "Pune",
    state: null,
    postal_code: null,
    country: "IN",
    payment_terms_days: 30,
    lead_time_days: null,
    tags: ["metals"],
    ...over,
  };
}

const CONTACTS = [
  { id: "c1", name: "Priya", role: "Sales", email: "priya@acme.test", phone: "555-1", is_primary: true },
  { id: "c2", name: "Ravi", role: null, email: null, phone: null, is_primary: false },
];

function editDialog() {
  return screen.getByRole("heading", { name: "Edit supplier" }).parentElement!.parentElement!;
}

async function openEdit(name = "Acme Steel") {
  const row = (await screen.findByRole("cell", { name })).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
  return editDialog();
}

function base(extra: Record<string, unknown> = {}) {
  return mockApi({
    "/suppliers": [supplier(), supplier({ id: "s2", name: "Sealtech", email: null, is_active: false, tags: ["seals", "metals"] })],
    "/suppliers/s1/contacts": CONTACTS,
    "/suppliers/s1/scorecard": { on_time_pct: 0.873, defect_pct: null, sample_size: 12 },
    ...extra,
  });
}

describe("SuppliersPanel edit and list flows", () => {
  it("lists suppliers with status and the right toggle per row", async () => {
    base();
    renderPage(<SuppliersPanel />);
    const acme = (await screen.findByRole("cell", { name: "Acme Steel" })).closest("tr")!;
    expect(within(acme).getByText("Active")).toBeTruthy();
    expect(within(acme).getByText("sales@acme.test")).toBeTruthy();
    expect(within(acme).getByRole("button", { name: "Deactivate" })).toBeTruthy();
    const seal = screen.getByRole("cell", { name: "Sealtech" }).closest("tr")!;
    expect(within(seal).getByText("Inactive")).toBeTruthy();
    expect(within(seal).getByRole("button", { name: "Reactivate" })).toBeTruthy();
  });

  it("deactivates and reactivates via the matching endpoint", async () => {
    const api = base({ "POST /suppliers/s1/deactivate": {}, "POST /suppliers/s2/reactivate": {} });
    renderPage(<SuppliersPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Deactivate" }));
    await userEvent.click(screen.getByRole("button", { name: "Reactivate" }));
    await waitFor(() =>
      expect(api.calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([
        "/suppliers/s1/deactivate",
        "/suppliers/s2/reactivate",
      ]),
    );
  });

  it("shows the server error when deactivating fails", async () => {
    base({
      "POST /suppliers/s1/deactivate": new Response(JSON.stringify({ detail: "Supplier has open POs" }), { status: 409 }),
    });
    renderPage(<SuppliersPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Deactivate" }));
    expect(await screen.findByText("Supplier has open POs")).toBeTruthy();
  });

  it("shows the create error from the server", async () => {
    base({ "POST /suppliers": new Response("{}", { status: 500 }) });
    renderPage(<SuppliersPanel />);
    await screen.findByRole("cell", { name: "Acme Steel" });
    await userEvent.type(screen.getByLabelText("Name"), "Dup");
    await userEvent.type(screen.getByLabelText("Email"), "a@b.test");
    await userEvent.type(screen.getByLabelText("Phone"), "123");
    await userEvent.click(screen.getByRole("button", { name: "Add supplier" }));
    expect(await screen.findByText("Failed to create supplier")).toBeTruthy();
  });

  it("filters by tag chip and toggles it off again", async () => {
    const api = base();
    renderPage(<SuppliersPanel />);
    await screen.findByRole("cell", { name: "Acme Steel" });
    // tags from all rows, de-duplicated and sorted
    const chips = screen.getByText("metals").parentElement!;
    expect(Array.from(chips.children).map((c) => c.textContent)).toEqual(["metals", "seals"]);

    await userEvent.click(screen.getByText("seals"));
    await waitFor(() =>
      expect(new URLSearchParams(api.calls[api.calls.length - 1].search).get("tag")).toBe("seals"),
    );
    await userEvent.click(screen.getByText("seals"));
    await waitFor(() => expect(new URLSearchParams(api.calls[api.calls.length - 1].search).has("tag")).toBe(false));
  });

  it("searches and paginates", async () => {
    const page1 = Array.from({ length: 20 }, (_, i) => supplier({ id: `p${i}`, name: `Vendor ${i}`, tags: [] }));
    const api = mockApi({
      "/suppliers": ({ url }: { url: URL }) =>
        url.searchParams.get("offset") === "0" ? page1 : [supplier({ id: "z", name: "Zeta", tags: [] })],
    });
    renderPage(<SuppliersPanel />);
    await screen.findByRole("cell", { name: "Vendor 19" });
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByRole("cell", { name: "Zeta" })).toBeTruthy();
    expect(screen.getByText("Page 2")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Next" }).hasAttribute("disabled")).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    await screen.findByText("Page 1");

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Page 2");
    await userEvent.type(screen.getByPlaceholderText("Search suppliers…"), "ac");
    expect(await screen.findByText("Page 1")).toBeTruthy();
    const last = new URLSearchParams(api.calls[api.calls.length - 1].search);
    expect(last.get("q")).toBe("ac");
    expect(last.get("offset")).toBe("0");
  });

  it("prefills the edit form and saves edits, tags and nulls for blanks", async () => {
    const api = base({ "PATCH /suppliers/s1": supplier() });
    renderPage(<SuppliersPanel />);
    const dialog = await openEdit();
    const d = within(dialog);
    expect(dialogInput("edit-name").value).toBe("Acme Steel");
    expect((d.getByLabelText("City") as HTMLInputElement).value).toBe("Pune");
    expect((d.getByLabelText("Payment terms (days)") as HTMLInputElement).value).toBe("30");

    await userEvent.clear(dialogInput("edit-name"));
    await userEvent.type(dialogInput("edit-name"), "Acme Steel Ltd");
    await userEvent.clear(dialogInput("edit-email"));
    await userEvent.type(dialogInput("edit-phone"), "+91 1");
    await userEvent.type(d.getByLabelText("Address line 2"), "Unit 4");
    await userEvent.type(d.getByLabelText("State"), "MH");
    await userEvent.type(d.getByLabelText("Postal code"), "411001");
    await userEvent.clear(d.getByLabelText("Country"));
    await userEvent.type(d.getByLabelText("Lead time (days)"), "14");

    // add by Enter, add by button, ignore duplicate and blank, remove by click
    await userEvent.type(d.getByLabelText("Tags"), "castings{Enter}");
    await userEvent.type(d.getByLabelText("Tags"), "  forged  ");
    await userEvent.click(d.getByRole("button", { name: "Add" }));
    await userEvent.type(d.getByLabelText("Tags"), "metals{Enter}");
    await userEvent.type(d.getByLabelText("Tags"), "   {Enter}");
    expect((d.getByLabelText("Tags") as HTMLInputElement).value).toBe("");
    await userEvent.click(d.getByText("metals ×"));

    await userEvent.click(d.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Edit supplier" })).toBeNull());
    expect(api.calls.find((c) => c.method === "PATCH")!.body).toEqual({
      name: "Acme Steel Ltd",
      email: null,
      phone: "+91 1",
      address_line1: "1 Forge Rd",
      address_line2: "Unit 4",
      city: "Pune",
      state: "MH",
      postal_code: "411001",
      country: null,
      payment_terms_days: 30,
      lead_time_days: 14,
      tags: ["castings", "forged"],
    });
  }, 20_000);

  it("keeps the dialog open with the error when saving fails, and Cancel closes it", async () => {
    const api = base({
      "PATCH /suppliers/s1": new Response(JSON.stringify({ detail: "Name already taken" }), { status: 409 }),
    });
    renderPage(<SuppliersPanel />);
    const dialog = await openEdit();
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText("Name already taken")).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("heading", { name: "Edit supplier" })).toBeNull();
    expect(api.calls.filter((c) => c.method === "PATCH")).toHaveLength(1);
  });

  it("shows contacts and the scorecard for the supplier being edited", async () => {
    base();
    renderPage(<SuppliersPanel />);
    const d = within(await openEdit());
    expect(await d.findByText("Priya")).toBeTruthy();
    expect(d.getByText("· Sales")).toBeTruthy();
    expect(d.getByText("· priya@acme.test")).toBeTruthy();
    expect(d.getByText("Primary")).toBeTruthy();
    // only the non-primary contact can be promoted
    expect(d.getAllByRole("button", { name: "Make primary" })).toHaveLength(1);
    expect(await d.findByText("87%")).toBeTruthy();
    expect(d.getByText("—")).toBeTruthy();
  });

  it("says when there is not enough scorecard data", async () => {
    base({ "/suppliers/s1/scorecard": { on_time_pct: null, defect_pct: null, sample_size: 0 } });
    renderPage(<SuppliersPanel />);
    const d = within(await openEdit());
    expect(await d.findByText("Not enough data yet.")).toBeTruthy();
  });

  it("adds a contact with blanks as null and clears the form", async () => {
    const api = base({ "POST /suppliers/s1/contacts": CONTACTS[1] });
    renderPage(<SuppliersPanel />);
    const d = within(await openEdit());
    await d.findByText("Priya");
    const add = d.getByRole("button", { name: "Add contact" });
    expect(add.hasAttribute("disabled")).toBe(true);
    const contactName = dialogInput("contact-name");
    await userEvent.type(contactName, "Meera");
    await userEvent.type(dialogInput("contact-role"), "QA");
    await userEvent.click(add);
    await waitFor(() => expect(contactName.value).toBe(""));
    expect(api.calls.find((c) => c.method === "POST" && c.path === "/suppliers/s1/contacts")!.body).toEqual({
      name: "Meera",
      role: "QA",
      email: null,
      phone: null,
      is_primary: false,
    });
  });

  it("shows an add-contact error", async () => {
    base({ "POST /suppliers/s1/contacts": new Response(JSON.stringify({ detail: "Bad email" }), { status: 422 }) });
    renderPage(<SuppliersPanel />);
    const d = within(await openEdit());
    await userEvent.type(dialogInput("contact-name"), "X");
    await userEvent.type(dialogInput("contact-email"), "x@y.test");
    await userEvent.type(dialogInput("contact-phone"), "9");
    await userEvent.click(d.getByRole("button", { name: "Add contact" }));
    expect(await d.findByText("Bad email")).toBeTruthy();
  });

  it("promotes a contact to primary, sending its full record", async () => {
    const api = base({ "PATCH /suppliers/s1/contacts/c2": { ...CONTACTS[1], is_primary: true } });
    renderPage(<SuppliersPanel />);
    const d = within(await openEdit());
    await userEvent.click(await d.findByRole("button", { name: "Make primary" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(api.calls.find((c) => c.method === "PATCH")!.body).toEqual({
      name: "Ravi",
      role: null,
      email: null,
      phone: null,
      is_primary: true,
    });
  });

  it("removes a contact and reports a failed removal", async () => {
    const api = base({
      "DELETE /suppliers/s1/contacts/c1": new Response(null, { status: 204 }),
      "DELETE /suppliers/s1/contacts/c2": new Response(null, { status: 500 }),
    });
    renderPage(<SuppliersPanel />);
    const d = within(await openEdit());
    await d.findByText("Ravi");
    const [removePriya, removeRavi] = d.getAllByRole("button", { name: "Remove" });
    await userEvent.click(removePriya);
    await waitFor(() => expect(api.calls.some((c) => c.path === "/suppliers/s1/contacts/c1")).toBe(true));
    expect(d.queryByText("Failed to delete contact")).toBeNull();
    await userEvent.click(removeRavi);
    expect(await d.findByText("Failed to delete contact")).toBeTruthy();
  });
});

function dialogInput(id: string) {
  return editDialog().querySelector<HTMLInputElement>(`#${id}`)!;
}
