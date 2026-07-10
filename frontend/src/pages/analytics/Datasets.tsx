import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Database } from "lucide-react";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { useDatasets, fileSize, fmt } from "./shared";

export function Datasets() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const datasets = useDatasets();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose a file first");
      const fd = new FormData();
      fd.append("file", file);
      if (name.trim()) fd.append("name", name.trim());
      const res = await apiFetch("/datasets", { method: "POST", body: fd });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Upload failed");
      return res.json();
    },
    onSuccess: () => {
      setError(null);
      setFile(null);
      setName("");
      qc.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const del = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/datasets/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) throw new Error("Delete failed");
    },
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div className="space-y-8">
      <PageHeader
        title="Datasets"
        description="Production data files powering the analytics engines. Upload CSV, Excel, JSON, or Parquet."
      />

      {error && <Alert variant="destructive">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>Upload dataset</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          <Field label="File">
            <Input
              type="file"
              accept=".csv,.tsv,.xlsx,.xls,.json,.parquet"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="file:mr-3 file:rounded file:border-0 file:bg-secondary file:px-3 file:py-1 file:text-foreground"
            />
          </Field>
          <Field label="Name (optional)">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Defaults to filename" />
          </Field>
          <div className="flex items-end">
            <Button loading={upload.isPending} disabled={!file} onClick={() => upload.mutate()}>
              Upload
            </Button>
          </div>
        </CardContent>
      </Card>

      {datasets.isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : !datasets.data?.length ? (
        <EmptyState
          icon={<Database />}
          title="No datasets yet"
          description="Upload your first production data file to start running the analytics engines."
        />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>All datasets</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Name</TH>
                    <TH>Format</TH>
                    <TH>Rows</TH>
                    <TH>Columns</TH>
                    <TH>Size</TH>
                    <TH>Uploaded</TH>
                    <TH />
                  </TR>
                </THead>
                <TBody>
                  {datasets.data.map((d) => (
                    <TR key={d.id}>
                      <TD className="font-medium">
                        {d.name}
                        <div className="text-xs text-muted-foreground">{d.original_filename}</div>
                      </TD>
                      <TD>
                        <Badge variant="neutral">{d.fmt}</Badge>
                      </TD>
                      <TD>{fmt(d.row_count)}</TD>
                      <TD>{d.columns.length}</TD>
                      <TD>{fileSize(d.size_bytes)}</TD>
                      <TD>{new Date(d.created_at).toLocaleDateString()}</TD>
                      <TD>
                        <div className="flex justify-end gap-2">
                          <Button variant="secondary" size="sm" onClick={() => navigate(`/datasets/${d.id}`)}>
                            Open
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-rose-400 hover:text-rose-300"
                            loading={del.isPending && del.variables === d.id}
                            onClick={() => {
                              if (confirm(`Delete dataset "${d.name}"? This cannot be undone.`)) del.mutate(d.id);
                            }}
                          >
                            Delete
                          </Button>
                        </div>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
