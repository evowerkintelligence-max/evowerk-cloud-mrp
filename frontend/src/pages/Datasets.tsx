import { ChangeEvent, FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

interface DatasetResponse {
  id: string;
  name: string;
  original_filename: string;
  fmt: string;
  columns: { name: string; dtype: string }[];
  row_count: number;
  size_bytes: number;
  created_at: string;
}

export function Datasets() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  const datasets = useQuery<DatasetResponse[]>({
    queryKey: ["datasets"],
    queryFn: async () => {
      const res = await apiFetch("/datasets");
      if (!res.ok) throw new Error("Failed to load datasets");
      return res.json();
    },
  });

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose a file first");
      const form = new FormData();
      form.append("file", file);
      if (name) form.append("name", name);
      const res = await apiFetch("/datasets", { method: "POST", body: form });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to upload dataset");
      }
      return res.json();
    },
    onSuccess: () => {
      setName("");
      setFile(null);
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/datasets/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete dataset");
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["datasets"] }),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    upload.mutate();
  }

  function onFileChange(e: ChangeEvent<HTMLInputElement>) {
    setFile(e.target.files?.[0] ?? null);
  }

  return (
    <div>
      <p>
        <Link to="/">Back to dashboard</Link>
      </p>
      <h1>Datasets</h1>
      <p>Upload a spreadsheet or CSV to preview, check its health, and query it - optionally joined to your Items.</p>

      {error && <p role="alert">{error}</p>}

      <form onSubmit={onSubmit}>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={file?.name} />
        </label>
        <label>
          File
          <input
            type="file"
            accept=".csv,.tsv,.xlsx,.xls,.json,.jsonl,.ndjson,.parquet"
            onChange={onFileChange}
            required
          />
        </label>
        <button type="submit" disabled={upload.isPending}>
          Upload
        </button>
      </form>

      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Rows</th>
            <th>Columns</th>
            <th>Uploaded</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {datasets.data?.map((ds) => (
            <tr key={ds.id}>
              <td>
                <Link to={`/datasets/${ds.id}`}>{ds.name}</Link>
              </td>
              <td>{ds.row_count}</td>
              <td>{ds.columns.length}</td>
              <td>{new Date(ds.created_at).toLocaleString()}</td>
              <td>
                <button onClick={() => remove.mutate(ds.id)} disabled={remove.isPending}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
