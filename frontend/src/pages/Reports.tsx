import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { useDatasets } from "./analytics/shared";

interface ItemResponse {
  id: string;
  sku: string;
  name: string;
}

type Kind = "mrp-plan" | "inventory-analytics" | "rca" | "scenario" | "dataset";
type Format = "pdf" | "csv" | "html";

const KINDS: { kind: Kind; label: string; description: string }[] = [
  {
    kind: "mrp-plan",
    label: "MRP plan",
    description: "What to build and what to order over a demand horizon, netted and exploded through active BOMs.",
  },
  {
    kind: "inventory-analytics",
    label: "Inventory analytics",
    description: "Reorder point, safety stock, days of supply and ABC/XYZ class for every item.",
  },
  {
    kind: "rca",
    label: "Root cause analysis",
    description: "Which suppliers, item types or ABC classes are significantly associated with stockout risk.",
  },
  {
    kind: "scenario",
    label: "Scenario comparison",
    description: "How reorder point, safety stock and status move if lead times change. Nothing is persisted.",
  },
  {
    kind: "dataset",
    label: "Dataset",
    description: "A row preview and size/shape summary of one uploaded dataset, instead of live production data.",
  },
];

const DIMENSIONS = ["supplier", "item_type", "abc_class"];

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function plusDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export function Reports() {
  const [kind, setKind] = useState<Kind>("mrp-plan");
  const [horizonStart, setHorizonStart] = useState(today());
  const [horizonEnd, setHorizonEnd] = useState(plusDays(90));
  const [dimension, setDimension] = useState(DIMENSIONS[0]);
  const [leadTimes, setLeadTimes] = useState<Record<string, string>>({});
  const [datasetId, setDatasetId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
    enabled: kind === "scenario",
  });

  const datasets = useDatasets();

  // Only items given a lead time take part in the what-if; the backend rejects
  // an empty override set, so the preview stays idle until one is entered.
  const leadTimeParam = Object.entries(leadTimes)
    .filter(([, days]) => days.trim() !== "")
    .map(([itemId, days]) => `${itemId}:${days.trim()}`)
    .join(",");

  function reportUrl(format: Format): string {
    const params = new URLSearchParams({ format });
    if (kind === "mrp-plan") {
      params.set("horizon_start", horizonStart);
      params.set("horizon_end", horizonEnd);
    }
    if (kind === "rca") params.set("dimension", dimension);
    if (kind === "scenario") params.set("lead_times", leadTimeParam);
    if (kind === "dataset") params.set("dataset_id", datasetId);
    return `/reports/${kind}?${params}`;
  }

  const ready = (kind !== "scenario" || leadTimeParam !== "") && (kind !== "dataset" || datasetId !== "");

  // Reports are authenticated, so they can't be fetched with a plain <a href> -
  // pull the bytes through apiFetch (which carries the bearer token and
  // refreshes it) and hand the browser a blob instead.
  async function fetchReport(format: Format): Promise<Blob> {
    const res = await apiFetch(reportUrl(format));
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? "Failed to build report");
    }
    return res.blob();
  }

  async function download(format: Format) {
    setError(null);
    try {
      const url = URL.createObjectURL(await fetchReport(format));
      const a = document.createElement("a");
      a.href = url;
      a.download = `${kind}-${today().replace(/-/g, "")}.${format}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // Live preview of the exact HTML the download produces, sandboxed so a
  // report can never script the app around it.
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const preview = useQuery({
    queryKey: ["report-preview", kind, horizonStart, horizonEnd, dimension, leadTimeParam, datasetId],
    queryFn: () => fetchReport("html"),
    enabled: ready,
    retry: false,
  });

  useEffect(() => {
    if (!preview.data) return;
    const url = URL.createObjectURL(preview.data);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [preview.data]);

  const active = KINDS.find((k) => k.kind === kind)!;

  return (
    <div>
      <p>
        <Link to="/">Back to dashboard</Link>
      </p>
      <h1>Reports</h1>
      <p>
        Every report is built from live data when you ask for it — nothing is stored. Download it as a
        PDF to file, a CSV to work with, or an HTML page to email.
      </p>

      {error && <p role="alert">{error}</p>}

      <section>
        <h2>Report</h2>
        <label>
          Kind
          <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
            {KINDS.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <p>{active.description}</p>

        {kind === "mrp-plan" && (
          <>
            <label>
              Horizon start
              <input type="date" value={horizonStart} onChange={(e) => setHorizonStart(e.target.value)} />
            </label>
            <label>
              Horizon end
              <input type="date" value={horizonEnd} onChange={(e) => setHorizonEnd(e.target.value)} />
            </label>
          </>
        )}

        {kind === "rca" && (
          <label>
            Dimension
            <select value={dimension} onChange={(e) => setDimension(e.target.value)}>
              {DIMENSIONS.map((d) => (
                <option key={d} value={d}>
                  {d.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </label>
        )}

        {kind === "scenario" && (
          <>
            <p>Enter a hypothetical lead time (in days) for the items you want to compare.</p>
            <table>
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Scenario lead time (days)</th>
                </tr>
              </thead>
              <tbody>
                {items.data?.map((item) => (
                  <tr key={item.id}>
                    <td>
                      {item.sku} - {item.name}
                    </td>
                    <td>
                      <input
                        type="number"
                        min="0"
                        value={leadTimes[item.id] ?? ""}
                        placeholder="unchanged"
                        onChange={(e) =>
                          setLeadTimes((prev) => ({ ...prev, [item.id]: e.target.value }))
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!ready && <p>Set a lead time on at least one item to run the comparison.</p>}
          </>
        )}

        {kind === "dataset" && (
          <label>
            Dataset
            <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
              <option value="">Select a dataset…</option>
              {datasets.data?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.row_count.toLocaleString()} rows)
                </option>
              ))}
            </select>
          </label>
        )}
      </section>

      <section>
        <h2>Export</h2>
        <button onClick={() => download("pdf")} disabled={!ready}>
          Download PDF
        </button>{" "}
        <button onClick={() => download("csv")} disabled={!ready}>
          Download CSV
        </button>{" "}
        <button onClick={() => download("html")} disabled={!ready}>
          Download HTML
        </button>
      </section>

      <section>
        <h2>Preview</h2>
        {preview.isError && <p role="alert">{(preview.error as Error).message}</p>}
        {preview.isFetching && <p>Building report…</p>}
        {ready && previewUrl && !preview.isError && (
          <iframe
            title={`${active.label} preview`}
            src={previewUrl}
            sandbox=""
            style={{ width: "100%", height: "70vh", border: "1px solid #ccc" }}
          />
        )}
      </section>
    </div>
  );
}
