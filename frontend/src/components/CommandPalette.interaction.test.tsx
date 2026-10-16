// @vitest-environment jsdom
import { beforeAll, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderPage } from "@/test/renderPage";
import { ALL_NAV_ITEMS } from "@/lib/nav";
import { CommandPalette } from "./CommandPalette";

beforeAll(() => {
  // jsdom has no layout, so scrollIntoView is missing.
  Element.prototype.scrollIntoView = () => {};
});

function Landed() {
  return <p>landed on {useLocation().pathname}</p>;
}

function renderPalette(onClose = vi.fn()) {
  renderPage(
    <Routes>
      <Route path="/" element={<CommandPalette open onClose={onClose} />} />
      <Route path="*" element={<Landed />} />
    </Routes>,
  );
  return onClose;
}

const resultLabels = () =>
  screen.queryAllByRole("button").map((b) => b.querySelector("span")?.textContent);

describe("CommandPalette", () => {
  it("renders nothing while closed", () => {
    renderPage(<CommandPalette open={false} onClose={() => {}} />);
    expect(screen.queryByPlaceholderText("Go to page…")).toBeNull();
  });

  it("lists every page when the query is empty", () => {
    renderPalette();
    expect(resultLabels()).toHaveLength(ALL_NAV_ITEMS.length);
  });

  it("matches on keywords and section names, not just labels", async () => {
    renderPalette();
    await userEvent.type(screen.getByPlaceholderText("Go to page…"), "on hand");
    expect(resultLabels()).toEqual(["Warehouse"]);

    await userEvent.clear(screen.getByPlaceholderText("Go to page…"));
    await userEvent.type(screen.getByPlaceholderText("Go to page…"), "system");
    expect(resultLabels()).toEqual(["Integrations", "Realtime", "Alerts"]);
  });

  it("says when nothing matches", async () => {
    renderPalette();
    await userEvent.type(screen.getByPlaceholderText("Go to page…"), "zzzz");
    expect(screen.getByText("No pages match “zzzz”.")).toBeTruthy();
  });

  it("arrow keys move the selection and Enter navigates to it", async () => {
    const onClose = renderPalette();
    await userEvent.type(screen.getByPlaceholderText("Go to page…"), "system");
    await userEvent.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}{ArrowUp}{Enter}");
    // Down x3 clamps at the last of 3 results, Up lands on the middle one.
    expect(await screen.findByText("landed on /realtime")).toBeTruthy();
    expect(onClose).toHaveBeenCalled();
  });

  it("clicking a result navigates; Escape closes", async () => {
    const onClose = renderPalette();
    await userEvent.click(screen.getByPlaceholderText("Go to page…"));
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: /Datasets/ }));
    expect(await screen.findByText("landed on /datasets")).toBeTruthy();
  });
});
