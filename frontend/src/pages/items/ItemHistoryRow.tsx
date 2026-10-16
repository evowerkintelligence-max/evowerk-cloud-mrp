import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { TD, TR } from "@/components/ui/table";
import { humanize } from "@/lib/status";
import type { AuditLogEntry } from "./types";
import { TABLE_COLUMN_COUNT } from "./types";

export function ItemHistoryRow({ itemId }: { itemId: string }) {
  const history = useQuery<AuditLogEntry[]>({
    queryKey: ["item-audit-log", itemId],
    queryFn: async () => {
      const res = await apiFetch(`/items/${itemId}/audit-log`);
      if (!res.ok) throw new Error("Failed to load history");
      return res.json();
    },
  });

  return (
    <TR>
      <TD colSpan={TABLE_COLUMN_COUNT} className="bg-secondary/20">
        {history.isLoading ? (
          <span className="text-sm text-muted-foreground">Loading history...</span>
        ) : history.data && history.data.length > 0 ? (
          <ul className="space-y-1 text-sm">
            {history.data.map((entry, i) => (
              <li key={i} className="text-muted-foreground">
                <span className="font-medium text-foreground">
                  {humanize(entry.action.split(".").pop() ?? entry.action)}
                </span>
                {" by "}
                {entry.actor_email ?? "unknown"} — {new Date(entry.created_at).toLocaleString()}
              </li>
            ))}
          </ul>
        ) : (
          <span className="text-sm text-muted-foreground">No history yet.</span>
        )}
      </TD>
    </TR>
  );
}
