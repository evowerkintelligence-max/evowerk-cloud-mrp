// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { SuppliersPanel } from "./Suppliers";

const ACME = { id: "s1", name: "Acme Steel", email: null, phone: null, is_active: true, tags: [] };

describe("SuppliersPanel", () => {
  it("adds a supplier and posts the typed name", async () => {
    const api = mockApi({ "/suppliers": [], "POST /suppliers": ACME });
    renderPage(<SuppliersPanel />);
    await screen.findByText("No suppliers yet");

    await userEvent.type(screen.getAllByRole("textbox")[0], "Acme Steel");
    await userEvent.click(screen.getByRole("button", { name: "Add supplier" }));

    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path === "/suppliers")).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")!.body).toMatchObject({ name: "Acme Steel" });
  });
});
