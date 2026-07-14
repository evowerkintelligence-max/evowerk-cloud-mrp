import { apiFetch } from "../api/client";

/**
 * Subscribe to a Bearer-authenticated Server-Sent Events endpoint.
 *
 * Native `EventSource` can't set an Authorization header, so this reads the
 * stream via fetch + a manual reader instead (this app has no cookie-based
 * session — every route, including SSE ones, expects a Bearer token).
 * Returns an unsubscribe function; call it on unmount to abort the fetch.
 */
export function subscribeToSSE<T>(path: string, onMessage: (data: T) => void, onError?: (err: unknown) => void): () => void {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await apiFetch(path, { signal: controller.signal });
      if (!res.ok || !res.body) throw new Error(`SSE request failed: ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          try {
            onMessage(JSON.parse(line.slice("data: ".length)) as T);
          } catch {
            // malformed frame - skip rather than tearing down the whole subscription
          }
        }
      }
    } catch (err) {
      if ((err as { name?: string }).name !== "AbortError") onError?.(err);
    }
  })();

  return () => controller.abort();
}
