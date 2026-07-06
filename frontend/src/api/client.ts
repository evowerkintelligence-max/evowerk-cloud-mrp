import { useAuthStore } from "../store/authStore";

async function refreshTokens(): Promise<boolean> {
  const { refreshToken, setTokens, clear } = useAuthStore.getState();
  if (!refreshToken) return false;

  const res = await fetch("/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) {
    clear();
    return false;
  }
  const data = await res.json();
  setTokens(data.access_token, data.refresh_token);
  return true;
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { accessToken } = useAuthStore.getState();
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body) headers.set("Content-Type", "application/json");

  let res = await fetch(path, { ...init, headers });

  if (res.status === 401 && (await refreshTokens())) {
    const retryHeaders = new Headers(init.headers);
    retryHeaders.set("Authorization", `Bearer ${useAuthStore.getState().accessToken}`);
    if (init.body) retryHeaders.set("Content-Type", "application/json");
    res = await fetch(path, { ...init, headers: retryHeaders });
  }

  return res;
}
