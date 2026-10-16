// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { mockApi, renderPage } from "@/test/renderPage";
import { Datasets } from "./Datasets";

// PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

const DATASETS = [
  {
    id: "d1", name: "Line 1 output", original_filename: "line1.csv", fmt: "csv", row_count: 1200,
    size_bytes: 1048576, created_at: "2026-10-01T10:00:00Z", columns: [{ name: "ts", dtype: "VARCHAR" }, { name: "qty", dtype: "VARCHAR" }],
  },
  {
    id: "d2", name: "Scrap log", original_filename: "scrap.xlsx", fmt: "xlsx", row_count: 300,
    size_bytes: 524288, created_at: "2026-10-02T10:00:00Z", columns: [{ name: "reason", dtype: "VARCHAR" }],
  },
];

function Loc() {
  return <div data-testid="loc">{useLocation().pathname}</div>;
}

function render() {
  return renderPage(
    <>
      <Datasets />
      <Loc />
    </>,
    { route: "/datasets" },
  );
}

function rowFor(name: string) {
  return screen.getByText(name).closest("tr") as HTMLElement;
}

describe("Datasets", () => {
  it("lists datasets with totals and opens one", async () => {
    mockApi({ "/datasets": DATASETS, "/orgs/me": { role: "member" } });
    render();
    await screen.findByText("Line 1 output");

    expect(screen.getByText("1.5 MB")).toBeTruthy(); // total size of 1 MB + 512 KB
    const row = rowFor("Line 1 output");
    expect(within(row).getByText("line1.csv")).toBeTruthy();
    expect(within(row).getByText("1,200")).toBeTruthy();
    expect(within(row).getByText("1.0 MB")).toBeTruthy();
    // Delete is owner-only.
    expect(within(row).queryByRole("button", { name: "Delete" })).toBeNull();

    await userEvent.click(within(row).getByRole("button", { name: "Open" }));
    expect(screen.getByTestId("loc").textContent).toBe("/datasets/d1");
  });

  it("shows the empty state when there are no datasets", async () => {
    mockApi({ "/datasets": [], "/orgs/me": { role: "owner" } });
    render();
    expect(await screen.findByText("No datasets yet")).toBeTruthy();
  });

  it("shows the load error", async () => {
    mockApi({ "/datasets": new Response("{}", { status: 500 }), "/orgs/me": { role: "owner" } });
    render();
    expect(await screen.findByText("Failed to load datasets")).toBeTruthy();
  });

  it("uploads the chosen file with a trimmed name and refetches the list", async () => {
    let list: typeof DATASETS = [];
    const api = mockApi({
      "/datasets": () => list,
      "/orgs/me": { role: "owner" },
      "POST /datasets": () => {
        list = DATASETS.slice(0, 1);
        return DATASETS[0];
      },
    });
    const { container } = render();
    await screen.findByText("No datasets yet");

    const upload = screen.getByRole("button", { name: "Upload" }) as HTMLButtonElement;
    expect(upload.disabled).toBe(true);
    const file = new File(["ts,qty\n1,2"], "line1.csv", { type: "text/csv" });
    await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, file);
    expect(screen.getByText("line1.csv")).toBeTruthy();
    await userEvent.type(screen.getByPlaceholderText("Defaults to filename"), "  Line 1 output  ");
    await userEvent.click(upload);

    await screen.findByText("Line 1 output");
    const body = api.calls.find((c) => c.method === "POST")!.body as FormData;
    expect((body.get("file") as File).name).toBe("line1.csv");
    expect(body.get("name")).toBe("Line 1 output");
    // Form resets after a successful upload.
    expect((screen.getByPlaceholderText("Defaults to filename") as HTMLInputElement).value).toBe("");
  });

  it("accepts a dropped file and surfaces the server's upload error", async () => {
    mockApi({
      "/datasets": [],
      "/orgs/me": { role: "owner" },
      "POST /datasets": new Response(JSON.stringify({ detail: "Unsupported file type" }), { status: 400 }),
    });
    const { container } = render();
    await screen.findByText("No datasets yet");

    const zone = (container.querySelector('input[type="file"]') as HTMLElement).parentElement!;
    const file = new File(["x"], "notes.txt");
    fireEvent.drop(zone, { dataTransfer: { files: [file] } });
    expect(screen.getByText("notes.txt")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText("Unsupported file type")).toBeTruthy();
  });

  it("lets an owner delete a dataset after confirming", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    let list = DATASETS;
    const api = mockApi({
      "/datasets": () => list,
      "/orgs/me": { role: "owner" },
      "DELETE /datasets/d2": () => {
        list = DATASETS.slice(0, 1);
        return new Response(null, { status: 204 });
      },
    });
    render();
    await screen.findByText("Scrap log");

    await userEvent.click(within(rowFor("Scrap log")).getByRole("button", { name: "Delete" }));
    expect(confirm).toHaveBeenCalledWith('Delete dataset "Scrap log"? This cannot be undone.');
    await waitFor(() => expect(screen.queryByText("Scrap log")).toBeNull());
    expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/datasets/d2")).toBe(true);
  });

  it("does not delete when the confirm is dismissed, and shows a failed delete", async () => {
    const confirmMock = vi.fn(() => false);
    vi.stubGlobal("confirm", confirmMock);
    const api = mockApi({
      "/datasets": DATASETS,
      "/orgs/me": { role: "owner" },
      "DELETE /datasets/d1": new Response("{}", { status: 500 }),
    });
    render();
    await screen.findByText("Line 1 output");

    const del = within(rowFor("Line 1 output")).getByRole("button", { name: "Delete" });
    await userEvent.click(del);
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);

    confirmMock.mockReturnValue(true);
    await userEvent.click(del);
    expect(await screen.findByText("Delete failed")).toBeTruthy();
  });
});
