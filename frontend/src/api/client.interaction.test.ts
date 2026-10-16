// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mockApi } from "@/test/renderPage";
import { useAuthStore } from "@/store/authStore";
import { apiFetch } from "./client";

beforeEach(() => useAuthStore.setState({ accessToken: "old-acc", refreshToken: "old-ref" }));
afterEach(() => useAuthStore.setState({ accessToken: null, refreshToken: null }));

const authHeader = (init: RequestInit | undefined) => new Headers(init?.headers).get("Authorization");

describe("apiFetch", () => {
  it("sends the bearer token, and a JSON content type only for non-FormData bodies", async () => {
    const { fetchMock } = mockApi({ "POST /items": {}, "POST /datasets": {} });
    await apiFetch("/items", { method: "POST", body: JSON.stringify({ a: 1 }) });
    await apiFetch("/datasets", { method: "POST", body: new FormData() });

    const [[, jsonInit], [, formInit]] = fetchMock.mock.calls;
    expect(authHeader(jsonInit)).toBe("Bearer old-acc");
    expect(new Headers(jsonInit!.headers).get("Content-Type")).toBe("application/json");
    expect(new Headers(formInit!.headers).get("Content-Type")).toBeNull();
  });

  it("on a 401 redeems the refresh token once and retries with the new access token", async () => {
    const { calls, fetchMock } = mockApi({
      "/items": [],
      "POST /auth/refresh": { access_token: "new-acc", refresh_token: "new-ref" },
    });
    // First request 401s (bypassing the router, so it isn't in `calls`).
    fetchMock.mockImplementationOnce(async () => new Response("{}", { status: 401 }));
    const res = await apiFetch("/items");

    expect(res.status).toBe(200);
    expect(calls.map((c) => c.path)).toEqual(["/auth/refresh", "/items"]);
    expect(calls[0].body).toEqual({ refresh_token: "old-ref" });
    expect(authHeader(fetchMock.mock.calls[2][1])).toBe("Bearer new-acc");
    expect(useAuthStore.getState()).toMatchObject({ accessToken: "new-acc", refreshToken: "new-ref" });
  });

  it("concurrent 401s share a single refresh", async () => {
    let first = true;
    const { calls } = mockApi({
      "/a": () => (first ? new Response("{}", { status: 401 }) : {}),
      "/b": () => (first ? new Response("{}", { status: 401 }) : {}),
      "POST /auth/refresh": () => {
        first = false;
        return { access_token: "new-acc", refresh_token: "new-ref" };
      },
    });
    const [a, b] = await Promise.all([apiFetch("/a"), apiFetch("/b")]);
    expect([a.status, b.status]).toEqual([200, 200]);
    expect(calls.filter((c) => c.path === "/auth/refresh")).toHaveLength(1);
  });

  it("signs out and returns the 401 when the refresh is rejected", async () => {
    const { calls } = mockApi({
      "/items": new Response("{}", { status: 401 }),
      "POST /auth/refresh": new Response("{}", { status: 401 }),
    });
    const res = await apiFetch("/items");
    expect(res.status).toBe(401);
    expect(calls.map((c) => c.path)).toEqual(["/items", "/auth/refresh"]);
    expect(useAuthStore.getState()).toMatchObject({ accessToken: null, refreshToken: null });
  });

  it("does not try to refresh without a refresh token", async () => {
    useAuthStore.setState({ accessToken: null, refreshToken: null });
    const { calls, fetchMock } = mockApi({ "/items": new Response("{}", { status: 401 }) });
    const res = await apiFetch("/items");
    expect(res.status).toBe(401);
    expect(calls.map((c) => c.path)).toEqual(["/items"]);
    expect(authHeader(fetchMock.mock.calls[0][1])).toBeNull();
  });

  it("reports 5xx responses and network failures, but not 4xx", async () => {
    const { calls, fetchMock } = mockApi({
      "/boom": new Response("{}", { status: 503 }),
      "/missing": new Response("{}", { status: 404 }),
    });
    await apiFetch("/boom?x=1");
    await apiFetch("/missing");
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(apiFetch("/offline")).rejects.toThrow("Failed to fetch");

    const reported = calls.filter((c) => c.path === "/client-errors").map((c) => c.body);
    expect(reported).toEqual([
      expect.objectContaining({ kind: "api", message: "GET /boom -> 503" }),
      expect.objectContaining({ kind: "api", message: "TypeError: Failed to fetch" }),
    ]);
  });
});
