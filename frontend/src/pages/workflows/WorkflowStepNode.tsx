import { Handle, Position, type NodeProps } from "@xyflow/react";
import { cn } from "@/lib/utils";

export type RunNodeStatus = "idle" | "running" | "success" | "failed" | "skipped";

export interface WorkflowNodeData {
  name: string;
  type: string;
  status: RunNodeStatus;
  error?: string;
  [key: string]: unknown;
}

const STATUS_BORDER: Record<RunNodeStatus, string> = {
  idle: "border-border",
  running: "border-accent animate-pulse",
  success: "border-success",
  failed: "border-destructive",
  skipped: "border-border opacity-50",
};

export function WorkflowStepNode({ data, selected }: NodeProps & { data: WorkflowNodeData }) {
  return (
    <div
      className={cn(
        "min-w-[10rem] rounded-md border-2 bg-popover px-3 py-2 shadow-sm",
        STATUS_BORDER[data.status],
        selected && "ring-2 ring-ring/50",
      )}
      title={data.error}
    >
      <Handle type="target" position={Position.Left} />
      <div className="text-xs font-medium text-muted-foreground">{data.type}</div>
      <div className="truncate text-sm font-medium">{data.name || "(unnamed step)"}</div>
      {data.error && <div className="mt-1 truncate text-xs text-destructive">{data.error}</div>}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
