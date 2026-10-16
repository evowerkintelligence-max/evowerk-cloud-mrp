// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { Register } from "./Register";

const PASSWORD = "Test-password-123"; // pragma: allowlist secret

function renderRegister() {
  renderPage(
    <Routes>
      <Route path="/register" element={<Register />} />
      <Route path="/login" element={<p>login page</p>} />
    </Routes>,
    { route: "/register" },
  );
}

async function fillForm() {
  await userEvent.type(screen.getByLabelText("Organization name"), "Test Org");
  await userEvent.type(screen.getByLabelText("Email"), "test@example.com");
  await userEvent.type(screen.getByLabelText("Password"), PASSWORD);
  await userEvent.click(screen.getByRole("button", { name: "Register" }));
}

describe("Register", () => {
  it("posts the org, email and password and sends the user to log in", async () => {
    const api = mockApi({ "POST /auth/register": {} });
    renderRegister();
    await fillForm();

    await screen.findByText("login page");
    expect(api.calls.find((c) => c.path === "/auth/register")!.body).toEqual({
      org_name: "Test Org",
      email: "test@example.com",
      password: PASSWORD,
    });
  });

  it("joins pydantic validation messages into the error and stays on the form", async () => {
    mockApi({
      "POST /auth/register": new Response(
        JSON.stringify({ detail: [{ msg: "email taken" }, { msg: "password too weak" }] }),
        { status: 422 },
      ),
    });
    renderRegister();
    await fillForm();

    expect((await screen.findByRole("alert")).textContent).toContain("email taken; password too weak");
    expect(screen.queryByText("login page")).toBeNull();
  });
});
