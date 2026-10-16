// Client error telemetry. Reports go to this deployment's own backend
// (POST /client-errors), which logs them and forwards to the customer's own
// Sentry if they configured one - never to a third-party service.

export type ClientErrorKind = "render" | "unhandled" | "rejection" | "api";

// An error loop (a render that throws on every retry, a failing poll) must not
// flood the backend: each distinct message is sent once, and at most
// MAX_REPORTS per page load.
const MAX_REPORTS = 20;
const sent = new Set<string>();

export function reportClientError(kind: ClientErrorKind, error: unknown, extra: { stack?: string } = {}): void {
  try {
    const message = (error instanceof Error ? `${error.name}: ${error.message}` : String(error)).slice(0, 1000);
    const key = `${kind}:${message}`;
    if (sent.has(key) || sent.size >= MAX_REPORTS) return;
    sent.add(key);
    const stack = extra.stack ?? (error instanceof Error ? error.stack : undefined);
    const body = JSON.stringify({ kind, message, stack: stack?.slice(0, 8000), url: location.pathname.slice(0, 500) });
    // sendBeacon survives page unload; fall back to fetch when it is missing or
    // its queue is full.
    if (navigator.sendBeacon?.("/client-errors", new Blob([body], { type: "application/json" }))) return;
    fetch("/client-errors", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    }).catch(() => {});
  } catch {
    // Telemetry must never be the thing that breaks the page.
  }
}

export function installGlobalErrorHandlers(): void {
  window.addEventListener("error", (e) => reportClientError("unhandled", e.error ?? e.message));
  window.addEventListener("unhandledrejection", (e) => reportClientError("rejection", e.reason));
}
