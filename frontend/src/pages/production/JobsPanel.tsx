import { FormEvent, useState } from "react";
import { Factory } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { ItemSelect } from "@/components/ItemSelect";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { useCurrentUser } from "@/lib/currentUser";
import { humanize, woStatusVariant } from "@/lib/status";
import { trimQty } from "../planning/types";
import type { WorkOrder } from "./types";

/** A scheduled job whose start date has passed. Same treatment OrdersPanel
 * gives an overdue order. */
function isLate(job: WorkOrder): boolean {
  if (job.status !== "scheduled" || job.scheduled_start === null) return false;
  return job.scheduled_start < new Date().toISOString().slice(0, 10);
}

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

function JobRow({
  job,
  canEdit,
  starting,
  onStart,
  onFinish,
  onEdit,
  onCancel,
}: {
  job: WorkOrder;
  canEdit: boolean;
  starting: boolean;
  onStart: () => void;
  onFinish: () => void;
  onEdit: () => void;
  onCancel: () => void;
}) {
  return (
    <TR>
      <TD className="font-medium">WO-{job.wo_number}</TD>
      <TD>
        {job.item_name}
        <span className="block text-xs text-muted-foreground">{job.item_sku}</span>
      </TD>
      <TD>{trimQty(job.quantity_planned)}</TD>
      <TD className="text-muted-foreground">{job.scheduled_start ?? "Not scheduled"}</TD>
      <TD>
        <div className="flex flex-wrap items-center gap-1">
          <Badge variant={woStatusVariant(job.status)}>{humanize(job.status)}</Badge>
          {isLate(job) && <Badge variant="danger">Late</Badge>}
        </div>
      </TD>
      <TD className="text-right">
        {canEdit && (job.status === "scheduled" || job.status === "in_progress") && (
          <div className="flex justify-end gap-2">
            {job.status === "scheduled" && (
              <Button size="sm" loading={starting} onClick={onStart}>
                Start
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={onEdit}>
              Edit
            </Button>
            {job.status === "in_progress" && (
              <Button size="sm" onClick={onFinish}>
                Finish
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        )}
      </TD>
    </TR>
  );
}

function RaiseDialog({
  open,
  onClose,
  onDone,
  onError,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
  onError: (message: string) => void;
}) {
  const [itemId, setItemId] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [start, setStart] = useState("");
  const [notes, setNotes] = useState("");

  const items = useQuery<{ id: string; sku: string; name: string }[]>({
    queryKey: ["items"],
    enabled: open,
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  const raise = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/work-orders", {
        method: "POST",
        body: JSON.stringify({
          item_id: itemId,
          quantity_planned: quantity,
          scheduled_start: start || null,
          notes: notes || null,
          // Scheduled, not started. This is the only path that produces a job
          // holding stock for a future date; the grid's Make button always
          // starts immediately.
          start_now: false,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to raise the job");
      }
      return res.json();
    },
    onSuccess: onDone,
    onError: (e: Error) => onError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    raise.mutate();
  }

  return (
    <Dialog open={open} onClose={onClose} title="Raise a job">
      <form onSubmit={onSubmit} className="grid gap-4">
        <p className="text-sm text-muted-foreground">
          The job holds its materials from now until you start it, and the date decides which job
          wins when two need the same stock. Leave the date blank to mean as soon as possible.
        </p>
        <Field label="Product" htmlFor="raise-item">
          <ItemSelect
            id="raise-item"
            items={items.data}
            value={itemId}
            onChange={setItemId}
            required
          />
        </Field>
        <Field label="Quantity" htmlFor="raise-qty">
          <Input
            id="raise-qty"
            type="number"
            min="0"
            step="0.0001"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            required
          />
        </Field>
        <Field label="Start date" htmlFor="raise-start">
          <Input
            id="raise-start"
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </Field>
        <Field label="Notes" htmlFor="raise-notes">
          <Input id="raise-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Never mind
          </Button>
          <Button type="submit" loading={raise.isPending}>
            Raise job
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function EditDialog({
  job,
  onClose,
  onDone,
  onError,
}: {
  job: WorkOrder | null;
  onClose: () => void;
  onDone: () => void;
  onError: (message: string) => void;
}) {
  // Keyed on the job id at the call site, so these initialise from whichever
  // job the user opened rather than sticking at the first one's values.
  const [quantity, setQuantity] = useState(trimQty(job?.quantity_planned ?? "0"));
  const [start, setStart] = useState(job?.scheduled_start ?? "");
  const [notes, setNotes] = useState(job?.notes ?? "");
  const [operatorId, setOperatorId] = useState(job?.assigned_operator_id ?? "");

  const isScheduled = job?.status === "scheduled";

  const operators = useQuery<{ id: string; name: string }[]>({
    queryKey: ["operators"],
    enabled: job !== null,
    queryFn: async () => {
      const res = await apiFetch("/operators");
      if (!res.ok) throw new Error("Failed to load operators");
      return res.json();
    },
  });

  const save = useMutation({
    mutationFn: async () => {
      if (job === null) return null;
      const body: Record<string, unknown> = { assigned_operator_id: operatorId || null };
      // Every other field 409s on a job that isn't "scheduled" - only send
      // them when the job actually is, so assigning an operator to a running
      // job doesn't trip that guard.
      if (isScheduled) {
        body.quantity_planned = quantity;
        body.scheduled_start = start || null;
        body.notes = notes || null;
      }
      const res = await apiFetch(`/work-orders/${job.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const responseBody = await res.json().catch(() => ({}));
        throw new Error(responseBody.detail ?? "Failed to save the job");
      }
      return res.json();
    },
    onSuccess: onDone,
    onError: (e: Error) => onError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }

  return (
    <Dialog open={job !== null} onClose={onClose} title={`Edit WO-${job?.wo_number ?? ""}`}>
      <form onSubmit={onSubmit} className="grid gap-4">
        {isScheduled && (
          <>
            <p className="text-sm text-muted-foreground">
              Changing the quantity recomputes what this job takes, against the recipe as it stands
              now.
            </p>
            <Field label="Quantity" htmlFor="edit-qty">
              <Input
                id="edit-qty"
                type="number"
                min="0"
                step="0.0001"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                required
              />
            </Field>
            <Field label="Start date" htmlFor="edit-start">
              <Input
                id="edit-start"
                type="date"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </Field>
            <Field label="Notes" htmlFor="edit-notes">
              <Input id="edit-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
            </Field>
          </>
        )}
        <Field
          label="Assigned operator"
          htmlFor="edit-operator"
          hint="Who's running this job, for shops without a badge reader or QR check-in."
        >
          <Select id="edit-operator" value={operatorId} onChange={(e) => setOperatorId(e.target.value)}>
            <option value="">Unassigned</option>
            {operators.data?.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Never mind
          </Button>
          <Button type="submit" loading={save.isPending}>
            Save
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function FinishDialog({
  job,
  onClose,
  onDone,
  onError,
}: {
  job: WorkOrder | null;
  onClose: () => void;
  onDone: () => void;
  onError: (message: string) => void;
}) {
  const [good, setGood] = useState("");
  const [scrapped, setScrapped] = useState("0");
  const [scrapReason, setScrapReason] = useState("");
  const [lotNumber, setLotNumber] = useState("");
  const [disposition, setDisposition] = useState("");

  const finish = useMutation({
    mutationFn: async () => {
      if (job === null) return null;
      const res = await apiFetch(`/work-orders/${job.id}/finish`, {
        method: "POST",
        body: JSON.stringify({
          quantity_good: good,
          quantity_scrapped: scrapped || "0",
          scrap_reason: scrapReason || null,
          lot_number: lotNumber || null,
          remainder_disposition: disposition || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to finish the job");
      }
      return res.json();
    },
    onSuccess: () => {
      setGood("");
      setScrapped("0");
      setScrapReason("");
      setLotNumber("");
      setDisposition("");
      onDone();
    },
    onError: (e: Error) => onError(e.message),
  });

  // The backend refuses an under-run with no answer, so asking here is the
  // difference between a question and a 400.
  const madeSoFar = Number(good || 0) + Number(scrapped || 0);
  const isUnderRun = job !== null && madeSoFar < Number(job.quantity_planned);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    finish.mutate();
  }

  return (
    <Dialog open={job !== null} onClose={onClose} title={`Finish WO-${job?.wo_number ?? ""}`}>
      <form onSubmit={onSubmit} className="grid gap-4">
        <p className="text-sm text-muted-foreground">
          {job?.item_name}, planned for {trimQty(job?.quantity_planned ?? "0")}. Good units go into
          stock; scrap does not.
        </p>
        <Field label="Good units" htmlFor="finish-good">
          <Input
            id="finish-good"
            type="number"
            min="0"
            step="0.0001"
            value={good}
            onChange={(e) => setGood(e.target.value)}
            required
          />
        </Field>
        <Field label="Scrapped" htmlFor="finish-scrapped">
          <Input
            id="finish-scrapped"
            type="number"
            min="0"
            step="0.0001"
            value={scrapped}
            onChange={(e) => setScrapped(e.target.value)}
          />
        </Field>
        <Field label="Why was it scrapped?" htmlFor="finish-scrap-reason">
          <Input
            id="finish-scrap-reason"
            value={scrapReason}
            onChange={(e) => setScrapReason(e.target.value)}
            placeholder="Porosity, wrong size…"
          />
        </Field>
        {job?.item_is_lot_tracked && (
          <Field label="Lot number" htmlFor="finish-lot">
            <Input
              id="finish-lot"
              value={lotNumber}
              onChange={(e) => setLotNumber(e.target.value)}
              required
            />
          </Field>
        )}
        {isUnderRun && (
          <Field label="The units you did not make" htmlFor="finish-disposition">
            <Select
              id="finish-disposition"
              value={disposition}
              onChange={(e) => setDisposition(e.target.value)}
              required
            >
              <option value="">Choose one...</option>
              <option value="not_started">Their components are still on the shelf</option>
              <option value="consumed">Their components were used up</option>
            </Select>
          </Field>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Never mind
          </Button>
          <Button type="submit" loading={finish.isPending}>
            Finish job
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
