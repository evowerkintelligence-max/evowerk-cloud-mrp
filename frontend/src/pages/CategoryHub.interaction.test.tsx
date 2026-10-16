// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { mockApi, renderPage } from "@/test/renderPage";
import { AnalyticsHub, DataReportsHub, OperationsHub, SystemHub } from "./CategoryHub";

const me = (enabled_features: string[]) => ({ id: "o1", name: "Test Org", role: "owner", user_id: "u1", enabled_features });

// PageHeader's Typewriter reads matchMedia, which jsdom lacks.
beforeEach(() => {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q }));
});

function cardHrefs() {
  return screen.getAllByRole("link").map((a) => a.getAttribute("href"));
}

describe("CategoryHub", () => {
  it("Operations lists only the pages the org's plan includes, plus ungated ones", async () => {
    mockApi({ "/orgs/me": me(["boms", "planning"]) });
    renderPage(<OperationsHub />);

    await screen.findByText("Bills of Materials");
    expect(cardHrefs()).toEqual(["/boms", "/planning", "/production"]);
    expect(screen.getByRole("link", { name: /Planning/ }).textContent).toContain("What to make and what to order");
  });

  it("Plant Analytics shows no cards when the plan has no analytics features", async () => {
    const api = mockApi({ "/orgs/me": me(["boms"]) });
    const { client } = renderPage(<AnalyticsHub />);
    expect(screen.getByRole("heading", { name: "Plant Analytics" })).toBeTruthy();
    await waitFor(() => expect(client.getQueryData(["org-me"])).toBeTruthy());
    expect(api.calls.map((c) => c.path)).toEqual(["/orgs/me"]);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });

  it("Data & Reports and System show their ungated pages", async () => {
    mockApi({ "/orgs/me": me(["reports"]) });
    renderPage(<DataReportsHub />);
    await screen.findByText("Report Builder");
    expect(cardHrefs()).toEqual(["/datasets", "/bi", "/report-builder"]);
  });

  it("System lists integrations, realtime and alerts", async () => {
    mockApi({ "/orgs/me": me([]) });
    renderPage(<SystemHub />);
    expect(cardHrefs()).toEqual(["/integrations", "/realtime", "/alerts"]);
  });
});
