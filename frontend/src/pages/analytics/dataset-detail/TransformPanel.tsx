import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "../../../api/client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { ColumnChecklist, fmt, type Dataset } from "../shared";
import type { DatasetDetail } from "./types";

export function TransformPanel({ d, onCreated }: { d: DatasetDetail; onCreated: (id: string) => void }) {
  const names = d.columns.map((c) => c.name);

  const [subsetCols, setSubsetCols] = useState<Set<string>>(new Set(names));
  const [subsetName, setSubsetName] = useState(`${d.name} (subset)`);
  const subset = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/datasets/${d.id}/transform/select-columns`, {
        method: "POST",
        body: JSON.stringify({ columns: Array.from(subsetCols), output_name: subsetName.trim() || undefined }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Subset failed");
      return res.json() as Promise<{ dataset: Dataset; dropped_columns: string[] }>;
    },
  });

  const [direction, setDirection] = useState<"wide_to_tall" | "tall_to_wide">("wide_to_tall");
  const [timestampCol, setTimestampCol] = useState("");
  const [tagCols, setTagCols] = useState<Set<string>>(new Set());
  const [tagCol, setTagCol] = useState("tag");
  const [valueCol, setValueCol] = useState("value");
  const [pivotName, setPivotName] = useState(`${d.name} (${direction})`);
  const pivot = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = {
        direction,
        output_name: pivotName.trim() || undefined,
        timestamp_col: timestampCol || undefined,
      };
      if (direction === "wide_to_tall") body.tag_cols = tagCols.size ? Array.from(tagCols) : undefined;
      else {
        body.tag_col = tagCol;
        body.value_col = valueCol;
      }
      const res = await apiFetch(`/datasets/${d.id}/transform/pivot`, { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Pivot failed");
      return res.json() as Promise<Dataset>;
    },
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Column subset</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">Create a new dataset with only the selected columns.</p>
          <ColumnChecklist columns={names} selected={subsetCols} onChange={setSubsetCols} />
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Output name" className="min-w-[16rem]">
              <Input value={subsetName} onChange={(e) => setSubsetName(e.target.value)} />
            </Field>
            <Button disabled={subsetCols.size === 0} loading={subset.isPending} onClick={() => subset.mutate()}>
              Create subset dataset
            </Button>
          </div>
          {subset.isError && <Alert variant="destructive">{(subset.error as Error).message}</Alert>}
          {subset.isSuccess && (
            <Card className="border-emerald-500/30 bg-emerald-500/5 p-4">
              <div className="text-sm font-semibold">
                Created "{subset.data.dataset.name}" — {fmt(subset.data.dataset.row_count)} rows ×{" "}
                {subset.data.dataset.columns.length} columns
              </div>
              <Button variant="secondary" size="sm" className="mt-3" onClick={() => onCreated(subset.data.dataset.id)}>
                Open subset dataset
              </Button>
            </Card>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Pivot (wide ↔ tall)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Reshape historian-style data: one column per tag ("wide") vs. one row per reading ("tall").
          </p>
          <div className="flex gap-4 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="radio"
                checked={direction === "wide_to_tall"}
                onChange={() => setDirection("wide_to_tall")}
              />
              Wide → Tall
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                checked={direction === "tall_to_wide"}
                onChange={() => setDirection("tall_to_wide")}
              />
              Tall → Wide
            </label>
          </div>

          <Field label="Timestamp column">
            <Select value={timestampCol} onChange={(e) => setTimestampCol(e.target.value)}>
              <option value="">Auto-detect</option>
              {names.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          </Field>

          {direction === "wide_to_tall" ? (
            <div className="space-y-2">
              <div className="text-sm font-medium">Tag columns (leave empty for all non-timestamp columns)</div>
              <ColumnChecklist
                columns={names.filter((c) => c !== timestampCol)}
                selected={tagCols}
                onChange={setTagCols}
              />
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Tag column">
                <Select value={tagCol} onChange={(e) => setTagCol(e.target.value)}>
                  {names.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Value column">
                <Select value={valueCol} onChange={(e) => setValueCol(e.target.value)}>
                  {names.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          )}

          <div className="flex flex-wrap items-end gap-3">
            <Field label="Output name" className="min-w-[16rem]">
              <Input value={pivotName} onChange={(e) => setPivotName(e.target.value)} />
            </Field>
            <Button loading={pivot.isPending} onClick={() => pivot.mutate()}>
              Run transform
            </Button>
          </div>
          {pivot.isError && <Alert variant="destructive">{(pivot.error as Error).message}</Alert>}
          {pivot.isSuccess && (
            <Card className="border-emerald-500/30 bg-emerald-500/5 p-4">
              <div className="text-sm font-semibold">
                Created "{pivot.data.name}" — {fmt(pivot.data.row_count)} rows × {pivot.data.columns.length} columns
              </div>
              <Button variant="secondary" size="sm" className="mt-3" onClick={() => onCreated(pivot.data.id)}>
                Open transformed dataset
              </Button>
            </Card>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
