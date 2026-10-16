// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { useAuthStore } from "@/store/authStore";
import { Account } from "./Account";

const ME = { id: "o1", name: "Test Org", role: "owner", user_id: "u1", enabled_features: [] };
const PASSWORD = "Test-password-123"; // pragma: allowlist secret

// PageHeader's Typewriter reads matchMedia, which jsdom lacks.
beforeEach(() => {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q }));
});

function renderAccount() {
  renderPage(
    <Routes>
      <Route path="/account" element={<Account />} />
      <Route path="/login" element={<p>login page</p>} />
    </Routes>,
    { route: "/account" },
  );
}

async function submitDelete() {
  await userEvent.click(await screen.findByRole("button", { name: "Delete my account" }));
  await userEvent.type(screen.getByLabelText("Confirm your password"), PASSWORD);
  await userEvent.click(screen.getByRole("button", { name: "Permanently delete account" }));
}

describe("Account", () => {
  it("shows who is signed in", async () => {
    mockApi({ "/orgs/me": ME });
    renderAccount();
    expect(await screen.findByText("Test Org")).toBeTruthy();
  });

  it("deletes the account with the confirmed password, signs out and goes to log in", async () => {
    const api = mockApi({ "/orgs/me": ME, "DELETE /auth/me": {} });
    renderAccount();
    await submitDelete();

    await screen.findByText("login page");
    expect(api.calls.find((c) => c.method === "DELETE")).toMatchObject({ path: "/auth/me", body: { password: PASSWORD } });
    expect(useAuthStore.getState()).toMatchObject({ accessToken: null, refreshToken: null });
  });

  it("keeps the session and shows the error when deletion is refused", async () => {
    mockApi({
      "/orgs/me": ME,
      "DELETE /auth/me": new Response(JSON.stringify({ detail: "Wrong password" }), { status: 400 }),
    });
    renderAccount();
    await submitDelete();

    expect((await screen.findByRole("alert")).textContent).toContain("Wrong password");
    expect(useAuthStore.getState().accessToken).toBe("test-token");
  });

  it("cancel hides the confirmation form without calling the API", async () => {
    const api = mockApi({ "/orgs/me": ME });
    renderAccount();
    await userEvent.click(await screen.findByRole("button", { name: "Delete my account" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByLabelText("Confirm your password")).toBeNull();
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);
  });
});
