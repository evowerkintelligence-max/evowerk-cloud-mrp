import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApprovalsPanel } from "./Approvals";

function render(org: { role: string; adjust_approval_threshold: string; demand_policy: string }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["inventory-adjustment-requests"], []);
  return renderToString(
    createElement(
      MemoryRouter,
      null,
      createElement(
        QueryClientProvider,
        { client: qc },
        createElement(ApprovalsPanel, { org }),
      ),
    ),
  )
    .split("<!-- -->")
    .join("");
}

const OWNER = { role: "owner", adjust_approval_threshold: "50", demand_policy: "consume" };

describe("demand policy setting", () => {
  it("offers both choices and reflects the current one", () => {
    const html = render(OWNER);
    expect(html).toContain("When real orders come in");
    expect(html).toContain("Towards it (recommended)");
    expect(html).toContain("On top of it");
    // Both options are always in the markup, so asserting on the value alone
    // passes even with the select's value binding deleted. `selected` is the
    // only part of the output that reflects the org's actual policy.
    expect(html).toContain('value="consume" selected');
    expect(html).not.toContain('value="add" selected');
  });

  it("reflects the add policy when that is what the org is on", () => {
    const html = render({ ...OWNER, demand_policy: "add" });
    expect(html).toContain('value="add" selected');
    expect(html).not.toContain('value="consume" selected');
  });

  it("is hidden from a non-owner, since it changes every planning number", () => {
    const html = render({ ...OWNER, role: "manager" });
    expect(html).not.toContain("When real orders come in");
  });
});
