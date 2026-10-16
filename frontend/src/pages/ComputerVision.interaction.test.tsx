// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { ComputerVision } from "./ComputerVision";

beforeEach(() => {
  // PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  // jsdom has no object URLs.
  URL.createObjectURL = vi.fn(() => "blob:preview");
});

const image = () => new File(["png"], "line.png", { type: "image/png" });

function fileInput() {
  return screen.getByText("Image", { selector: "label" }).parentElement!.querySelector("input") as HTMLInputElement;
}

describe("ComputerVision", () => {
  it("uploads the image with the chosen task and shows findings", async () => {
    const api = mockApi({
      "POST /computer-vision/analyze": {
        task: "ppe_compliance", mode: "demo", summary: "2 of 3 workers wear helmets.",
        findings: [
          { label: "No helmet", confidence: "high", location: "left bay", severity: "critical" },
          { label: "Helmet", confidence: "medium", location: null, severity: null },
        ],
        compliance_pct: 66.7, count: 3,
      },
    });
    renderPage(<ComputerVision />);
    const analyze = screen.getByRole("button", { name: "Analyze" }) as HTMLButtonElement;
    expect(analyze.disabled).toBe(true);

    await userEvent.selectOptions(screen.getByRole("combobox"), "ppe_compliance");
    await userEvent.upload(fileInput(), image());
    expect((screen.getByAltText("Preview") as HTMLImageElement).src).toBe("blob:preview");
    await userEvent.click(analyze);

    expect(await screen.findByText("2 of 3 workers wear helmets.")).toBeTruthy();
    const body = api.calls.find((c) => c.method === "POST")!.body as FormData;
    expect(body.get("task")).toBe("ppe_compliance");
    expect((body.get("image") as File).name).toBe("line.png");
    expect(screen.getByText("demo mode")).toBeTruthy();
    expect(screen.getByText("66.7%")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("No helmet")).toBeTruthy();
    expect(screen.getByText("— left bay")).toBeTruthy();
    expect(screen.getByText("critical")).toBeTruthy();
  });

  it("shows the API error", async () => {
    mockApi({ "POST /computer-vision/analyze": new Response(JSON.stringify({ detail: "Image too large" }), { status: 413 }) });
    renderPage(<ComputerVision />);
    await userEvent.upload(fileInput(), image());
    await userEvent.click(screen.getByRole("button", { name: "Analyze" }));
    expect(await screen.findByText("Image too large")).toBeTruthy();
  });

  it("hides stat cards when the task returns neither compliance nor count", async () => {
    mockApi({
      "POST /computer-vision/analyze": { task: "defect_detection", mode: "live", summary: "No defects.", findings: [], compliance_pct: null, count: null },
    });
    renderPage(<ComputerVision />);
    await userEvent.upload(fileInput(), image());
    await userEvent.click(screen.getByRole("button", { name: "Analyze" }));
    expect(await screen.findByText("No defects.")).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Compliance")).toBeNull());
    expect(screen.queryByText("demo mode")).toBeNull();
  });
});
