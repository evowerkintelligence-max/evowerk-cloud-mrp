import { useState } from "react";
import { ChevronDown, ChevronRight, ChevronUp, GripVertical, Trash2 } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Button } from "@/components/ui/button";
import { TD, TR } from "@/components/ui/table";
import type { BomResponse, BomLineResponse, BomDetailResponse } from "./types";

// Recursively explodes a line into its component's own active BOM, if it has
// one - lazily fetched only once expanded, reusing the same GET /boms/{id}
// the top-level detail view already uses.
export function BomLineRow({
  line,
  depth,
  allBoms,
  onRemove,
  removePending,
  draggable,
  onDropLine,
  onMove,
  canMoveUp,
  canMoveDown,
}: {
  line: BomLineResponse;
  depth: number;
  allBoms: BomResponse[];
  onRemove?: (lineId: string) => void;
  removePending?: boolean;
  draggable?: boolean;
  onDropLine?: (draggedId: string, targetId: string) => void;
  onMove?: (lineId: string, direction: "up" | "down") => void;
  canMoveUp?: boolean;
  canMoveDown?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const queryClient = useQueryClient();
  const childBom = allBoms.find((b) => b.item_id === line.component_item_id && b.status === "active");

  function prefetchChild() {
    if (!childBom) return;
    // Warms the cache on hover so the click that follows usually finds it
    // already there instead of waiting on a fresh round trip.
    queryClient.prefetchQuery({
      queryKey: ["boms", childBom.id],
      queryFn: async () => {
        const res = await apiFetch(`/boms/${childBom.id}`);
        if (!res.ok) throw new Error("Failed to load BOM");
        return res.json();
      },
    });
  }

  const childDetail = useQuery<BomDetailResponse>({
    queryKey: ["boms", childBom?.id],
    enabled: expanded && !!childBom,
    queryFn: async () => {
      const res = await apiFetch(`/boms/${childBom!.id}`);
      if (!res.ok) throw new Error("Failed to load BOM");
      return res.json();
    },
  });

  return (
    <>
      <TR
        onDragOver={(e) => {
          if (draggable) e.preventDefault();
        }}
        onDrop={(e) => {
          if (!draggable) return;
          e.preventDefault();
          const draggedId = e.dataTransfer.getData("text/plain");
          onDropLine?.(draggedId, line.id);
        }}
      >
        {onRemove && (
          <TD
            className="w-6 cursor-move text-muted-foreground"
            draggable
            onDragStart={(e) => e.dataTransfer.setData("text/plain", line.id)}
            title="Drag to reorder"
          >
            <span className="inline-flex items-center gap-0.5">
              <GripVertical className="size-4" />
              {/* Keyboard/screen-reader alternative to the drag handle above -
                  native HTML5 drag-and-drop has no keyboard equivalent. */}
              <span className="flex flex-col">
                <button
                  type="button"
                  disabled={!canMoveUp}
                  onClick={() => onMove?.(line.id, "up")}
                  className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                  title="Move up"
                >
                  <ChevronUp className="size-3" />
                </button>
                <button
                  type="button"
                  disabled={!canMoveDown}
                  onClick={() => onMove?.(line.id, "down")}
                  className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                  title="Move down"
                >
                  <ChevronDown className="size-3" />
                </button>
              </span>
            </span>
          </TD>
        )}
        <TD className="font-medium">
          <span className="inline-flex items-center gap-1" style={{ paddingLeft: `${depth * 1.25}rem` }}>
            {childBom ? (
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                onMouseEnter={prefetchChild}
                className="text-muted-foreground hover:text-foreground"
                title={expanded ? "Collapse" : "Show this component's own BOM"}
              >
                {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
              </button>
            ) : (
              <span className="inline-block size-3.5" />
            )}
            {line.component_sku} - {line.component_name}
          </span>
        </TD>
        <TD>{line.quantity}</TD>
        <TD className="text-muted-foreground">{line.unit_of_measure}</TD>
        <TD className="text-muted-foreground">
          {line.unit_code ? `${line.unit_quantity} ${line.unit_code}` : "-"}
        </TD>
        <TD className="text-muted-foreground">{Number(line.scrap_pct) > 0 ? `${line.scrap_pct}%` : "-"}</TD>
        {onRemove && (
          <TD>
            <Button
              variant="ghost"
              size="icon"
              loading={removePending}
              onClick={() => onRemove(line.id)}
              title="Remove line"
            >
              <Trash2 className="size-4" />
            </Button>
          </TD>
        )}
      </TR>
      {expanded &&
        childBom &&
        childDetail.data?.lines.map((childLine) => (
          <BomLineRow key={childLine.id} line={childLine} depth={depth + 1} allBoms={allBoms} />
        ))}
    </>
  );
}
