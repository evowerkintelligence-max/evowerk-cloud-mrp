// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { ResetPassword } from "./ResetPassword";

const PASSWORD = "Test-password-123"; // pragma: allowlist secret

function renderAt(route: string) {
  renderPage(
    <Routes>
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/login" element={<p>login page</p>} />
    </Routes>,
    { route },
  );
}

describe("ResetPassword", () => {
  it("explains a link without a token instead of showing the form", () => {
    mockApi({});
    renderAt("/reset-password");
    expect(screen.getByText("Invalid link")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Request a new link" }).getAttribute("href")).toBe("/forgot-password");
    expect(screen.queryByLabelText("New password")).toBeNull();
  });

  it("posts the token from the URL with the new password, then goes to log in", async () => {
    const api = mockApi({ "POST /auth/reset-password": {} });
    renderAt("/reset-password?token=tok-123");
    await userEvent.type(screen.getByLabelText("New password"), PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Reset password" }));

    await screen.findByText("login page");
    expect(api.calls[0].body).toEqual({ token: "tok-123", password: PASSWORD });
  });

  it("falls back to an expiry message when the error body has no detail", async () => {
    mockApi({ "POST /auth/reset-password": new Response("oops", { status: 400 }) });
    renderAt("/reset-password?token=tok-123");
    await userEvent.type(screen.getByLabelText("New password"), PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Reset password" }));

    expect((await screen.findByRole("alert")).textContent).toContain("The link may have expired.");
  });
});
