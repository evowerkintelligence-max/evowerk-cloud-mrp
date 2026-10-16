// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Procurement } from "./Procurement";

function api() {
  return mockApi({ "/items": [], "/suppliers": [], "/purchase-orders": [] });
}

beforeEach(() => {
  // PageHeader's Typewriter reads prefers-reduced-motion; jsdom has no matchMedia.
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }));
});

describe("Procurement", () => {
  it("opens on purchase orders and switches between tabs", async () => {
    api();
    renderPage(<Procurement />);
    expect(await screen.findByText("Create a draft PO")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Suppliers" }));
    expect(await screen.findByText("Add a supplier")).toBeTruthy();
    expect(screen.queryByText("Create a draft PO")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Build Calculator" }));
    expect(await screen.findByText("Build calculator")).toBeTruthy();
    expect(screen.queryByText("Add a supplier")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Purchase Orders" }));
    expect(await screen.findByText("Create a draft PO")).toBeTruthy();
  });

  it("honours a valid ?tab=", async () => {
    api();
    renderPage(<Procurement />, { route: "/procurement?tab=suppliers" });
    expect(await screen.findByText("Add a supplier")).toBeTruthy();
  });

  it("falls back to purchase orders for an unknown ?tab=", async () => {
    api();
    renderPage(<Procurement />, { route: "/procurement?tab=nope" });
    expect(await screen.findByText("Create a draft PO")).toBeTruthy();
  });
});
