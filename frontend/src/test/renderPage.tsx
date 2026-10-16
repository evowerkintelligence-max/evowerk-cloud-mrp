// Shared harness for interaction tests. Files using it start with
// `// @vitest-environment jsdom` (the default environment stays "node" so the
// existing renderToString tests don't pay for a DOM).
import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { cleanup, render } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { useAuthStore } from "@/store/authStore";

// vitest globals are off, so Testing Library can't register its own cleanup.
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useAuthStore.setState({ accessToken: null, refreshToken: null });
});

type Handler = unknown | ((req: { url: URL; method: string; body: unknown }) => unknown);

export interface ApiCall {
  method: string;
  path: string;
  search: string;
  body: unknown;
}

/** Stubs global fetch. Keys are "METHOD /path" (query string ignored) or
 * "METHOD /path?query" for an exact match; GET may omit the method. A handler
 * is a JSON body, a function returning one, or a Response. Unmatched calls get
 * a 404 and are still recorded in `calls`, so a test can assert on them. */
export function mockApi(routes: Record<string, Handler>) {
  const calls: ApiCall[] = [];
  const table = new Map<string, Handler>();
  for (const [k, v] of Object.entries(routes)) table.set(k.includes(" ") ? k : `GET ${k}`, v);

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(typeof input === "string" ? input : input.toString(), "http://localhost");
    const method = (init.method ?? "GET").toUpperCase();
    let body: unknown = init.body;
    if (typeof body === "string") {
      try {
        body = JSON.parse(body);
      } catch {
        // leave non-JSON bodies as-is
      }
    }
    calls.push({ method, path: url.pathname, search: url.search, body });
    const handler = table.has(`${method} ${url.pathname}${url.search}`)
      ? table.get(`${method} ${url.pathname}${url.search}`)
      : table.get(`${method} ${url.pathname}`);
    if (handler === undefined) {
      return new Response(JSON.stringify({ detail: `unmocked ${method} ${url.pathname}` }), { status: 404 });
    }
    const out = typeof handler === "function" ? await handler({ url, method, body }) : handler;
    if (out instanceof Response) return out;
    return new Response(JSON.stringify(out ?? null), {
      status: method === "POST" ? 201 : 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

/** Renders a page inside a fresh QueryClient and a MemoryRouter at `route`,
 * signed in with a dummy token. */
export function renderPage(ui: ReactElement, { route = "/" }: { route?: string } = {}) {
  useAuthStore.setState({ accessToken: "test-token", refreshToken: "test-refresh" });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
  });
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, client };
}
