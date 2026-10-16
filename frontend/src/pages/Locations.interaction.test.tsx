// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { LocationsPanel } from "./Locations";

const MAIN = { id: "loc-a", code: "MAIN", name: "Main store", is_default: true };
const COLD = { id: "loc-b", code: "COLD", name: "Cold room", is_default: false };

describe("LocationsPanel", () => {
  it("lists locations and marks only the default one", async () => {
    mockApi({ "/locations": [MAIN, COLD] });
    renderPage(<LocationsPanel />);
    expect(await screen.findByText("Cold room")).toBeTruthy();
    expect(screen.getByText("Main store")).toBeTruthy();
    expect(screen.getAllByText("Default", { selector: "span, div" })).toHaveLength(1);
  });

  it("creates a location, clears the form, and refetches the list", async () => {
    let list = [MAIN];
    const api = mockApi({
      "/locations": () => list,
      "POST /locations": ({ body }: { body: unknown }) => {
        list = [MAIN, { ...COLD, ...(body as object) }];
        return list[1];
      },
    });
    renderPage(<LocationsPanel />);
    await screen.findByText("Main store");

    const code = screen.getByLabelText("Code") as HTMLInputElement;
    await userEvent.type(code, "COLD");
    await userEvent.type(screen.getByLabelText("Name"), "Cold room");
    await userEvent.click(screen.getByRole("button", { name: "Add location" }));

    expect(await screen.findByText("Cold room")).toBeTruthy();
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({ code: "COLD", name: "Cold room" });
    await waitFor(() => expect(code.value).toBe(""));
  });

  it("shows the empty state and the server's error on a failed create", async () => {
    mockApi({
      "/locations": [],
      "POST /locations": new Response(JSON.stringify({ detail: "Code already exists" }), { status: 409 }),
    });
    renderPage(<LocationsPanel />);
    expect(await screen.findByText("No locations yet")).toBeTruthy();

    await userEvent.type(screen.getByLabelText("Code"), "MAIN");
    await userEvent.type(screen.getByLabelText("Name"), "Dup");
    await userEvent.click(screen.getByRole("button", { name: "Add location" }));
    expect(await screen.findByText("Code already exists")).toBeTruthy();
  });
});
