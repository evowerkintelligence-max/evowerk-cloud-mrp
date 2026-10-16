// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { mockApi, renderPage } from "@/test/renderPage";
import { VerifyEmail } from "./VerifyEmail";

describe("VerifyEmail", () => {
  it("posts the token and confirms a successful verification", async () => {
    const api = mockApi({ "POST /auth/verify": {} });
    renderPage(<VerifyEmail />, { route: "/verify-email?token=tok-1" });
    expect(screen.getByText("Verifying…")).toBeTruthy();

    expect(await screen.findByText("Your email is verified.")).toBeTruthy();
    expect(api.calls[0]).toMatchObject({ method: "POST", path: "/auth/verify", body: { token: "tok-1" } });
  });

  it("shows the invalid-link error without calling the API when the token is missing", async () => {
    const api = mockApi({});
    renderPage(<VerifyEmail />, { route: "/verify-email" });
    expect(await screen.findByText("This verification link is invalid or expired.")).toBeTruthy();
    expect(api.calls).toHaveLength(0);
  });

  it("shows the invalid-link error when the backend rejects the token", async () => {
    mockApi({ "POST /auth/verify": new Response("{}", { status: 400 }) });
    renderPage(<VerifyEmail />, { route: "/verify-email?token=bad" });
    expect(await screen.findByText("This verification link is invalid or expired.")).toBeTruthy();
  });

  it("shows the invalid-link error on a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    renderPage(<VerifyEmail />, { route: "/verify-email?token=tok-1" });
    expect(await screen.findByText("This verification link is invalid or expired.")).toBeTruthy();
  });
});
