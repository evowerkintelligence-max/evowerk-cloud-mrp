// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { useAuthStore } from "@/store/authStore";
import { useStaffAuthStore } from "@/store/staffAuthStore";
import { Login } from "./Login";

const EMAIL = "test@example.com";
const PASSWORD = "Test-password-123"; // pragma: allowlist secret

function renderLogin() {
  renderPage(
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<p>home page</p>} />
      <Route path="/staff" element={<p>staff page</p>} />
    </Routes>,
    { route: "/login" },
  );
  // Start signed out: renderPage seeds a dummy session.
  useAuthStore.setState({ accessToken: null, refreshToken: null });
}

async function fillCredentials() {
  await userEvent.type(screen.getByLabelText("Email"), EMAIL);
  await userEvent.type(screen.getByLabelText(/^Password/), PASSWORD);
}

afterEach(() => useStaffAuthStore.setState({ accessToken: null }));

describe("Login", () => {
  it("posts credentials, stores the returned tokens and goes home", async () => {
    const api = mockApi({
      "/auth/providers": { google: false, microsoft: false },
      "POST /auth/login": { access_token: "acc-1", refresh_token: "ref-1" },
    });
    renderLogin();
    await fillCredentials();
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));

    await screen.findByText("home page");
    expect(api.calls.find((c) => c.path === "/auth/login")!.body).toEqual({ email: EMAIL, password: PASSWORD });
    expect(useAuthStore.getState()).toMatchObject({ accessToken: "acc-1", refreshToken: "ref-1" });
  });

  it("asks for an authenticator code when MFA is required and sends it on the second submit", async () => {
    let n = 0;
    const api = mockApi({
      "/auth/providers": { google: false, microsoft: false },
      "POST /auth/login": () => (++n === 1 ? { mfa_required: true } : { access_token: "acc-2", refresh_token: "ref-2" }),
    });
    renderLogin();
    await fillCredentials();
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));

    const code = await screen.findByLabelText("Authenticator code");
    expect(useAuthStore.getState().accessToken).toBeNull();
    await userEvent.type(code, "123456");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));

    await screen.findByText("home page");
    const logins = api.calls.filter((c) => c.path === "/auth/login");
    expect(logins[1].body).toEqual({ email: EMAIL, password: PASSWORD, totp_code: "123456" });
    expect(useAuthStore.getState().accessToken).toBe("acc-2");
  });

  it("shows the server's error detail and stays signed out on a failed login", async () => {
    mockApi({
      "/auth/providers": { google: false, microsoft: false },
      "POST /auth/login": new Response(JSON.stringify({ detail: "Invalid credentials" }), { status: 400 }),
    });
    renderLogin();
    await fillCredentials();
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));

    expect((await screen.findByRole("alert")).textContent).toContain("Invalid credentials");
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(screen.queryByText("home page")).toBeNull();
  });

  it("offers only the OAuth providers the backend reports as configured", async () => {
    mockApi({ "/auth/providers": { google: true, microsoft: false } });
    renderLogin();
    const google = await screen.findByRole("button", { name: "Continue with Google" });
    expect(google.closest("a")!.getAttribute("href")).toBe("/auth/google/authorize");
    expect(screen.queryByRole("button", { name: "Continue with Microsoft" })).toBeNull();
  });

  it("requires an email before resending verification, then posts it and confirms", async () => {
    const api = mockApi({ "/auth/providers": { google: false, microsoft: false }, "POST /auth/request-verify-token": {} });
    renderLogin();
    await userEvent.click(screen.getByRole("button", { name: "Resend verification email" }));
    expect(screen.getByRole("alert").textContent).toContain("Enter your email above first");
    expect(api.calls.some((c) => c.path === "/auth/request-verify-token")).toBe(false);

    await userEvent.type(screen.getByLabelText("Email"), EMAIL);
    await userEvent.click(screen.getByRole("button", { name: "Resend verification email" }));

    expect(await screen.findByText(/a new verification link is on its way/)).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/auth/request-verify-token")!.body).toEqual({ email: EMAIL });
  });

  it("staff mode posts the employee id, stores the staff token and opens the staff portal", async () => {
    const api = mockApi({
      "/auth/providers": { google: true, microsoft: true },
      "POST /staff/auth/login": { access_token: "staff-acc" },
    });
    renderLogin();
    await userEvent.click(screen.getByRole("button", { name: "Staff Login" }));
    expect(screen.queryByLabelText("Email")).toBeNull();
    expect(screen.queryByRole("button", { name: "Continue with Google" })).toBeNull();

    await userEvent.type(screen.getByLabelText("Employee ID"), "E-100");
    await userEvent.type(screen.getByLabelText("Password"), PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));

    await screen.findByText("staff page");
    expect(api.calls.find((c) => c.path === "/staff/auth/login")!.body).toEqual({ employee_id: "E-100", password: PASSWORD });
    expect(useStaffAuthStore.getState().accessToken).toBe("staff-acc");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("staff mode shows the error and can switch back to customer login", async () => {
    mockApi({
      "/auth/providers": { google: false, microsoft: false },
      "POST /staff/auth/login": new Response("not json", { status: 500 }),
    });
    renderLogin();
    await userEvent.click(screen.getByRole("button", { name: "Staff Login" }));
    await userEvent.type(screen.getByLabelText("Employee ID"), "E-100");
    await userEvent.type(screen.getByLabelText("Password"), PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));

    expect((await screen.findByRole("alert")).textContent).toContain("Login failed");
    expect(useStaffAuthStore.getState().accessToken).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Back to customer login" }));
    expect(await screen.findByLabelText("Email")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
