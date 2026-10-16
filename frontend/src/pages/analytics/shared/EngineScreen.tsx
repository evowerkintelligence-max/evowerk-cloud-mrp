import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Maximize2, Minimize2 } from "lucide-react";
import { apiFetch } from "../../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Checkbox } from "@/components/ui/checkbox";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { autoMapColumns, useDatasets, type EngineField } from "./datasets";
import { SendToReportButton } from "./SendToReportButton";

const TV_REFRESH_MS = 30_000;

function useDatasetPreview(datasetId: string) {
  return useQuery<{ preview: Record<string, unknown>[] }>({
    queryKey: ["dataset-preview", datasetId],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${datasetId}`);
      if (!res.ok) throw new Error("Failed to load dataset preview");
      return res.json();
    },
    enabled: Boolean(datasetId),
  });
}

export function EngineScreen<T>({
  title,
  description,
  path,
  fields,
  runLabel,
  showHeader = true,
  children,
}: {
  title: string;
  description: string;
  path: string;
  fields: EngineField[];
  runLabel?: string;
  /** False when an outer page (e.g. a merged tab view) already renders its own PageHeader. */
  showHeader?: boolean;
  children: (result: T, requestBody: Record<string, unknown>) => ReactNode;
}) {
  const [datasetId, setDatasetId] = useState("");
  const [form, setForm] = useState<Record<string, string>>({});
  const [result, setResult] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const resultsRef = useRef<HTMLDivElement>(null);

  const datasets = useDatasets();
  const preview = useDatasetPreview(datasetId);
  const [searchParams] = useSearchParams();

  // The ?dataset_id= dataset (e.g. from Pace Tracking's "View in Throughput")
  // may not be in the ["datasets"] list yet on this page's first render - React
  // Query can still be serving an older cached snapshot from before that
  // dataset existed, with the real list arriving a moment later. Retrying on
  // every datasets.data change (instead of a one-shot effect gated on
  // datasetId) means auto-mapping actually applies once the dataset shows up,
  // rather than silently giving up because the first attempt saw an empty
  // column list.
  const autoMappedFor = useRef<string | null>(null);
  useEffect(() => {
    const fromUrl = searchParams.get("dataset_id");
    if (!fromUrl) return;
    if (!datasetId) setDatasetId(fromUrl);
    if (autoMappedFor.current === fromUrl) return;
    const ds = datasets.data?.find((d) => d.id === fromUrl);
    if (!ds) return;
    autoMappedFor.current = fromUrl;
    setForm(autoMapColumns(fields, ds.columns.map((c) => c.name)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, datasets.data]);

  const columns = useMemo(
    () => datasets.data?.find((d) => d.id === datasetId)?.columns.map((c) => c.name) ?? [],
    [datasets.data, datasetId],
  );

  function pickDataset(id: string) {
    setDatasetId(id);
    setResult(null);
    // Auto-suggest column mappings by naming convention, like the desktop app.
    const cols = datasets.data?.find((d) => d.id === id)?.columns.map((c) => c.name) ?? [];
    setForm(autoMapColumns(fields, cols));
  }

  const lastComputeBody = useRef<Record<string, unknown> | null>(null);

  const compute = useMutation({
    // `rerun` is the auto-refresh path: re-post that exact body instead of rebuilding from the form.
    mutationFn: async (rerun: Record<string, unknown> | void) => {
      const body: Record<string, unknown> = rerun ?? { dataset_id: datasetId };
      if (!rerun) for (const f of fields) {
        const v = form[f.key];
        if (f.kind === "num") body[f.key] = v ? Number(v) : null;
        else if (f.kind === "select") body[f.key] = v || f.options?.[0];
        else if (v) body[f.key] = v;
      }
      lastComputeBody.current = body;
      const res = await apiFetch(path, { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Compute failed");
      return res.json() as Promise<T>;
    },
    onSuccess: (r) => {
      setError(null);
      setResult(r);
    },
    onError: (e: Error) => setError(e.message),
  });

  const ready = Boolean(datasetId) && fields.every((f) => !f.required || form[f.key]);

  // Column-mapped fields (kind "col", the default) point at real dataset
  // columns - num/select fields are user-typed values, not something a data
  // preview can sanity-check.
  const mappedCols = useMemo(
    () =>
      fields
        .filter((f) => (!f.kind || f.kind === "col") && form[f.key])
        .map((f) => ({ field: f.label, col: form[f.key] })),
    [fields, form],
  );

  // TV mode: fullscreen the results container via the native Fullscreen API,
  // optionally polling the last-run compute body so a wall-mounted screen stays live.
  useEffect(() => {
    const onChange = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => {
      if (lastComputeBody.current) compute.mutate(lastComputeBody.current);
    }, TV_REFRESH_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh]);

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else resultsRef.current?.requestFullscreen();
  }

  return (
    <div className="space-y-8">
      {showHeader && <PageHeader title={title} description={description} />}

      {error && <Alert variant="destructive">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>Dataset</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 sm:max-w-sm">
          <Field label="Dataset">
            <Select value={datasetId} onChange={(e) => pickDataset(e.target.value)}>
              <option value="">Select a dataset…</option>
              {datasets.data?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.row_count.toLocaleString()} rows)
                </option>
              ))}
            </Select>
          </Field>
          <p className="text-sm text-muted-foreground">
            Need a new dataset?{" "}
            <Link to="/datasets" className="text-primary underline underline-offset-2">
              Upload one in Datasets
            </Link>
            .
          </p>
        </CardContent>
      </Card>

      {datasetId && (
        <Card>
          <CardHeader>
            <CardTitle>Column mapping</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {fields.map((f) => (
              <Field key={f.key} label={f.label}>
                {f.kind === "num" ? (
                  <Input
                    type="number"
                    step="any"
                    value={form[f.key] ?? ""}
                    onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                  />
                ) : f.kind === "select" ? (
                  <Select value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}>
                    {f.options?.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <Select value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}>
                    <option value="">{f.required ? "Select column…" : "(none)"}</option>
                    {columns.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            ))}
            <div className="flex items-end">
              <Button disabled={!ready} loading={compute.isPending} onClick={() => compute.mutate()}>
                {runLabel ?? "Compute"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {mappedCols.length > 0 && preview.data && preview.data.preview.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Data preview</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-3 text-sm text-muted-foreground">
              First {Math.min(5, preview.data.preview.length)} rows for the columns you mapped — check these look right before running.
            </p>
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    {mappedCols.map((m) => (
                      <TH key={m.field}>
                        {m.field}
                        <span className="ml-1 font-normal text-muted-foreground">({m.col})</span>
                      </TH>
                    ))}
                  </TR>
                </THead>
                <TBody>
                  {preview.data.preview.slice(0, 5).map((row, i) => (
                    <TR key={i}>
                      {mappedCols.map((m) => (
                        <TD key={m.field}>{String(row[m.col] ?? "")}</TD>
                      ))}
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          </CardContent>
        </Card>
      )}

      {result !== null && (
        <div ref={resultsRef} className={isFullscreen ? "bg-background p-8 overflow-y-auto" : ""}>
          <div className="mb-4 flex items-center justify-end gap-4">
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Checkbox checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
              Auto-refresh (30s)
              {autoRefresh && <span aria-hidden className="live-pulse-dot" />}
            </label>
            <Button variant="ghost" size="sm" onClick={toggleFullscreen}>
              {isFullscreen ? <Minimize2 /> : <Maximize2 />}
              {isFullscreen ? "Exit TV mode" : "TV mode"}
            </Button>
          </div>
          {children(result, lastComputeBody.current ?? {})}
          <SendToReportButton
            spec={{ type: "engine", engine_path: path, engine_body: lastComputeBody.current ?? {} }}
            label={`${title} — ${new Date().toLocaleString()}`}
          />
        </div>
      )}
    </div>
  );
}
