import { useEffect, useState } from "react";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

interface StaffAuthState {
  accessToken: string | null;
  setToken: (accessToken: string) => void;
  clear: () => void;
}

// sessionStorage, same rationale as the customer authStore: ends when the tab
// closes, survives a refresh within the session.
export const useStaffAuthStore = create<StaffAuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      setToken: (accessToken) => set({ accessToken }),
      clear: () => set({ accessToken: null }),
    }),
    {
      name: "staff-auth",
      storage: createJSONStorage(() => sessionStorage),
    },
  ),
);

export function useStaffAuthHydrated(): boolean {
  const [hydrated, setHydrated] = useState(useStaffAuthStore.persist.hasHydrated());
  useEffect(() => {
    setHydrated(useStaffAuthStore.persist.hasHydrated());
    return useStaffAuthStore.persist.onFinishHydration(() => setHydrated(true));
  }, []);
  return hydrated;
}
