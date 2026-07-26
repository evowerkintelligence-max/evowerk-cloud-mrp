import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";

export interface CurrentUser {
  id: string;
  name: string;
  role: string;
  user_id: string;
  enabled_features: string[];
}

/** GET /orgs/me - single source of truth for the current user/org, shared via
 * the ["org-me"] query key so the sidebar, command palette, route guards, and
 * analytics pages all dedupe to one fetch instead of each rolling their own. */
export function useCurrentUser() {
  return useQuery<CurrentUser>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load current user");
      return res.json();
    },
  });
}
