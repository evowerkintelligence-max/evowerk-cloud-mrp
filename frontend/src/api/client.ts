import { useAuthStore } from "../store/authStore";

// Shared across concurrent callers: if two requests 401 around the same time, they
// must not both try to redeem the same single-use refresh token (the second one
// would get a "revoked" error and log the user out even though the first succeeded).
let inFlightRefresh: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
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

function refreshTokens(): Promise<boolean> {
  if (!inFlightRefresh) {
    inFlightRefresh = doRefresh().finally(() => {
      inFlightRefresh = null;
    });
  }
  return inFlightRefresh;
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { accessToken } = useAuthStore.getState();
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

  let res = await fetch(path, { ...init, headers });

  if (res.status === 401 && (await refreshTokens())) {
    const retryHeaders = new Headers(init.headers);
    retryHeaders.set("Authorization", `Bearer ${useAuthStore.getState().accessToken}`);
    if (init.body && !(init.body instanceof FormData)) retryHeaders.set("Content-Type", "application/json");
    res = await fetch(path, { ...init, headers: retryHeaders });
  }

  return res;
}
