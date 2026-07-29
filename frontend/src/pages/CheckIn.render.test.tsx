// frontend/src/pages/CheckIn.render.test.tsx
import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CheckIn } from "./CheckIn";

function renderCheckIn(machineId: string, seed: (qc: QueryClient) => void = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed(qc);
  return renderToString(
    createElement(
      MemoryRouter,
      { initialEntries: [`/checkin/${machineId}`] },
      createElement(
        QueryClientProvider,
        { client: qc },
        createElement(
          Routes,
          null,
          createElement(Route, { path: "/checkin/:machineId", element: createElement(CheckIn) }),
        ),
      ),
    ),
  ).split("<!-- -->").join("");
}

describe("CheckIn", () => {
  it("lists the roster to tap when there's no remembered operator", () => {
    const html = renderCheckIn("machine-1", (qc) =>
      qc.setQueryData(["checkin-roster", "machine-1"], {
        machine_id: "machine-1",
        machine_name: "CNC 01",
        operators: [{ id: "op-1", name: "J Kim" }],
      }),
    );
    expect(html).toContain("CNC 01");
    expect(html).toContain("J Kim");
    expect(html).toContain("Not on the list?");
  });

  it("shows the empty-roster message when there are no operators yet", () => {
    const html = renderCheckIn("machine-1", (qc) =>
      qc.setQueryData(["checkin-roster", "machine-1"], {
        machine_id: "machine-1",
        machine_name: "CNC 01",
        operators: [],
      }),
    );
    expect(html).toContain("No one on the roster yet");
  });
});
