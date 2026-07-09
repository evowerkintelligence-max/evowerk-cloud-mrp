import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { C, fileSize, fmt, type Dataset } from "./shared";

interface DatasetDetail extends Dataset {
  preview: Record<string, unknown>[];
}

interface HealthColumn {
  name: string;
  inferred_dtype: string;
  null_count: number;
  null_pct: number;
  distinct_count: number;
  top_values: { value: string; count: number }[];
  min: number | null;
  max: number | null;
  mean: number | null;
  std: number | null;
  numeric_count: number | null;
  outlier_count: number | null;
  outlier_pct: number | null;
}

interface Health {
  row_count: number;
  column_count: number;
  duplicate_rows: number;
  duplicate_rows_pct: number;
  total_null_cells: number;
  total_cells: number;
  null_cells_pct: number;
  columns: HealthColumn[];
  warnings: string[];
}

const TABS = ["overview", "schema", "preview", "health", "clean", "transform"] as const;
type Tab = (typeof TABS)[number];

function Overview({ d }: { d: DatasetDetail }) {
  const rows: [string, string][] = [
    ["Name", d.name],
    ["Original filename", d.original_filename],
    ["Format", d.fmt],
    ["Rows", fmt(d.row_count)],
    ["Columns", String(d.columns.length)],
    ["Size", fileSize(d.size_bytes)],
    ["Uploaded", new Date(d.created_at).toLocaleString()],
    ["ID", d.id],
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Metadata</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-3 sm:grid-cols-2">
          {rows.map(([label, val]) => (
            <div key={label} className="flex items-center justify-between gap-4 border-b border-border/50 pb-2 text-sm">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="truncate font-medium" title={val}>
                {val}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

function Schema({ d }: { d: DatasetDetail }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Columns</CardTitle>
      </CardHeader>
      <CardContent>
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Type</TH>
              </TR>
            </THead>
            <TBody>
              {d.columns.map((c) => (
                <TR key={c.name}>
                  <TD className="font-medium">{c.name}</TD>
                  <TD>
                    <Badge variant="neutral">{c.dtype}</Badge>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      </CardContent>
    </Card>
  );
}

function Preview({ d }: { d: DatasetDetail }) {
  const cols = d.columns.map((c) => c.name);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Preview (first {d.preview.length} rows)</CardTitle>
      </CardHeader>
      <CardContent>
        <TableWrap className="max-h-[32rem] overflow-y-auto">
          <Table>
            <THead>
              <TR>
                {cols.map((c) => (
                  <TH key={c}>{c}</TH>
                ))}
              </TR>
            </THead>
            <TBody>
              {d.preview.map((row, i) => (
                <TR key={i}>
                  {cols.map((c) => (
                    <TD key={c} className="whitespace-nowrap">
                      {row[c] === null || row[c] === undefined ? "—" : String(row[c])}
                    </TD>
                  ))}
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      </CardContent>
    </Card>
  );
}

function HealthPanel({ id }: { id: string }) {
  const health = useQuery<Health>({
    queryKey: ["dataset-health", id],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${id}/health`);
      if (!res.ok) throw new Error("Failed to compute health");
      return res.json();
    },
  });

  if (health.isLoading) return <Skeleton className="h-64 w-full" />;
  if (!health.data) return <Alert variant="destructive">Failed to compute health.</Alert>;
  const h = health.data;

  return (
    <div className="space-y-6">
      {h.warnings.map((w) => (
        <Alert key={w} variant="warning">
          {w}
        </Alert>
      ))}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card className="p-4">
          <div className="text-xs uppercase tracking-wide text-muted-foreground">Rows</div>
          <div className="mt-1 text-xl font-semibold">{fmt(h.row_count)}</div>
        </Card>
        <Card className="p-4">
          <div className="text-xs uppercase tracking-wide text-muted-foreground">Duplicate rows</div>
          <div className="mt-1 text-xl font-semibold" style={{ color: h.duplicate_rows_pct > 5 ? C.red : undefined }}>
            {fmt(h.duplicate_rows)} ({h.duplicate_rows_pct}%)
          </div>
        </Card>
        <Card className="p-4">
          <div className="text-xs uppercase tracking-wide text-muted-foreground">Null cells</div>
          <div className="mt-1 text-xl font-semibold" style={{ color: h.null_cells_pct > 25 ? C.red : undefined }}>
            {fmt(h.total_null_cells)} ({h.null_cells_pct}%)
          </div>
        </Card>
        <Card className="p-4">
          <div className="text-xs uppercase tracking-wide text-muted-foreground">Columns</div>
          <div className="mt-1 text-xl font-semibold">{h.column_count}</div>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Per-column profile</CardTitle>
        </CardHeader>
        <CardContent>
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Column</TH>
                  <TH>Type</TH>
                  <TH>Null %</TH>
                  <TH>Distinct</TH>
                  <TH>Numeric range</TH>
                  <TH>Outliers</TH>
                  <TH>Top values</TH>
                </TR>
              </THead>
              <TBody>
                {h.columns.map((c) => (
                  <TR key={c.name}>
                    <TD className="font-medium">{c.name}</TD>
                    <TD>
                      <Badge variant="neutral">{c.inferred_dtype}</Badge>
                    </TD>
                    <TD style={{ color: c.null_pct > 25 ? C.red : undefined }}>{c.null_pct}%</TD>
                    <TD>{fmt(c.distinct_count)}</TD>
                    <TD>{c.min !== null ? `${c.min} – ${c.max}` : "—"}</TD>
                    <TD style={{ color: (c.outlier_pct ?? 0) > 5 ? C.red : undefined }}>
                      {c.outlier_pct !== null ? `${c.outlier_count} (${c.outlier_pct}%)` : "—"}
                    </TD>
                    <TD className="max-w-xs truncate text-xs text-muted-foreground">
                      {c.top_values.map((v) => v.value).join(", ") || "—"}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
        </CardContent>
      </Card>
    </div>
  );
}

function CleanPanel({ d, onCreated }: { d: DatasetDetail; onCreated: (id: string) => void }) {
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
          outlier_iqr_factor: removeOutliers ? Number(outlierFactor) || 1.5 : undefined,
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

function ColumnChecklist({
  columns,
  selected,
  onChange,
}: {
  columns: string[];
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  return (
    <div className="grid max-h-64 grid-cols-2 gap-1.5 overflow-y-auto rounded-md border border-border p-3 sm:grid-cols-3">
      {columns.map((c) => (
        <label key={c} className="flex items-center gap-2 truncate text-sm">
          <Checkbox
            checked={selected.has(c)}
            onChange={(e) => {
              const next = new Set(selected);
              if (e.target.checked) next.add(c);
              else next.delete(c);
              onChange(next);
            }}
          />
          <span className="truncate" title={c}>
            {c}
          </span>
        </label>
      ))}
    </div>
  );
}

function TransformPanel({ d, onCreated }: { d: DatasetDetail; onCreated: (id: string) => void }) {
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

export function DatasetDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");

  const detail = useQuery<DatasetDetail>({
    queryKey: ["dataset", id],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${id}`);
      if (!res.ok) throw new Error("Dataset not found");
      return res.json();
    },
    enabled: !!id,
  });

  function openDataset(newId: string) {
    qc.invalidateQueries({ queryKey: ["datasets"] });
    navigate(`/datasets/${newId}`);
  }

  if (detail.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (!detail.data) return <Alert variant="destructive">Dataset not found.</Alert>;
  const d = detail.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title={d.name}
        description={`${d.fmt.toUpperCase()} · ${fmt(d.row_count)} rows · ${d.columns.length} columns`}
        actions={
          <Button variant="ghost" size="sm" onClick={() => navigate("/datasets")}>
            <ArrowLeft /> Back to datasets
          </Button>
        }
      />

      <div className="flex gap-1 overflow-x-auto border-b border-border">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium capitalize transition-colors",
              tab === t
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "overview" && <Overview d={d} />}
      {tab === "schema" && <Schema d={d} />}
      {tab === "preview" && <Preview d={d} />}
      {tab === "health" && <HealthPanel id={d.id} />}
      {tab === "clean" && <CleanPanel d={d} onCreated={openDataset} />}
      {tab === "transform" && <TransformPanel d={d} onCreated={openDataset} />}
    </div>
  );
}
