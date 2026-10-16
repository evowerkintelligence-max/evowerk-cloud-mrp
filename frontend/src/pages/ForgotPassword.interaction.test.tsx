// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { ForgotPassword } from "./ForgotPassword";

describe("ForgotPassword", () => {
  it("posts the email and shows the same confirmation even when the backend errors", async () => {
    const api = mockApi({ "POST /auth/forgot-password": new Response("{}", { status: 404 }) });
    renderPage(<ForgotPassword />);
    await userEvent.type(screen.getByLabelText("Email"), "test@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByText("Check your email")).toBeTruthy();
    expect(api.calls[0]).toMatchObject({ method: "POST", path: "/auth/forgot-password", body: { email: "test@example.com" } });
  });

  it("surfaces a network failure distinctly and keeps the form", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    renderPage(<ForgotPassword />);
    await userEvent.type(screen.getByLabelText("Email"), "test@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    expect((await screen.findByRole("alert")).textContent).toContain("Something went wrong");
    expect(screen.queryByText("Check your email")).toBeNull();
  });
});
