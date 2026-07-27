import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Production } from "./Production";

const YESTERDAY = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
const TOMORROW = new Date(Date.now() + 86400000).toISOString().slice(0, 10);

const SCHEDULED = {
  id: "wo-1",
  wo_number: 4,
  item_id: "item-1",
  item_sku: "PUMP-1",
  item_name: "Coolant pump",
  item_is_lot_tracked: false,
  status: "scheduled",
  quantity_planned: "50.0000",
  quantity_good: "0.0000",
  quantity_scrapped: "0.0000",
  scrap_reason: null,
  remainder_disposition: null,
  location_id: "loc-1",
  location_code: "MAIN",
  scheduled_start: TOMORROW,
  started_at: null,
  finished_at: null,
  notes: null,
  components: [
    {
      item_id: "item-2",
      item_sku: "SEAL-1",
      item_name: "Seal",
      quantity_required: "50.0000",
      quantity_consumed: "0.0000",
      sequence: 0,
    },
  ],
};

const RUNNING = {
  ...SCHEDULED,
  id: "wo-2",
  wo_number: 5,
  status: "in_progress",
  started_at: "2026-07-27T08:00:00+00:00",
};

const DONE = {
  ...SCHEDULED,
  id: "wo-3",
  wo_number: 6,
  status: "done",
  quantity_good: "50.0000",
  finished_at: "2026-07-27T16:00:00+00:00",
};

const LATE = { ...SCHEDULED, id: "wo-4", wo_number: 7, scheduled_start: YESTERDAY };

function render(jobs: unknown[] | undefined, role = "owner") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["org-me"], {
    id: "org-1",
    name: "Acme",
    role,
    user_id: "user-1",
    enabled_features: ["production"],
  });
  if (jobs !== undefined) qc.setQueryData(["work-orders", "", ""], jobs);
  // renderToString splits adjacent text nodes with <!-- --> markers - strip them
  // so assertions can match the text a user actually sees.
  return renderToString(
    createElement(
      MemoryRouter,
      null,
      createElement(QueryClientProvider, { client: qc }, createElement(Production)),
    ),
  )
    .split("<!-- -->")
    .join("");
}

describe("Production", () => {
  it("renders with no data without crashing", () => {
    const html = render(undefined);
    expect(html).toContain("Production");
  });

  it("shows a scheduled job with its number, product, quantity and a Start action", () => {
    const html = render([SCHEDULED]);
    expect(html).toContain("WO-4");
    expect(html).toContain("Coolant pump");
    expect(html).toContain("PUMP-1");
    expect(html).toContain("50");
    expect(html).toContain("Start");
    expect(html).toContain("Edit");
  });

  it("offers Finish and Cancel on a running job, not Start", () => {
    const html = render([RUNNING]);
    expect(html).toContain("Finish");
    expect(html).toContain("Cancel");
    expect(html).not.toContain(">Start<");
  });

  it("offers nothing on a job that is already done", () => {
    const html = render([DONE]);
    expect(html).toContain("WO-6");
    expect(html).not.toContain(">Start<");
    expect(html).not.toContain(">Finish<");
    expect(html).not.toContain(">Cancel<");
  });

  it("badges a scheduled job whose start date has passed as late", () => {
    const html = render([LATE]);
    expect(html).toContain("Late");
  });

  it("does not badge a job that is still due in the future", () => {
    const html = render([SCHEDULED]);
    expect(html).not.toContain("Late");
  });

  it("shows a member no action buttons at all", () => {
    const html = render([SCHEDULED, RUNNING], "member");
    expect(html).toContain("WO-4");
    expect(html).not.toContain(">Start<");
    expect(html).not.toContain(">Finish<");
    expect(html).not.toContain(">Cancel<");
    expect(html).not.toContain(">Edit<");
  });

  it("invites the user to make something when there are no jobs", () => {
    const html = render([]);
    expect(html).toContain("No jobs yet");
  });
});
