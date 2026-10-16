// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { StatCard } from "./StatCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("StatCard", () => {
  it("default size shows label, value, hint and the explanatory tooltip", () => {
    render(<StatCard label="Scrap" value="4.2%" hint="vs 3.9% last week" title="Rejected units / total" tone="warning" />);
    expect(screen.getByText("Scrap").getAttribute("title")).toBe("Rejected units / total");
    expect(screen.getByText("4.2%")).toBeTruthy();
    expect(screen.getByText("vs 3.9% last week")).toBeTruthy();
  });

  it("hero size formats the hero number and shows the delta", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q }));
    render(
      <StatCard
        size="hero"
        label="OEE"
        value="ignored"
        heroValue={72.46}
        heroFormat={(n) => `${n.toFixed(1)}%`}
        heroColor="rgb(1, 2, 3)"
        delta="+2.1%"
        hint="Target 85%"
        icon={<svg data-testid="icon" />}
      />,
    );
    const hero = screen.getByText("72.5%");
    expect(hero.style.color).toBe("rgb(1, 2, 3)");
    expect(screen.getByText("+2.1%")).toBeTruthy();
    expect(screen.getByText("Target 85%")).toBeTruthy();
    expect(screen.getByTestId("icon")).toBeTruthy();
    expect(screen.queryByText("ignored")).toBeNull();
  });

  it("hero size without heroValue falls back to the static value", () => {
    render(<StatCard size="hero" label="Lines" value="12 running" />);
    expect(screen.getByText("12 running")).toBeTruthy();
  });

  it("hero number counts up to its value when motion is allowed", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q }));
    let now = 0;
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
    vi.stubGlobal("cancelAnimationFrame", () => {});
    vi.spyOn(performance, "now").mockImplementation(() => now);

    const { rerender } = render(<StatCard size="hero" label="Units" value="" heroValue={0} />);
    rerender(<StatCard size="hero" label="Units" value="" heroValue={100} />);

    const step = (t: number) =>
      act(() => {
        now = t;
        frames.splice(0).forEach((f) => f(t));
      });
    step(300);
    const mid = Number(screen.getByText(/^\d+$/).textContent);
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(100);
    step(600);
    expect(screen.getByText("100")).toBeTruthy();
    vi.restoreAllMocks();
  });
});
