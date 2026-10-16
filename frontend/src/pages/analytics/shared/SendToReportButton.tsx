import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../../api/client";
import { Button } from "@/components/ui/button";
import type { ReportSectionSpecPayload } from "./datasets";

export function SendToReportButton({ spec, label }: { spec: ReportSectionSpecPayload; label: string }) {
  const qc = useQueryClient();
  const send = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/reports/inbox", {
        method: "POST",
        body: JSON.stringify({ label, ...spec }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to send to report");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["report-inbox"] }),
  });

  if (send.isSuccess) {
    return <span className="text-sm text-muted-foreground">Sent to report inbox.</span>;
  }
  return (
    <div className="flex items-center gap-2">
      <Button variant="secondary" size="sm" loading={send.isPending} onClick={() => send.mutate()}>
        Send to report
      </Button>
      {send.isError && <span className="text-sm text-destructive">{(send.error as Error).message}</span>}
    </div>
  );
}
