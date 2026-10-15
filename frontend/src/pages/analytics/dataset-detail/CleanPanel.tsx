import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "../../../api/client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { fmt, type Dataset } from "../shared";
import type { DatasetDetail } from "./types";

export function CleanPanel({ d, onCreated }: { d: DatasetDetail; onCreated: (id: string) => void }) {
  const [trimStrings, setTrimStrings] = useState(true);
  const [dropDuplicates, setDropDuplicates] = useState(true);
  const [fillMissing, setFillMissing] = useState(false);
  const [fillStrategy, setFillStrategy] = useState("custom");
  const [fillValue, setFillValue] = useState("");
  const [removeOutliers, setRemoveOutliers] = useState(false);
  const [outlierFactor, setOutlierFactor] = useState("1.5");
  const [outlierStrategy, setOutlierStrategy] = useState("remove");
  const [standardize, setStandardize] = useState(false);
  const [outputName, setOutputName] = useState(`${d.name} (cleaned)`);

  const clean = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/datasets/${d.id}/clean`, {
        method: "POST",
        body: JSON.stringify({
          trim_strings: trimStrings,
          drop_duplicates: dropDuplicates,
          fill_missing: fillMissing,
          fill_strategy: fillMissing ? fillStrategy : undefined,
          fill_value: fillMissing && fillStrategy === "custom" ? fillValue : undefined,
          remove_outliers: removeOutliers,
          outlier_iqr_factor: removeOutliers
            ? (outlierFactor.trim() !== "" && Number.isFinite(Number(outlierFactor)) ? Number(outlierFactor) : 1.5)
            : undefined,
          outlier_strategy: removeOutliers ? outlierStrategy : undefined,
          standardize_numeric: standardize,
          output_name: outputName.trim() || undefined,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Clean failed");
      return res.json() as Promise<{ dataset: Dataset; ops: string[] }>;
    },
  });

  const hasOps = trimStrings || dropDuplicates || fillMissing || removeOutliers || standardize;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Clean dataset</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-sm text-muted-foreground">
          Creates a new dataset with the selected operations applied — the original is never modified.
        </p>

        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={trimStrings} onChange={(e) => setTrimStrings(e.target.checked)} />
          Trim whitespace from text values
        </label>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={dropDuplicates} onChange={(e) => setDropDuplicates(e.target.checked)} />
          Drop duplicate rows
        </label>

        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={fillMissing} onChange={(e) => setFillMissing(e.target.checked)} />
            Fill missing values
          </label>
          {fillMissing && (
            <div className="ml-6 grid gap-3 sm:grid-cols-2">
              <Field label="Strategy">
                <Select value={fillStrategy} onChange={(e) => setFillStrategy(e.target.value)}>
                  <option value="custom">Custom value</option>
                  <option value="mean">Column mean</option>
                  <option value="median">Column median</option>
                  <option value="mode">Most frequent (mode)</option>
                </Select>
              </Field>
              {fillStrategy === "custom" && (
                <Field label="Fill value">
                  <Input value={fillValue} onChange={(e) => setFillValue(e.target.value)} placeholder="e.g. 0" />
                </Field>
              )}
            </div>
          )}
        </div>

        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={removeOutliers} onChange={(e) => setRemoveOutliers(e.target.checked)} />
            Handle outliers (IQR method, across all numeric columns)
          </label>
          {removeOutliers && (
            <div className="ml-6 grid gap-3 sm:grid-cols-2">
              <Field label="IQR factor">
                <Input type="number" step="0.1" value={outlierFactor} onChange={(e) => setOutlierFactor(e.target.value)} />
              </Field>
              <Field label="Action">
                <Select value={outlierStrategy} onChange={(e) => setOutlierStrategy(e.target.value)}>
                  <option value="remove">Remove rows</option>
                  <option value="cap">Cap to fence value</option>
                </Select>
              </Field>
            </div>
          )}
        </div>

        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={standardize} onChange={(e) => setStandardize(e.target.checked)} />
          Standardize numeric columns (z-score)
        </label>

        <Field label="Output dataset name">
          <Input value={outputName} onChange={(e) => setOutputName(e.target.value)} />
        </Field>

        <Button disabled={!hasOps} loading={clean.isPending} onClick={() => clean.mutate()}>
          Run cleaning
        </Button>

        {clean.isError && <Alert variant="destructive">{(clean.error as Error).message}</Alert>}

        {clean.isSuccess && (
          <Card className="border-emerald-500/30 bg-emerald-500/5 p-4">
            <div className="text-sm font-semibold">
              Created "{clean.data.dataset.name}" — {fmt(clean.data.dataset.row_count)} rows
            </div>
            <div className="mt-1 text-xs text-muted-foreground">Applied: {clean.data.ops.join(", ")}</div>
            <Button variant="secondary" size="sm" className="mt-3" onClick={() => onCreated(clean.data.dataset.id)}>
              Open cleaned dataset
            </Button>
          </Card>
        )}
      </CardContent>
    </Card>
  );
}
