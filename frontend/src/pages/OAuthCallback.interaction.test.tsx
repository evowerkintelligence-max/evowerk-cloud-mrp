// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { useAuthStore } from "@/store/authStore";
import { OAuthCallback } from "./OAuthCallback";
import { OAuthMfaVerify } from "./OAuthMfaVerify";

// OAuthCallback reads tokens from the real window.location fragment. Rendered
// without renderPage so the session starts signed out (no dummy token).
function renderCallback(hash: string) {
  window.history.replaceState(null, "", `/oauth/callback${hash}`);
  render(
    <MemoryRouter initialEntries={["/oauth/callback"]}>
      <Routes>
        <Route path="/oauth/callback" element={<OAuthCallback />} />
        <Route path="/oauth/mfa-verify" element={<OAuthMfaVerify />} />
        <Route path="/" element={<p>home page</p>} />
        <Route path="/login" element={<p>login page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => window.history.replaceState(null, "", "/"));

describe("OAuthCallback + OAuthMfaVerify", () => {
  it("stores tokens from the URL fragment and goes home", async () => {
    mockApi({});
    renderCallback("#access_token=oa-acc&refresh_token=oa-ref");
    await screen.findByText("home page");
    expect(useAuthStore.getState()).toMatchObject({ accessToken: "oa-acc", refreshToken: "oa-ref" });
  });

  it("sends the user back to log in when the fragment has no tokens", async () => {
    mockApi({});
    renderCallback("");
    await screen.findByText("login page");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("hands an MFA pending token to the verify page, which posts it with the code", async () => {
    const api = mockApi({ "POST /auth/oauth/mfa-verify": { access_token: "mfa-acc", refresh_token: "mfa-ref" } });
    renderCallback("#mfa_pending_token=pend-1");
    await userEvent.type(await screen.findByLabelText("Authenticator code"), "654321");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));

    await screen.findByText("home page");
    expect(api.calls[0].body).toEqual({ pending_token: "pend-1", code: "654321" });
    expect(useAuthStore.getState()).toMatchObject({ accessToken: "mfa-acc", refreshToken: "mfa-ref" });
  });

  it("shows the server error on a wrong MFA code and stays signed out", async () => {
    mockApi({
      "POST /auth/oauth/mfa-verify": new Response(JSON.stringify({ detail: "Code expired" }), { status: 400 }),
    });
    renderCallback("#mfa_pending_token=pend-1");
    await userEvent.type(await screen.findByLabelText("Authenticator code"), "000000");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));

    expect((await screen.findByRole("alert")).textContent).toContain("Code expired");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("the verify page without a pending token asks the user to sign in again", () => {
    mockApi({});
    renderPage(<OAuthMfaVerify />, { route: "/oauth/mfa-verify" });
    expect(screen.getByText("Invalid link")).toBeTruthy();
    expect(screen.queryByLabelText("Authenticator code")).toBeNull();
  });
});
