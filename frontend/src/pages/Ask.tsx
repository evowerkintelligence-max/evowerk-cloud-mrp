import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

interface AskResponse {
  question: string;
  intents: string[];
  narrative: string;
  warnings: string[];
}

const EXAMPLES = [
  "how's my inventory looking?",
  "why are items low, compare by supplier",
  "forecast expected demand for WIDGET-1",
  "what if WIDGET-1's lead time were 10 days?",
];

export function Ask() {
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<string | null>(null);

  const ask = useMutation({
    mutationFn: async (q: string) => {
      const res = await apiFetch("/ai/ask", { method: "POST", body: JSON.stringify({ question: q }) });
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
      <form onSubmit={onSubmit}>
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. how's my inventory looking?"
          style={{ width: "28rem" }}
        />
        <button type="submit" disabled={ask.isPending}>
          {ask.isPending ? "Asking..." : "Ask"}
        </button>
      </form>
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
