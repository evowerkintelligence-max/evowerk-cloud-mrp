// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { MfaChallenge } from "./MfaChallenge";

const ENROLL = { secret: "JBSWY3DPEHPK3PXP", provisioning_uri: "otpauth://totp/x", qr_svg: "<svg data-testid=\"qr\"></svg>" }; // pragma: allowlist secret

describe("MfaChallenge", () => {
  it("starts enrollment, shows the QR and secret, and confirms a valid code", async () => {
    const api = mockApi({ "POST /auth/mfa/enroll": ENROLL, "POST /auth/mfa/verify": {} });
    renderPage(<MfaChallenge />);

    expect(await screen.findByText(ENROLL.secret)).toBeTruthy();
    expect(screen.getByTestId("qr")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/auth/mfa/enroll")!.body).toEqual({});

    await userEvent.type(screen.getByLabelText("6-digit code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("MFA enabled.")).toBeTruthy();
    expect(api.calls.find((c) => c.path === "/auth/mfa/verify")!.body).toEqual({ code: "123456" });
  });

  it("tells the user when the code is rejected", async () => {
    mockApi({ "POST /auth/mfa/enroll": ENROLL, "POST /auth/mfa/verify": new Response("{}", { status: 400 }) });
    renderPage(<MfaChallenge />);
    await userEvent.type(await screen.findByLabelText("6-digit code"), "000000");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("Invalid code, try again.")).toBeTruthy();
    expect(screen.queryByText("MFA enabled.")).toBeNull();
  });

  it("shows an error when enrollment cannot start", async () => {
    mockApi({ "POST /auth/mfa/enroll": new Response("{}", { status: 500 }) });
    renderPage(<MfaChallenge />);
    expect(await screen.findByText("Could not start MFA enrollment.")).toBeTruthy();
  });

  it("asks for the password when MFA is already enabled (403)", async () => {
    mockApi({ "POST /auth/mfa/enroll": new Response("{}", { status: 403 }) });
    renderPage(<MfaChallenge />);
    expect(await screen.findByText("Confirm your password")).toBeTruthy();
    expect(screen.getByLabelText("Password")).toBeTruthy();
  });

  it("re-enrolls only after the full password is submitted", async () => {
    const api = mockApi({
      "POST /auth/mfa/enroll": ({ body }: { body: unknown }) =>
        (body as { password?: string }).password ? ENROLL : new Response("{}", { status: 403 }),
    });
    renderPage(<MfaChallenge />);
    const enrolls = () => api.calls.filter((c) => c.path === "/auth/mfa/enroll");
    await userEvent.type(await screen.findByLabelText("Password"), "hunter22"); // pragma: allowlist secret
    // typing fires nothing and keeps the password screen up
    expect(enrolls()).toHaveLength(1);
    expect(screen.getByText("Confirm your password")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText(ENROLL.secret)).toBeTruthy();
    expect(enrolls().map((c) => c.body)).toEqual([{}, { password: "hunter22" }]); // pragma: allowlist secret
    expect(screen.getByLabelText("6-digit code")).toBeTruthy();
  });
});
