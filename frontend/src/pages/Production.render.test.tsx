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

function renderJobs(jobs: unknown[] | undefined, role = "owner") {
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

describe("Production (Jobs tab)", () => {
  it("renders with no data without crashing", () => {
    const html = renderJobs(undefined);
    expect(html).toContain("Production");
  });

  it("shows a scheduled job with its number, product, quantity and a Start action", () => {
    const html = renderJobs([SCHEDULED]);
    expect(html).toContain("WO-4");
    expect(html).toContain("Coolant pump");
    expect(html).toContain("PUMP-1");
    expect(html).toContain("50");
    expect(html).toContain("Start");
    expect(html).toContain("Edit");
  });

  it("offers Finish and Cancel on a running job, not Start", () => {
    const html = renderJobs([RUNNING]);
    expect(html).toContain("Finish");
    expect(html).toContain("Cancel");
    expect(html).not.toContain(">Start<");
  });

  it("offers nothing on a job that is already done", () => {
    const html = renderJobs([DONE]);
    expect(html).toContain("WO-6");
    expect(html).not.toContain(">Start<");
    expect(html).not.toContain(">Finish<");
    expect(html).not.toContain(">Cancel<");
  });

  it("badges a scheduled job whose start date has passed as late", () => {
    const html = renderJobs([LATE]);
    expect(html).toContain("Late");
  });

  it("does not badge a job that is still due in the future", () => {
    const html = renderJobs([SCHEDULED]);
    expect(html).not.toContain("Late");
  });

  it("shows a member no action buttons at all", () => {
    const html = renderJobs([SCHEDULED, RUNNING], "member");
    expect(html).toContain("WO-4");
    expect(html).not.toContain(">Start<");
    expect(html).not.toContain(">Finish<");
    expect(html).not.toContain(">Cancel<");
    expect(html).not.toContain(">Edit<");
  });

  it("invites the user to make something when there are no jobs", () => {
    const html = renderJobs([]);
    expect(html).toContain("No jobs yet");
  });

  it("offers a way to raise a job that is scheduled rather than started", () => {
    // The grid's Make button always starts immediately, so without this the
    // whole reservation, horizon and late-to-start machinery is unreachable.
    const html = renderJobs([SCHEDULED]);
    expect(html).toContain("New job");
  });

  it("shows a member no way to raise a job either", () => {
    const html = renderJobs([SCHEDULED], "member");
    expect(html).not.toContain("New job");
  });
});

const WC = {
  id: "wc-1",
  code: "CNC-01",
  name: "CNC Cell",
  status: "active",
  machine_capacity: 2,
  default_cycle_time_seconds: 45,
  default_mtbf_hours: 100,
  default_mttr_hours: 2,
};

function renderSetup(tab: string, seed: (qc: QueryClient) => void = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed(qc);
  return renderToString(
    createElement(
      MemoryRouter,
      { initialEntries: [`/production?tab=${tab}`] },
      createElement(QueryClientProvider, { client: qc }, createElement(Production)),
    ),
  ).split("<!-- -->").join("");
}

describe("Production (setup tabs)", () => {
  it("renders each tab empty without crashing", () => {
    for (const tab of ["work-centers", "machines", "routings", "operators"]) {
      const html = renderSetup(tab);
      expect(html).toContain("Production");
      expect(html).toContain("Work Centers");
      expect(html).toContain("Machines");
      expect(html).toContain("Routings");
    }
  });

  it("work centers tab shows the create form and lists rows", () => {
    const html = renderSetup("work-centers", (qc) => qc.setQueryData(["work-centers"], [WC]));
    expect(html).toContain("Add a work center");
    expect(html).toContain("Machines in parallel");
    expect(html).toContain("CNC-01");
    expect(html).toContain("CNC Cell");
    expect(html).toContain("Active");
  });

  it("work centers tab shows the empty state when there are none", () => {
    const html = renderSetup("work-centers", (qc) => qc.setQueryData(["work-centers"], []));
    expect(html).toContain("No work centers yet");
  });

  it("machines tab dropdown is populated from the work-centers list", () => {
    const html = renderSetup("machines", (qc) => {
      qc.setQueryData(["work-centers"], [WC]);
      qc.setQueryData(["machines"], []);
    });
    expect(html).toContain("Add a machine");
    expect(html).toContain('value="wc-1"');
    expect(html).toContain("CNC-01 - CNC Cell");
    expect(html).toContain("No machines yet");
  });

  it("machines tab lists machines with work center, status and needs-review badge", () => {
    const html = renderSetup("machines", (qc) => {
      qc.setQueryData(["work-centers"], [WC]);
      qc.setQueryData(
        ["machines"],
        [
          {
            id: "m-1",
            work_center_id: "wc-1",
            code: "M-100",
            name: "Haas VF2",
            status: "active",
            mtbf_hours: null,
            mttr_hours: null,
            needs_review: true,
          },
        ],
      );
    });
    expect(html).toContain("M-100");
    expect(html).toContain("Haas VF2");
    expect(html).toContain("CNC-01 - CNC Cell");
    expect(html).toContain("Needs review");
  });

  it("machines tab tells you to add a work center first when there are none", () => {
    const html = renderSetup("machines", (qc) => {
      qc.setQueryData(["work-centers"], []);
      qc.setQueryData(["machines"], []);
    });
    expect(html).toContain("Add a work center first");
  });

  it("routings tab lists routings with their item label and step count", () => {
    const html = renderSetup("routings", (qc) => {
      qc.setQueryData(["work-centers"], [WC]);
      qc.setQueryData(["items"], [{ id: "item-1", sku: "WIDGET-1", name: "Widget" }]);
      qc.setQueryData(
        ["routings"],
        [
          {
            id: "r-1",
            item_id: "item-1",
            version: 1,
            status: "draft",
            steps: [
              {
                id: "s-1",
                sequence: 1,
                work_center_id: "wc-1",
                cycle_time_seconds: 30,
                setup_time_seconds: null,
              },
            ],
          },
        ],
      );
    });
    expect(html).toContain("Create a draft routing");
    expect(html).toContain("WIDGET-1 - Widget");
    expect(html).toContain("v1");
    expect(html).toContain("Draft");
    expect(html).toContain("Activate");
  });

  it("routings tab shows the empty state when there are none", () => {
    const html = renderSetup("routings", (qc) => qc.setQueryData(["routings"], []));
    expect(html).toContain("No routings yet");
  });

  it("operators tab shows the create form and lists rows", () => {
    const html = renderSetup("operators", (qc) =>
      qc.setQueryData(
        ["operators"],
        [{ id: "op-1", name: "J Kim", code: "E-100", badge_id: null, status: "active", needs_review: false }],
      ),
    );
    expect(html).toContain("Add an operator");
    expect(html).toContain("J Kim");
    expect(html).toContain("E-100");
    expect(html).toContain("Active");
  });

  it("operators tab shows the empty state when there are none", () => {
    const html = renderSetup("operators", (qc) => qc.setQueryData(["operators"], []));
    expect(html).toContain("No operators yet");
  });

  it("operators tab surfaces a needs-review row with a merge picker", () => {
    const html = renderSetup("operators", (qc) =>
      qc.setQueryData(
        ["operators"],
        [
          {
            id: "op-1",
            name: "Johnson",
            code: null,
            badge_id: null,
            status: "active",
            needs_review: false,
            duplicate_of_id: null,
            duplicate_of_name: null,
          },
          {
            id: "op-2",
            name: "Jonson",
            code: null,
            badge_id: null,
            status: "active",
            needs_review: true,
            duplicate_of_id: "op-1",
            duplicate_of_name: "Johnson",
          },
        ],
      ),
    );
    expect(html).toContain("Needs review");
    expect(html).toContain("Possible duplicate of Johnson");
    expect(html).toContain("Merge into");
    expect(html).toContain("Keep both");
  });
});
