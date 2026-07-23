import { useStaffAuthStore } from "@/store/staffAuthStore";

export async function staffApiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { accessToken } = useStaffAuthStore.getState();
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

  const res = await fetch(path, { ...init, headers });
  if (res.status === 401) useStaffAuthStore.getState().clear();
  return res;
}
