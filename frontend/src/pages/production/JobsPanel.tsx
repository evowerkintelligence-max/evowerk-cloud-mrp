import { useState } from "react";
import { Factory } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Table, TBody, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useCurrentUser } from "@/lib/currentUser";
import { EditDialog } from "./jobs/EditDialog";
import { FinishDialog } from "./jobs/FinishDialog";
import { JobRow } from "./jobs/JobRow";
import { RaiseDialog } from "./jobs/RaiseDialog";
import type { WorkOrder } from "./types";

export function JobsPanel() {
  const queryClient = useQueryClient();
  const currentUser = useCurrentUser();
  const canEdit = currentUser.data?.role === "owner" || currentUser.data?.role === "manager";

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [finishing, setFinishing] = useState<WorkOrder | null>(null);
  const [editing, setEditing] = useState<WorkOrder | null>(null);
  const [raising, setRaising] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState<WorkOrder | null>(null);

  // The list endpoint returns each job's components, so the rows need no
  // per-row fetch of their own.
  const jobs = useQuery<WorkOrder[]>({
    queryKey: ["work-orders", search, statusFilter],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (search) params.set("q", search);
      if (statusFilter) params.set("status", statusFilter);
      const res = await apiFetch(`/work-orders?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load jobs");
      return res.json();
    },
  });

  // Every one of these moves stock or supply, so the planning grid is stale
  // after any of them.
  function refresh() {
    queryClient.invalidateQueries({ queryKey: ["work-orders"] });
    queryClient.invalidateQueries({ queryKey: ["planning-grid"] });
  }

  const startJob = useMutation({
    mutationFn: async (job: WorkOrder) => {
      const res = await apiFetch(`/work-orders/${job.id}/start`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        // The backend's 409 names the short component, the location and the
        // job holding the stock, and is written to be read by the user.
        throw new Error(body.detail ?? "Failed to start the job");
      }
      return res.json();
    },
    onSuccess: refresh,
    onError: (e: Error) => setError(e.message),
  });

  const cancelJob = useMutation({
    mutationFn: async (job: WorkOrder) => {
      const res = await apiFetch(`/work-orders/${job.id}/cancel`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to cancel the job");
      }
      return res.json();
    },
    onSuccess: () => {
      setConfirmCancel(null);
      refresh();
    },
    onError: (e: Error) => {
      setConfirmCancel(null);
      setError(e.message);
    },
  });

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}
      {jobs.isError && (
        <Alert variant="destructive" className="mb-6">
          {(jobs.error as Error).message}
        </Alert>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-4">
        {canEdit && (
          <Button size="sm" onClick={() => setRaising(true)}>
            New job
          </Button>
        )}
        <Input
          placeholder="Search jobs…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <div className="w-40">
          <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All statuses</option>
            <option value="scheduled">Scheduled</option>
            <option value="in_progress">In progress</option>
            <option value="done">Done</option>
            <option value="cancelled">Cancelled</option>
          </Select>
        </div>
      </div>

      {jobs.data && jobs.data.length === 0 ? (
        <EmptyState
          icon={<Factory />}
          title="No jobs yet"
          description="Raise one here to schedule it for a date, or press Make on a product in Planning to start one now."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Job</TH>
                <TH>Product</TH>
                <TH>Planned</TH>
                <TH>Start date</TH>
                <TH>Status</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {jobs.data?.map((job) => (
                <JobRow
                  key={job.id}
                  job={job}
                  canEdit={canEdit}
                  starting={startJob.isPending}
                  onStart={() => {
                    setError(null);
                    startJob.mutate(job);
                  }}
                  onFinish={() => setFinishing(job)}
                  onEdit={() => setEditing(job)}
                  onCancel={() => setConfirmCancel(job)}
                />
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}

      <RaiseDialog
        key={`raise-${raising}`}
        open={raising}
        onClose={() => setRaising(false)}
        onDone={() => {
          setRaising(false);
          refresh();
        }}
        onError={(message) => {
          setRaising(false);
          setError(message);
        }}
      />

      {/* Keyed on the job so each dialog's fields initialise from the job the
          user actually opened, instead of sticking at the first one's. */}
      <FinishDialog
        key={`finish-${finishing?.id ?? "none"}`}
        job={finishing}
        onClose={() => setFinishing(null)}
        onDone={() => {
          setFinishing(null);
          refresh();
        }}
        onError={(message) => {
          setFinishing(null);
          setError(message);
        }}
      />

      <EditDialog
        key={`edit-${editing?.id ?? "none"}`}
        job={editing}
        onClose={() => setEditing(null)}
        onDone={() => {
          setEditing(null);
          refresh();
        }}
        onError={(message) => {
          setEditing(null);
          setError(message);
        }}
      />

      <Dialog
        open={confirmCancel !== null}
        onClose={() => setConfirmCancel(null)}
        title="Call this job off?"
      >
        <p className="text-sm text-muted-foreground">
          WO-{confirmCancel?.wo_number} will be cancelled and every component goes back on the
          shelf, as though the job never ran. If any work actually happened, finish it instead so
          you can say what was made.
        </p>
        <div className="flex justify-end gap-2 pt-4">
          <Button variant="secondary" onClick={() => setConfirmCancel(null)}>
            Never mind
          </Button>
          <Button
            variant="destructive"
            loading={cancelJob.isPending}
            onClick={() => confirmCancel && cancelJob.mutate(confirmCancel)}
          >
            Cancel job
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
