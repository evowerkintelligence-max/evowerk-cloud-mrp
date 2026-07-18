import { useEffect, useState } from "react";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  setTokens: (accessToken: string, refreshToken: string) => void;
  clear: () => void;
}

// sessionStorage (not localStorage) so a refresh or direct URL open doesn't
// bounce the user back to /login, but the session still ends when the tab
// closes rather than persisting indefinitely across devices/backups. Same
// XSS exposure as the in-memory-only approach this replaced - both are
// synchronously readable from JS - so this isn't a security regression.
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      setTokens: (accessToken, refreshToken) => set({ accessToken, refreshToken }),
      clear: () => set({ accessToken: null, refreshToken: null }),
    }),
    {
      name: "auth",
      storage: createJSONStorage(() => sessionStorage),
    },
  ),
);

// persist rehydrates asynchronously (even from a synchronous storage) - a
// guard that renders nothing until this reports true avoids RequireAuth
// reading a still-null accessToken on the first render and redirecting to
// /login before sessionStorage's saved session has actually loaded.
export function useAuthHydrated(): boolean {
  const [hydrated, setHydrated] = useState(useAuthStore.persist.hasHydrated());
  useEffect(() => {
    setHydrated(useAuthStore.persist.hasHydrated());
    return useAuthStore.persist.onFinishHydration(() => setHydrated(true));
  }, []);
  return hydrated;
}
