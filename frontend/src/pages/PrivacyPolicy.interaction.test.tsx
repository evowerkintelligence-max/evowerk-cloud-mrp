// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderPage } from "@/test/renderPage";
import { PrivacyPolicy } from "./PrivacyPolicy";

describe("PrivacyPolicy", () => {
  it("renders the policy sections and a link back into the app", () => {
    renderPage(<PrivacyPolicy />);
    const sections = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(sections).toEqual(expect.arrayContaining(["What we collect", "AI features", "Your rights", "Contact"]));
    expect(screen.getAllByRole("link")[0].getAttribute("href")).toBe("/");
    expect(screen.getByText(/Google Gemini/)).toBeTruthy();
  });
});
