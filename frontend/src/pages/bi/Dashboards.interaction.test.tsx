// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { Dashboards } from "./Dashboards";

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

function Loc() {
  return <div data-testid="loc">{useLocation().pathname}</div>;
}

function render() {
  return renderPage(
    <>
      <Dashboards />
      <Loc />
    </>,
    { route: "/bi" },
  );
}

describe("Dashboards", () => {
  it("lists dashboards as links", async () => {
    mockApi({
      "/bi/dashboards": [
        { id: "b1", name: "Ops overview", created_at: "" },
        { id: "b2", name: "Quality", created_at: "" },
      ],
    });
    render();
    const link = (await screen.findByText("Ops overview")).closest("a") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/bi/b1");
    expect(screen.getByText("Quality").closest("a")!.getAttribute("href")).toBe("/bi/b2");
    expect(screen.getByRole("link", { name: /New chart/ }).getAttribute("href")).toBe("/bi/new");
  });

  it("creates a dashboard and navigates to it", async () => {
    const api = mockApi({ "/bi/dashboards": [], "POST /bi/dashboards": { id: "b9", name: "Line 2", created_at: "" } });
    render();
    await screen.findByText(/No dashboards yet/);
    const create = screen.getByRole("button", { name: "Create dashboard" }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);

    await userEvent.type(screen.getByPlaceholderText("e.g. Ops overview"), "  Line 2 ");
    await userEvent.click(create);

    await waitFor(() => expect(screen.getByTestId("loc").textContent).toBe("/bi/b9"));
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({ name: "Line 2" });
  });

  it("shows the create error", async () => {
    mockApi({
      "/bi/dashboards": [],
      "POST /bi/dashboards": new Response(JSON.stringify({ detail: "Name already used" }), { status: 409 }),
    });
    render();
    await userEvent.type(screen.getByPlaceholderText("e.g. Ops overview"), "Ops");
    await userEvent.click(screen.getByRole("button", { name: "Create dashboard" }));
    expect(await screen.findByText("Name already used")).toBeTruthy();
    expect(screen.getByTestId("loc").textContent).toBe("/bi");
  });
});
