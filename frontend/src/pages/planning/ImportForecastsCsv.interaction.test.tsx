// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { ImportForecastsCsvSection } from "./ImportForecastsCsv";
import { CSV_TEMPLATE } from "./csvImport";

const ITEMS = [
  { id: "p1", sku: "WIDGET-1" },
  { id: "p2", sku: "GADGET-2" },
];

// jsdom 25's File has no text(); the browser one does, so give each test file one.
function csvFile(text: string, name = "forecast.csv") {
  const file = new File([text], name, { type: "text/csv" });
  Object.defineProperty(file, "text", { value: async () => text });
  return file;
}

const fileInput = () => screen.getByLabelText("Forecast CSV file") as HTMLInputElement;

describe("ImportForecastsCsvSection", () => {
  it("previews valid rows and imports them, then reports the counts and resets", async () => {
    const api = mockApi({ "POST /forecasts/import": { created: 1, updated: 1, total: 2 } });
    renderPage(<ImportForecastsCsvSection items={ITEMS} />);

    await userEvent.upload(
      fileInput(),
      csvFile('sku,period_start,quantity,notes\nWIDGET-1,2026-11-01,100,"peak, Q4"\nGADGET-2,2026-12-01,5,\n'),
    );

    expect(await screen.findByText("peak, Q4")).toBeTruthy();
    expect(screen.getByText("GADGET-2")).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Import 2 rows from forecast.csv" }));

    expect(await screen.findByText(/Imported 2 rows: 1 created,\s*1 updated\./)).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/forecasts/import")!.body).toEqual([
      { item_id: "p1", period_start: "2026-11-01", quantity: "100", notes: "peak, Q4" },
      { item_id: "p2", period_start: "2026-12-01", quantity: "5", notes: null },
    ]);
    expect(screen.queryByRole("button", { name: /^Import \d/ })).toBeNull();
    expect(fileInput().value).toBe("");
  });

  it("lists row errors and offers no import when any row is bad", async () => {
    const api = mockApi({});
    renderPage(<ImportForecastsCsvSection items={ITEMS} />);

    await userEvent.upload(
      fileInput(),
      csvFile("sku,period_start,quantity\nWIDGET-1,2026-11-01,10\nNOPE,2026-11-01,1\nGADGET-2,11/01/2026,1\nWIDGET-1,2026-11-01,-4\n"),
    );

    expect(await screen.findByText("Line 3: Unknown SKU: NOPE")).toBeTruthy();
    expect(screen.getByText("Line 4: Bad date (expected YYYY-MM-DD): 11/01/2026")).toBeTruthy();
    expect(screen.getByText("Line 5: Quantity must be a non-negative number: -4")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Import/ })).toBeNull();
    expect(api.calls).toHaveLength(0);
  });

  it("rejects a file with the wrong header", async () => {
    mockApi({});
    renderPage(<ImportForecastsCsvSection items={ITEMS} />);
    await userEvent.upload(fileInput(), csvFile("item,date,qty\nWIDGET-1,2026-11-01,1\n"));
    expect(await screen.findByText("Line 1: Header must be: sku,period_start,quantity[,notes]")).toBeTruthy();
  });

  it("shows the server error when the import fails and keeps the preview", async () => {
    mockApi({
      "POST /forecasts/import": new Response(JSON.stringify({ detail: "Period outside horizon" }), { status: 400 }),
    });
    renderPage(<ImportForecastsCsvSection items={ITEMS} />);
    await userEvent.upload(fileInput(), csvFile("sku,period_start,quantity\nWIDGET-1,2026-11-01,3\n", "one.csv"));
    await userEvent.click(await screen.findByRole("button", { name: "Import 1 row from one.csv" }));

    expect(await screen.findByText("Period outside horizon")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import 1 row from one.csv" })).toBeTruthy();
  });

  it("downloads the template as a CSV blob", async () => {
    mockApi({});
    const blobs: Blob[] = [];
    vi.stubGlobal("URL", Object.assign(Object.create(URL), {
      createObjectURL: (b: Blob) => {
        blobs.push(b);
        return "blob:template";
      },
      revokeObjectURL: () => {},
    }));
    const clicked: HTMLAnchorElement[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this);
    });

    renderPage(<ImportForecastsCsvSection items={ITEMS} />);
    await userEvent.click(screen.getByRole("button", { name: "Download template" }));

    await waitFor(() => expect(clicked).toHaveLength(1));
    expect(clicked[0].download).toBe("forecast-import-template.csv");
    expect(blobs[0].type).toBe("text/csv");
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.readAsText(blobs[0]);
    });
    expect(text).toBe(CSV_TEMPLATE);
    click.mockRestore();
  });
});
