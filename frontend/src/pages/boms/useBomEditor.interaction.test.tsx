// @vitest-environment jsdom
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { mockApi } from "@/test/renderPage";
import { useAuthStore } from "@/store/authStore";
import { useBomEditor } from "./useBomEditor";

const fail = (status: number, body = "<html>") => () => new Response(body, { status });

describe("useBomEditor error paths", () => {
  it("surfaces failed loads and failed creates", async () => {
    useAuthStore.setState({ accessToken: "test-token", refreshToken: "test-refresh" });
    mockApi({
      "GET /items": fail(500),
      "GET /boms": fail(500),
      "GET /boms/b1": fail(500),
      "GET /items/c1/units": fail(500),
      "POST /boms": fail(400, JSON.stringify({ detail: "duplicate BOM" })),
      "POST /items/c1/units": fail(500),
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    const { result } = renderHook(() => useBomEditor(), { wrapper });

    await waitFor(() => expect(result.current.items.error?.message).toBe("Failed to load items"));
    await waitFor(() => expect(result.current.boms.error?.message).toBe("Failed to load BOMs"));

    act(() => {
      result.current.setSelectedBomId("b1");
      result.current.setLineComponentId("c1");
    });
    await waitFor(() => expect(result.current.bomDetail.error?.message).toBe("Failed to load BOM"));
    await waitFor(() => expect(result.current.componentUnits.error?.message).toBe("Failed to load units"));

    act(() => result.current.createBom.mutate());
    await waitFor(() => expect(result.current.error).toBe("duplicate BOM"));
    act(() => result.current.createUnit.mutate());
    await waitFor(() => expect(result.current.error).toBe("Failed to create unit"));
  });
});
