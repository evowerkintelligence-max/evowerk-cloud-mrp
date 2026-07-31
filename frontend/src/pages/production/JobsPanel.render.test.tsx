import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { JobsPanel } from "./JobsPanel";

const RUNNING = {
  id: "wo-2", wo_number: 5, item_id: "item-1", item_sku: "PUMP-1", item_name: "Coolant pump",
  item_is_lot_tracked: false, status: "in_progress", quantity_planned: "50.0000",
  quantity_good: "0.0000", quantity_scrapped: "0.0000", scrap_reason: null,
  remainder_disposition: null, location_id: "loc-1", location_code: "MAIN",
  scheduled_start: null, started_at: "2026-07-27T08:00:00+00:00", finished_at: null,
  notes: null, assigned_operator_id: null, components: [],
};

function renderJobs(seed: (qc: QueryClient) => void = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["org-me"], { id: "u-1", name: "Owner", role: "owner", user_id: "u-1", enabled_features: ["production"] });
  seed(qc);
  return renderToString(
    createElement(MemoryRouter, null, createElement(QueryClientProvider, { client: qc }, createElement(JobsPanel))),
  ).split("<!-- -->").join("");
}

describe("JobsPanel", () => {
  it("shows an Edit button for a running job, not just a scheduled one", () => {
    const html = renderJobs((qc) => qc.setQueryData(["work-orders", "", ""], [RUNNING]));
    expect(html).toContain("Edit");
    expect(html).toContain("Finish");
  });

  it("links a job's item to its Inventory page", () => {
    const html = renderJobs((qc) => qc.setQueryData(["work-orders", "", ""], [RUNNING]));
    expect(html).toContain('href="/warehouse?item=item-1"');
  });
});
