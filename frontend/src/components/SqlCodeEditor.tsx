import { useMemo } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { sql, PostgreSQL, keywordCompletionSource } from "@codemirror/lang-sql";
import { autocompletion, type CompletionContext, type CompletionResult } from "@codemirror/autocomplete";
import { sqlEditorExtensions } from "./SqlCodeEditor.theme";

export type SqlColumnDtype = "text" | "numeric" | "datetime";

export interface SqlColumn {
  name: string;
  dtype: SqlColumnDtype;
}

function castedColumnToken(name: string, dtype: SqlColumnDtype): string {
  if (dtype === "numeric") return `CAST(${name} AS DOUBLE)`;
  if (dtype === "datetime") return `CAST(${name} AS TIMESTAMP)`;
  return name;
}

function columnCompletionSource(columns: SqlColumn[]) {
  return (context: CompletionContext): CompletionResult | null => {
    const word = context.matchBefore(/\w*/);
    if (!word || (word.from === word.to && !context.explicit)) return null;
    return {
      from: word.from,
      validFor: /^\w*$/,
      options: columns.map((c) => ({
        label: c.name,
        type: "variable",
        detail: c.dtype,
        boost: 1,
        apply: castedColumnToken(c.name, c.dtype),
      })),
    };
  };
}

export function SqlCodeEditor({
  value,
  onChange,
  columns,
}: {
  value: string;
  onChange: (next: string) => void;
  columns: SqlColumn[];
}) {
  const extensions = useMemo(
    () => [
      sql({ dialect: PostgreSQL }),
      autocompletion({ override: [keywordCompletionSource(PostgreSQL), columnCompletionSource(columns)] }),
      ...sqlEditorExtensions,
    ],
    [columns],
  );

  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      height="12rem"
      basicSetup={{ lineNumbers: false, foldGutter: false, autocompletion: false }}
      extensions={extensions}
      className="overflow-hidden rounded-md border border-border text-sm"
    />
  );
}
