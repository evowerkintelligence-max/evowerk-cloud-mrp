import { ChangeEvent, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { parseCsv } from "../../lib/csv";
import { CSV_TEMPLATE, mapForecastCsv, type CsvForecastRow, type CsvRowError } from "./csvImport";

interface ImportResult {
  created: number;
  updated: number;
  total: number;
}

export function ImportForecastsCsvSection({ items }: { items: { id: string; sku: string }[] }) {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [rows, setRows] = useState<CsvForecastRow[]>([]);
  const [errors, setErrors] = useState<CsvRowError[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);

  function onFile(e: ChangeEvent<HTMLInputElement>) {
    setError(null);
    setResult(null);
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    void file.text().then((text) => {
      const itemIdBySku = new Map(items.map((it) => [it.sku, it.id]));
      const mapped = mapForecastCsv(parseCsv(text), itemIdBySku);
      setRows(mapped.rows);
      setErrors(mapped.errors);
    });
  }

  function onDownloadTemplate() {
    const url = URL.createObjectURL(new Blob([CSV_TEMPLATE], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "forecast-import-template.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  const importCsv = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/forecasts/import", {
        method: "POST",
        body: JSON.stringify(rows),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Import failed");
      }
      return (await res.json()) as ImportResult;
    },
    onSuccess: (r) => {
      setResult(r);
      setRows([]);
      setErrors([]);
      setFileName(null);
      if (fileRef.current) fileRef.current.value = "";
      queryClient.invalidateQueries({ queryKey: ["planning-grid"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div className="space-y-3 border-t border-border pt-6">
      <h3 className="text-sm font-semibold">Import forecasts from CSV</h3>
      <p className="text-sm text-muted-foreground">
        Columns: sku, period_start (YYYY-MM-DD), quantity, notes (optional).{" "}
        <button type="button" onClick={onDownloadTemplate} className="text-primary hover:underline">
          Download template
        </button>
      </p>
      <input
        ref={fileRef}
        type="file"
        accept=".csv,text/csv"
        onChange={onFile}
        className="text-sm"
        aria-label="Forecast CSV file"
      />

      {error && <Alert variant="destructive">{error}</Alert>}
      {result && (
        <Alert variant="success">
          Imported {result.total} row{result.total === 1 ? "" : "s"}: {result.created} created,{" "}
          {result.updated} updated.
        </Alert>
      )}

      {errors.length > 0 && (
        <Alert variant="destructive">
          <div className="space-y-1">
            <p>Fix these rows and re-select the file (nothing was imported):</p>
            <ul className="list-disc pl-5 text-sm">
              {errors.map((err) => (
                <li key={`${err.line}-${err.message}`}>
                  Line {err.line}: {err.message}
                </li>
              ))}
            </ul>
          </div>
        </Alert>
      )}

      {rows.length > 0 && errors.length === 0 && (
        <>
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Item</TH>
                  <TH>Period start</TH>
                  <TH>Quantity</TH>
                  <TH>Notes</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((r, i) => (
                  <TR key={`${r.item_id}-${r.period_start}-${i}`}>
                    <TD>{items.find((it) => it.id === r.item_id)?.sku ?? r.item_id}</TD>
                    <TD>{r.period_start}</TD>
                    <TD>{r.quantity}</TD>
                    <TD className="text-muted-foreground">{r.notes ?? "—"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
          <Button onClick={() => importCsv.mutate()} loading={importCsv.isPending}>
            Import {rows.length} row{rows.length === 1 ? "" : "s"} from {fileName}
          </Button>
        </>
      )}
    </div>
  );
}
