// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Ask } from "./Ask";

// echarts needs canvas, which jsdom lacks.
vi.mock("@/pages/analytics/shared/EChart", () => ({ EChart: () => null }));

beforeEach(() => {
  // PageHeader's Typewriter reads matchMedia; reduced motion renders the full text at once.
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  // jsdom elements have no scrollTo; the page auto-scrolls the chat on each turn.
  Element.prototype.scrollTo = vi.fn();
});

const DATASETS = [
  { id: "d1", name: "Scrap log", original_filename: "s.csv", fmt: "csv", row_count: 2500, size_bytes: 1, created_at: "", columns: [] },
];

const ANSWER = {
  answer: "Average scrap is 4.2%.",
  chart: { title: "Scrap by month", type: "bar", x_labels: ["Sep", "Oct"], series: [{ name: "scrap", data: [4, 4.4] }, { name: "target", data: [3, 3] }] },
  table: { columns: ["month", "scrap", "note"], rows: [["Sep", 4, null], ["Oct", 4.4, ""]] },
  follow_ups: ["Which machine is worst?"],
  datasets_used: ["Scrap log"],
};

const posts = (calls: { method: string; body: unknown }[]) => calls.filter((c) => c.method === "POST").map((c) => c.body);

describe("Ask", () => {
  it("sends a question and renders the answer with chart, table, source and follow-ups", async () => {
    const api = mockApi({ "/datasets": DATASETS, "POST /data-chat/message": ANSWER });
    renderPage(<Ask />);
    expect(screen.getByText("Ask anything about this data")).toBeTruthy();
    const ask = screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement;
    expect(ask.disabled).toBe(true);

    await userEvent.type(screen.getByPlaceholderText("Ask about this data…"), "average scrap?");
    await userEvent.click(ask);

    expect(await screen.findByText("Average scrap is 4.2%.")).toBeTruthy();
    expect(screen.getByText("average scrap?")).toBeTruthy();
    expect(posts(api.calls)[0]).toEqual({ question: "average scrap?", history: [], dataset_ids: null });
    expect(screen.getByText("Scrap by month")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "note" })).toBeTruthy();
    expect(screen.getAllByText("—")).toHaveLength(2); // null and "" cells
    expect(screen.getByText("Source: Scrap log")).toBeTruthy();
    expect((screen.getByPlaceholderText("Ask about this data…") as HTMLInputElement).value).toBe("");

    // a follow-up chip sends the next question with the conversation so far
    await userEvent.click(screen.getByRole("button", { name: "Which machine is worst?" }));
    await waitFor(() => expect(posts(api.calls)).toHaveLength(2));
    expect(posts(api.calls)[1]).toEqual({
      question: "Which machine is worst?",
      history: [{ role: "user", content: "average scrap?" }, { role: "assistant", content: "Average scrap is 4.2%." }],
      dataset_ids: null,
    });
  });

  it("scopes to one dataset, and switching source clears the chat", async () => {
    const api = mockApi({
      "/datasets": DATASETS,
      "POST /data-chat/message": { ...ANSWER, chart: { ...ANSWER.chart, type: "pie", title: "" }, table: null, follow_ups: [], datasets_used: [] },
    });
    renderPage(<Ask />);
    await screen.findByRole("option", { name: "Scrap log (2,500 rows)" });
    await userEvent.selectOptions(screen.getByRole("combobox"), "d1");
    await userEvent.type(screen.getByPlaceholderText("Ask about this data…"), "share by line{enter}");

    expect(await screen.findByText("Average scrap is 4.2%.")).toBeTruthy();
    expect(posts(api.calls)[0]).toMatchObject({ dataset_ids: ["d1"] });
    expect(screen.queryByText(/^Source:/)).toBeNull();

    await userEvent.selectOptions(screen.getByRole("combobox"), "__all__");
    expect(screen.getByText("Ask anything about this data")).toBeTruthy();
  });

  it("New chat clears the conversation", async () => {
    mockApi({ "/datasets": DATASETS, "POST /data-chat/message": { ...ANSWER, chart: { ...ANSWER.chart, type: "line" } } });
    renderPage(<Ask />);
    await userEvent.type(screen.getByPlaceholderText("Ask about this data…"), "trend{enter}");
    await screen.findByText("Average scrap is 4.2%.");
    await userEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByText("Average scrap is 4.2%.")).toBeNull();
    expect(screen.queryByRole("button", { name: "New chat" })).toBeNull();
  });

  it("shows the API error, with a fallback when the body isn't JSON", async () => {
    mockApi({ "/datasets": DATASETS, "POST /data-chat/message": new Response("gateway down", { status: 502 }) });
    renderPage(<Ask />);
    await userEvent.type(screen.getByPlaceholderText("Ask about this data…"), "hello{enter}");
    expect(await screen.findByText("Failed to answer question")).toBeTruthy();
  });
});
