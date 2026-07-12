import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { useDatasets } from "./analytics/shared";

interface AskResponse {
  question: string;
  intents: string[];
  narrative: string;
  warnings: string[];
}

const LIVE_DATA = "";

const EXAMPLES = [
  "how's my inventory looking?",
  "why are items low, compare by supplier",
  "forecast expected demand for WIDGET-1",
  "what if WIDGET-1's lead time were 10 days?",
];

export function Ask() {
  const [question, setQuestion] = useState("");
  const [dataSource, setDataSource] = useState(LIVE_DATA);
  const [error, setError] = useState<string | null>(null);
  const datasets = useDatasets();

  const ask = useMutation({
    mutationFn: async (q: string) => {
      const res = await apiFetch("/ai/ask", {
        method: "POST",
        body: JSON.stringify({ question: q, dataset_id: dataSource || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to answer question");
      }
      return (await res.json()) as AskResponse;
    },
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!question.trim()) return;
    ask.mutate(question);
  }

  return (
    <div>
      <p>
        <Link to="/">Back</Link>
      </p>
      <h1>Ask your MRP data</h1>
      <p>
        <label>
          Data source{" "}
          <select value={dataSource} onChange={(e) => setDataSource(e.target.value)}>
            <option value={LIVE_DATA}>Live production data</option>
            {datasets.data?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} ({d.row_count.toLocaleString()} rows)
              </option>
            ))}
          </select>
        </label>
      </p>
      <form onSubmit={onSubmit}>
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={
            dataSource
              ? "e.g. what is the total of <column>?"
              : "e.g. how's my inventory looking?"
          }
          style={{ width: "28rem" }}
        />
        <button type="submit" disabled={ask.isPending}>
          {ask.isPending ? "Asking..." : "Ask"}
        </button>
      </form>
      {!dataSource && (
        <p>
          Try:{" "}
          {EXAMPLES.map((ex, i) => (
            <span key={ex}>
              {i > 0 && " · "}
              <button type="button" onClick={() => setQuestion(ex)}>
                {ex}
              </button>
            </span>
          ))}
        </p>
      )}

      {error && <p style={{ color: "red" }}>{error}</p>}

      {ask.data && (
        <div>
          <p>
            <strong>Intents:</strong> {ask.data.intents.join(", ")}
          </p>
          <p>{ask.data.narrative}</p>
          {ask.data.warnings.length > 0 && (
            <ul>
              {ask.data.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
