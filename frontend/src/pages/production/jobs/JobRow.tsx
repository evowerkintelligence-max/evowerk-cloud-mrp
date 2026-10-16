import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { TD, TR } from "@/components/ui/table";
import { humanize, woStatusVariant } from "@/lib/status";
import { trimQty } from "../../planning/types";
import type { WorkOrder } from "../types";

/** A scheduled job whose start date has passed. Same treatment OrdersPanel
 * gives an overdue order. */
function isLate(job: WorkOrder): boolean {
  if (job.status !== "scheduled" || job.scheduled_start === null) return false;
  return job.scheduled_start < new Date().toISOString().slice(0, 10);
}

export function JobRow({
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
        <span className="block text-xs text-muted-foreground">
          {job.item_sku}{" "}
          <Link to={`/warehouse?item=${job.item_id}`} className="underline">
            View in Inventory
          </Link>
        </span>
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
