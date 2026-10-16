// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import { Typewriter } from "./Typewriter";
import { PageTabs } from "./PageTabs";
import { Dialog } from "./ui/dialog";
import userEvent from "@testing-library/user-event";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const revealed = (container: HTMLElement) => container.querySelector("[aria-hidden]")!.textContent;

describe("Typewriter", () => {
  it("reveals the text one character per tick, keeping the full text laid out", () => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q }));
    const { container } = render(<Typewriter text="abc" speed={10} />);
    expect(container.querySelector(".opacity-0")!.textContent).toBe("abc");
    expect(revealed(container)).toBe("|");

    act(() => vi.advanceTimersByTime(10));
    expect(revealed(container)).toBe("a|");
    act(() => vi.advanceTimersByTime(10));
    act(() => vi.advanceTimersByTime(10));
    // Cursor disappears once the whole string is out.
    expect(revealed(container)).toBe("abc");
  });

  it("shows the whole text at once under prefers-reduced-motion", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce"), media: q }));
    const { container } = render(<Typewriter text="hello" />);
    expect(revealed(container)).toBe("hello");
  });
});

describe("PageTabs", () => {
  it("reports the clicked tab", async () => {
    const onChange = vi.fn();
    const { getByRole } = render(
      <PageTabs tabs={[{ value: "a", label: "Alpha" }, { value: "b", label: "Beta" }]} active="a" onChange={onChange} />,
    );
    await userEvent.click(getByRole("button", { name: "Beta" }));
    expect(onChange).toHaveBeenCalledWith("b");
  });
});

describe("Dialog", () => {
  it("renders nothing while closed", () => {
    const { queryByText } = render(<Dialog open={false} onClose={() => {}} title="Edit">body</Dialog>);
    expect(queryByText("Edit")).toBeNull();
  });

  it("shows title and body and closes from the X and the backdrop", async () => {
    const onClose = vi.fn();
    const { getByRole, getByText, container } = render(<Dialog open onClose={onClose} title="Edit">body text</Dialog>);
    expect(getByRole("heading", { name: "Edit" })).toBeTruthy();
    expect(getByText("body text")).toBeTruthy();
    await userEvent.click(getByRole("button", { name: "Close" }));
    await userEvent.click(container.querySelector(".backdrop-blur-sm")!);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
