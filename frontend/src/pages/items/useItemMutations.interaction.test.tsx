// @vitest-environment jsdom
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { mockApi } from "@/test/renderPage";
import { useAuthStore } from "@/store/authStore";
import { useItemMutations } from "./useItemMutations";

function setup(response: () => Response) {
  useAuthStore.setState({ accessToken: "test-token", refreshToken: "test-refresh" });
  mockApi({ "PATCH /items/i1": response });
  const setError = vi.fn();
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => useItemMutations(setError), { wrapper });
  return { result, setError };
}

const base = { itemId: "i1", preferredSupplierId: null };
type Hook = ReturnType<typeof useItemMutations>;
const calls: [string, (h: Hook) => void][] = [
  ["Failed to update preferred supplier", (h) => h.setPreferredSupplier.mutate({ itemId: "i1", supplierId: null })],
  ["Failed to update lead time", (h) => h.setLeadTime.mutate({ ...base, leadTimeDays: 3 })],
  ["Failed to update MOQ", (h) => h.setMoq.mutate({ ...base, minOrderQty: "5" })],
  ["Failed to update safety stock", (h) => h.setSafetyStock.mutate({ ...base, safetyStockQty: "5" })],
  ["Failed to update status", (h) => h.setActive.mutate({ ...base, isActive: false })],
  ["Failed to update item", (h) => h.patchItemField.mutate({ ...base, field: "name", value: "x" })],
];

describe("useItemMutations errors", () => {
  it.each(calls)("surfaces the server detail, else falls back to %s", async (fallback, run) => {
    const withDetail = setup(() => new Response(JSON.stringify({ detail: "nope" }), { status: 400 }));
    run(withDetail.result.current);
    await waitFor(() => expect(withDetail.setError).toHaveBeenCalledWith("nope"));

    const noBody = setup(() => new Response("<html>", { status: 500 }));
    run(noBody.result.current);
    await waitFor(() => expect(noBody.setError).toHaveBeenCalledWith(fallback));
  });
});
