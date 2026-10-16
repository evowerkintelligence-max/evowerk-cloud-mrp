// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { Sparkline } from "./Sparkline";
import { BreakdownBars } from "./BreakdownBars";

afterEach(cleanup);

describe("Sparkline", () => {
  it("draws nothing for fewer than two points", () => {
    const { container } = render(<Sparkline values={[5]} />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("scales points into the box, min at the bottom and max at the top", () => {
    const { container } = render(<Sparkline values={[0, 10, 5]} width={100} height={20} />);
    expect(container.querySelector("polyline")!.getAttribute("points")).toBe("0.0,20.0 50.0,0.0 100.0,10.0");
    // The fill area closes down to the baseline.
    expect(container.querySelector("polygon")!.getAttribute("points")).toBe("0.0,20.0 50.0,0.0 100.0,10.0 100.0,20 0,20");
  });

  it("draws a flat line for constant values instead of dividing by zero", () => {
    const { container } = render(<Sparkline values={[3, 3]} width={10} height={8} />);
    expect(container.querySelector("polyline")!.getAttribute("points")).toBe("0.0,8.0 10.0,8.0");
  });
});

describe("BreakdownBars", () => {
  it("renders nothing for no items", () => {
    const { container } = render(<BreakdownBars items={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("sizes each bar relative to the largest magnitude and labels its value", () => {
    const { container, getByText } = render(
      <BreakdownBars items={[{ label: "Jam", value: 40 }, { label: "Setup", value: -10 }, { label: "Other", value: 2.345 }]} />,
    );
    const widths = [...container.querySelectorAll<HTMLElement>("[style]")].map((b) => b.style.width);
    expect(widths).toEqual(["100%", "25%", `${(2.345 / 40) * 100}%`]);
    expect(getByText("Setup").getAttribute("title")).toBe("Setup");
    expect(getByText("-10")).toBeTruthy();
    expect(getByText("2.3")).toBeTruthy();
  });
});
