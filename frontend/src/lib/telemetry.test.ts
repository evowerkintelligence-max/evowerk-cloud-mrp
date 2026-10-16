// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mockApi } from "@/test/renderPage";

// Fresh module per test: the dedupe set and report cap are per page load.
let telemetry: typeof import("./telemetry");
beforeEach(async () => {
  vi.resetModules();
  telemetry = await import("./telemetry");
});
afterEach(() => {
  delete (navigator as { sendBeacon?: unknown }).sendBeacon;
});

const reports = (calls: { path: string; body: unknown }[]) =>
  calls.filter((c) => c.path === "/client-errors").map((c) => c.body as Record<string, string>);

describe("reportClientError", () => {
  it("falls back to a keepalive fetch when sendBeacon is missing (jsdom)", () => {
    const { calls, fetchMock } = mockApi({ "POST /client-errors": new Response(null, { status: 204 }) });
    telemetry.reportClientError("unhandled", new TypeError("x is undefined"));
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST", keepalive: true });
    expect(reports(calls)).toEqual([
      expect.objectContaining({ kind: "unhandled", message: "TypeError: x is undefined", url: "/" }),
    ]);
    expect(reports(calls)[0].stack).toContain("x is undefined");
  });

  it("uses sendBeacon when available, and fetch only if the beacon is refused", () => {
    const { fetchMock } = mockApi({});
    const sendBeacon = vi.fn().mockReturnValueOnce(true).mockReturnValueOnce(false);
    Object.assign(navigator, { sendBeacon });
    telemetry.reportClientError("rejection", "first");
    expect(sendBeacon).toHaveBeenCalledWith("/client-errors", expect.any(Blob));
    expect(fetchMock).not.toHaveBeenCalled();
    telemetry.reportClientError("rejection", "second");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("sends each distinct message once and caps reports per page load", () => {
    const { calls } = mockApi({});
    for (let i = 0; i < 5; i++) telemetry.reportClientError("api", "same");
    for (let i = 0; i < 50; i++) telemetry.reportClientError("api", `err ${i}`);
    const sent = reports(calls);
    expect(sent.filter((r) => r.message === "same")).toHaveLength(1);
    expect(sent).toHaveLength(20);
  });

  it("truncates to the backend's size limits and never throws", () => {
    const { calls } = mockApi({});
    telemetry.reportClientError("api", "m".repeat(5000), { stack: "s".repeat(20000) });
    expect(reports(calls)[0].message).toHaveLength(1000);
    expect(reports(calls)[0].stack).toHaveLength(8000);

    vi.stubGlobal("fetch", () => {
      throw new Error("fetch is broken");
    });
    expect(() => telemetry.reportClientError("api", "another")).not.toThrow();
  });
});

describe("installGlobalErrorHandlers", () => {
  it("reports uncaught errors and unhandled rejections", () => {
    const { calls } = mockApi({});
    telemetry.installGlobalErrorHandlers();
    window.dispatchEvent(new ErrorEvent("error", { error: new Error("kaboom") }));
    const rejection = new Event("unhandledrejection") as Event & { reason?: unknown };
    rejection.reason = "nope";
    window.dispatchEvent(rejection);
    expect(reports(calls).map((r) => [r.kind, r.message])).toEqual([
      ["unhandled", "Error: kaboom"],
      ["rejection", "nope"],
    ]);
  });
});
