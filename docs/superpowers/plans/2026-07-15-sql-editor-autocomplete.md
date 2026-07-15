# SQL Editor Syntax Highlighting + Autocomplete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plain `<textarea>` + click-to-insert column chips on both SQL surfaces (`SqlEditor.tsx` top-level page, `DatasetDetail.tsx`'s `SqlPanel` tab) with a shared CodeMirror 6 editor: SQL syntax highlighting, keyword autocomplete, and schema-aware column autocomplete that casts numeric/datetime columns on accept.

**Architecture:** One new shared component (`frontend/src/components/SqlCodeEditor.tsx`) plus a small theme file (`frontend/src/components/SqlCodeEditor.theme.ts`) that reads the app's existing CSS custom properties so it follows the app's existing light/dark toggle with zero new theme-switching logic. `SqlCodeEditor` is a pure/controlled component - it takes `value`, `onChange`, and a `columns: SqlColumn[]` prop; it fetches nothing itself. Both pages keep their existing `["dataset-health", ...]` query (already wired up from a prior fix) and pass the column+dtype list down instead of rendering chip buttons. Column completions are produced by a custom `CompletionSource` (not the `sql()` extension's built-in `schema` option, which doesn't support cast-wrapping) combined with `@codemirror/lang-sql`'s own `keywordCompletionSource` via `autocompletion({ override: [...] })`, so keyword and column completions both appear in one dropdown.

**Tech Stack:** React 18 + TypeScript + Vite 5 (existing), `@uiw/react-codemirror` + `@codemirror/lang-sql` + `@codemirror/autocomplete` + `@codemirror/language` + `@codemirror/view` + `@lezer/highlight` (new). No frontend test runner exists in this repo - verification is `tsc -b`/`tsc --noEmit` plus manual browser checks via the dev server.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-15-sql-editor-autocomplete-design.md` - source of truth if this plan and the spec disagree.
- SQL dialect is `PostgreSQL` (from `@codemirror/lang-sql`), not the default StandardSQL - the backend (`backend/app/services/sql_query.py`) runs DuckDB, whose grammar tracks Postgres closely enough to be the right default for both highlighting and keyword completion. Exact value from the spec's "Library choice" section.
- Numeric columns cast to `CAST(col AS DOUBLE)`, datetime columns to `CAST(col AS TIMESTAMP)`, text columns insert bare - exact strings, matching the prior chip-based fix so query results/behavior don't change, only how the SQL gets typed.
- No backend changes anywhere in this plan. `/datasets/{id}/sql` and its error handling are untouched.
- No new test runner (Vitest, Jest) gets added - verification is `tsc` + manual browser checks only, per the spec's Testing section.
- Frontend commands run from the `frontend/` directory.
- **One file, one task.** Tasks are ordered so no file is reopened by a later task.

---

### Task 1: Install CodeMirror dependencies and build the theme

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/src/components/SqlCodeEditor.theme.ts`

**Interfaces:**
- Produces: `sqlEditorExtensions` - an array of CodeMirror `Extension`s (a base `EditorView.theme()` plus a `syntaxHighlighting(HighlightStyle)`), exported from `SqlCodeEditor.theme.ts` for Task 2 to spread into its own extensions array.

This is a setup task with no user-facing behavior yet (nothing imports this file). Verification is dependency install + typecheck, same pattern as any schema-only/foundation task.

- [ ] **Step 1: Install the CodeMirror packages**

Run (from `frontend/`):

```bash
npm install @uiw/react-codemirror @codemirror/lang-sql @codemirror/autocomplete @codemirror/language @codemirror/view @lezer/highlight
```

Expected: command exits 0; `frontend/package.json`'s `dependencies` block gains six new entries (exact versions are whatever npm resolves as latest-compatible - don't hand-pin different ones).

- [ ] **Step 2: Verify the install**

Run: `git diff package.json` (from `frontend/`)
Expected: six new lines under `"dependencies"` for `@codemirror/autocomplete`, `@codemirror/lang-sql`, `@codemirror/language`, `@codemirror/view`, `@lezer/highlight`, `@uiw/react-codemirror`.

- [ ] **Step 3: Write the theme file**

Create `frontend/src/components/SqlCodeEditor.theme.ts`:

```typescript
import { EditorView } from "@codemirror/view";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";

// Every color below is one of this app's existing design-system CSS custom
// properties (see frontend/src/index.css) referenced via hsl(var(--x)) - the
// editor follows the app's existing class-based dark/light toggle with no
// CodeMirror-side theme branching, because the browser resolves var(--x)
// against whichever :root/.dark block is active at render time.
const baseTheme = EditorView.theme({
  "&": {
    color: "hsl(var(--foreground))",
    backgroundColor: "hsl(var(--background))",
    fontSize: "0.875rem",
  },
  ".cm-content": {
    caretColor: "hsl(var(--foreground))",
  },
  "&.cm-focused .cm-cursor": {
    borderLeftColor: "hsl(var(--foreground))",
  },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
    backgroundColor: "hsl(var(--primary) / 0.25) !important",
  },
  ".cm-gutters": {
    backgroundColor: "hsl(var(--muted))",
    color: "hsl(var(--muted-foreground))",
    border: "none",
  },
  ".cm-activeLine": {
    backgroundColor: "hsl(var(--muted) / 0.35)",
  },
  ".cm-activeLineGutter": {
    backgroundColor: "hsl(var(--muted) / 0.6)",
  },
  ".cm-tooltip": {
    backgroundColor: "hsl(var(--card))",
    border: "1px solid hsl(var(--border))",
    color: "hsl(var(--foreground))",
  },
  ".cm-tooltip-autocomplete ul li[aria-selected]": {
    backgroundColor: "hsl(var(--primary) / 0.15) !important",
    color: "hsl(var(--foreground)) !important",
  },
});

const highlightStyle = HighlightStyle.define([
  { tag: [t.keyword, t.bool], color: "hsl(var(--primary))", fontWeight: 600 },
  { tag: [t.string, t.special(t.string)], color: "hsl(var(--success))" },
  { tag: [t.number, t.typeName], color: "hsl(var(--accent))" },
  { tag: t.comment, color: "hsl(var(--muted-foreground))", fontStyle: "italic" },
  { tag: [t.operator, t.punctuation], color: "hsl(var(--muted-foreground))" },
]);

export const sqlEditorExtensions = [baseTheme, syntaxHighlighting(highlightStyle)];
```

- [ ] **Step 4: Typecheck**

Run (from `frontend/`): `npx tsc -b`
Expected: exits 0, no errors. (This is the first real test that the six packages installed correctly and their type declarations resolve.)

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/components/SqlCodeEditor.theme.ts
git commit -m "feat: add CodeMirror dependencies and SQL editor theme"
```

---

### Task 2: Build SqlCodeEditor and wire it into the top-level SQL Editor page

**Files:**
- Create: `frontend/src/components/SqlCodeEditor.tsx`
- Modify: `frontend/src/pages/SqlEditor.tsx`

**Interfaces:**
- Consumes: `sqlEditorExtensions` from `./SqlCodeEditor.theme` (Task 1).
- Produces: `SqlCodeEditor({ value: string; onChange: (next: string) => void; columns: SqlColumn[] })` React component; `SqlColumn { name: string; dtype: SqlColumnDtype }`; `SqlColumnDtype = "text" | "numeric" | "datetime"` - all exported from `frontend/src/components/SqlCodeEditor.tsx`. Task 3 imports all three names from this file.

- [ ] **Step 1: Write SqlCodeEditor.tsx**

Create `frontend/src/components/SqlCodeEditor.tsx`:

```tsx
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
```

`basicSetup`'s `autocompletion: false` disables `@uiw/react-codemirror`'s own default autocompletion extension - without this, the editor would register two separate `autocompletion()` extensions (the wrapper's default and this component's custom one), and only one would end up handling completions, silently dropping the other's results. `lineNumbers: false` and `foldGutter: false` match the plain textarea's prior look (no gutter chrome) - this is an ~8-line SQL box, not a multi-file editor.

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npx tsc -b`
Expected: exits 0, no errors.

- [ ] **Step 3: Replace SqlEditor.tsx's textarea + chips with SqlCodeEditor**

The whole file becomes:

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { SqlCodeEditor, type SqlColumn, type SqlColumnDtype } from "@/components/SqlCodeEditor";
import { useDatasets } from "@/pages/analytics/shared";

interface QueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
}

interface HistoryEntry {
  id: string;
  action: string;
  dataset_id: string | null;
  sql: string | null;
  created_at: string;
}

interface HealthColumn {
  name: string;
  inferred_dtype: string;
}

const DEFAULT_SQL = "SELECT * FROM dataset LIMIT 100";

export function SqlEditor() {
  const qc = useQueryClient();
  const datasets = useDatasets();
  const [datasetId, setDatasetId] = useState("");
  const [sql, setSql] = useState(DEFAULT_SQL);

  const activeDataset = datasets.data?.find((d) => d.id === datasetId) ?? datasets.data?.[0];
  const effectiveDatasetId = datasetId || activeDataset?.id || "";

  // Same query key as DatasetDetail's Schema/SQL tabs, so switching to a dataset
  // already viewed there is a no-op fetch. inferred_dtype (not column.dtype, which
  // is always VARCHAR for CSV/TSV) is what tells us a column needs CAST(...AS DOUBLE).
  const health = useQuery<{ columns: HealthColumn[] }>({
    queryKey: ["dataset-health", effectiveDatasetId],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${effectiveDatasetId}/health`);
      if (!res.ok) throw new Error("Failed to compute health");
      return res.json();
    },
    enabled: !!effectiveDatasetId,
  });
  const inferredByColumn = new Map(health.data?.columns.map((c) => [c.name, c.inferred_dtype]));
  const sqlColumns: SqlColumn[] = (activeDataset?.columns ?? []).map((c) => ({
    name: c.name,
    dtype: (inferredByColumn.get(c.name) as SqlColumnDtype | undefined) ?? "text",
  }));

  const history = useQuery<HistoryEntry[]>({
    queryKey: ["query-history"],
    queryFn: async () => {
      const res = await apiFetch("/datasets/query-history?limit=20");
      if (!res.ok) throw new Error("Failed to load history");
      return res.json();
    },
  });
  const sqlHistory = (history.data ?? []).filter((h) => h.action === "dataset.sql_query");

  const query = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/datasets/${effectiveDatasetId}/sql`, {
        method: "POST",
        body: JSON.stringify({ sql, limit: 500 }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Query failed");
      return res.json() as Promise<QueryResult>;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["query-history"] }),
  });

  function rerun(entry: HistoryEntry) {
    if (entry.dataset_id) setDatasetId(entry.dataset_id);
    if (entry.sql) setSql(entry.sql);
  }

  const deleteEntry = useMutation({
    mutationFn: (id: string) => apiFetch(`/datasets/query-history/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["query-history"] }),
  });

  const clearHistory = useMutation({
    mutationFn: () => apiFetch("/datasets/query-history?action=dataset.sql_query", { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["query-history"] }),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="SQL Editor"
        description="Run a read-only SELECT against any uploaded dataset. Backed by a sandboxed DuckDB table named `dataset`."
      />

      <Card>
        <CardHeader>
          <CardTitle>Query</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {datasets.isLoading ? (
            <Skeleton className="h-10 w-64" />
          ) : (
            <div className="min-w-[16rem] space-y-1.5">
              <label className="text-sm font-medium text-muted-foreground">Dataset</label>
              <Select value={effectiveDatasetId} onChange={(e) => setDatasetId(e.target.value)}>
                {(datasets.data ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </div>
          )}

          <SqlCodeEditor value={sql} onChange={setSql} columns={sqlColumns} />
          <p className="text-xs text-muted-foreground">
            Runs against a single read-only table named <code>dataset</code> — SELECT only, no joins to other
            files. Start typing a column name for autocomplete — numeric/date columns insert pre-cast (
            <code>CAST(... AS DOUBLE)</code>).
          </p>
          <Button disabled={!effectiveDatasetId} loading={query.isPending} onClick={() => query.mutate()}>
            Run SQL
          </Button>
          {query.isError && <Alert variant="destructive">{(query.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {query.isSuccess && (
        <Card>
          <CardHeader>
            <CardTitle>Results ({query.data.rows.length} rows)</CardTitle>
          </CardHeader>
          <CardContent>
            <TableWrap className="max-h-[32rem] overflow-y-auto">
              <Table>
                <THead>
                  <TR>
                    {query.data.columns.map((c) => (
                      <TH key={c}>{c}</TH>
                    ))}
                  </TR>
                </THead>
                <TBody>
                  {query.data.rows.map((row, i) => (
                    <TR key={i}>
                      {query.data!.columns.map((c) => (
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
      )}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>History</CardTitle>
          {sqlHistory.length > 0 && (
            <Button variant="ghost" size="sm" onClick={() => clearHistory.mutate()}>
              Clear all
            </Button>
          )}
        </CardHeader>
        <CardContent className="space-y-2">
          {sqlHistory.length === 0 && (
            <p className="text-sm text-muted-foreground">Queries you run will show up here for quick rerun.</p>
          )}
          {sqlHistory.map((h) => (
            <div key={h.id} className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
              <div className="min-w-0">
                <code className="block truncate text-xs">{h.sql}</code>
                <span className="text-xs text-muted-foreground">{new Date(h.created_at).toLocaleString()}</span>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button variant="secondary" size="sm" onClick={() => rerun(h)}>
                  Rerun
                </Button>
                <Button variant="ghost" size="sm" onClick={() => deleteEntry.mutate(h.id)}>
                  Delete
                </Button>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
```

What changed from the current file: `useRef` import dropped (no longer needed - CodeMirror's completion mechanism handles insertion, not a manual ref); `Badge` import dropped (was only used by the chip row, which is gone); `insertColumn`/`textareaRef` deleted; the chip-rendering block and `<textarea>` replaced by `<SqlCodeEditor value={sql} onChange={setSql} columns={sqlColumns} />`; the dataset-picker `<div>` no longer needs the `flex flex-wrap items-end gap-4` wrapper since there's nothing beside it now; hint paragraph reworded from "Click a column above" to "Start typing a column name for autocomplete".

- [ ] **Step 4: Typecheck**

Run (from `frontend/`): `npx tsc -b`
Expected: exits 0, no errors (in particular, no "unused import" errors for the removed `useRef`/`Badge`).

- [ ] **Step 5: Manual browser verification**

Start the dev server (`npm run dev` from `frontend/`, and `backend-dev` per `.claude/launch.json`), log in, upload a small CSV if none exists, navigate to `/sql-editor` (must be reached via in-app link click, not a direct URL load/refresh - auth tokens are memory-only, see `frontend/src/store/authStore.ts`).

Check:
- The SQL box shows syntax-highlighted text (keywords like `SELECT`/`FROM` colored) instead of a plain textarea.
- Typing a numeric column's name (partial, e.g. first 2-3 letters) pops an autocomplete list showing the column with its dtype; accepting it inserts `CAST(colname AS DOUBLE)`.
- Typing a SQL keyword fragment (e.g. `SEL`) also completes to `SELECT`.
- Toggle the app's dark/light mode (if there's a toggle in the UI) and confirm the editor's background/text colors follow.
- `Run SQL` still executes and returns results as before.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SqlCodeEditor.tsx frontend/src/pages/SqlEditor.tsx
git commit -m "feat: replace SQL Editor textarea with syntax-highlighting autocomplete editor"
```

---

### Task 3: Wire SqlCodeEditor into DatasetDetail's SqlPanel

**Files:**
- Modify: `frontend/src/pages/analytics/DatasetDetail.tsx`

**Interfaces:**
- Consumes: `SqlCodeEditor`, `type SqlColumn`, `type SqlColumnDtype` from `@/components/SqlCodeEditor` (Task 2).

- [ ] **Step 1: Drop the now-unused `useRef` import**

In `frontend/src/pages/analytics/DatasetDetail.tsx`, line 1:

```tsx
import { useRef, useState } from "react";
```

becomes:

```tsx
import { useState } from "react";
```

(`useRef` is used nowhere else in this file outside `SqlPanel` - confirmed by `grep -n "useRef" frontend/src/pages/analytics/DatasetDetail.tsx` returning only the import line and the one usage being replaced below.)

- [ ] **Step 2: Add the SqlCodeEditor import**

After line 6 (`import { PageHeader } from "@/components/PageHeader";`), add:

```tsx
import { SqlCodeEditor, type SqlColumn, type SqlColumnDtype } from "@/components/SqlCodeEditor";
```

(`Badge` on the existing line 8 stays - it's also used by `Schema` and `HealthPanel` in this same file, not just `SqlPanel`.)

- [ ] **Step 3: Replace SqlPanel's chip row + textarea**

Find this block (currently lines 946-1023):

```tsx
function SqlPanel({ d }: { d: DatasetDetail }) {
  const [sql, setSql] = useState(`SELECT * FROM dataset LIMIT 100`);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Same query key as Schema/HealthPanel, so this is a no-op fetch if either tab
  // was already visited. inferred_dtype (not c.dtype, which is always VARCHAR for
  // CSV/TSV) is what tells us a column actually needs CAST(... AS DOUBLE) in SQL.
  const health = useQuery<Health>({
    queryKey: ["dataset-health", d.id],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${d.id}/health`);
      if (!res.ok) throw new Error("Failed to compute health");
      return res.json();
    },
  });
  const inferredByColumn = new Map(health.data?.columns.map((c) => [c.name, c.inferred_dtype]));

  function insertColumn(name: string) {
    const dtype = inferredByColumn.get(name);
    const token =
      dtype === "numeric" ? `CAST(${name} AS DOUBLE)` : dtype === "datetime" ? `CAST(${name} AS TIMESTAMP)` : name;
    const el = textareaRef.current;
    const start = el?.selectionStart ?? sql.length;
    const end = el?.selectionEnd ?? sql.length;
    const next = sql.slice(0, start) + token + sql.slice(end);
    setSql(next);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(start + token.length, start + token.length);
    });
  }

  const query = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/datasets/${d.id}/sql`, {
        method: "POST",
        body: JSON.stringify({ sql, limit: 500 }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Query failed");
      return res.json() as Promise<QueryResult>;
    },
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>SQL</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-1.5">
            {d.columns.map((c) => (
              <button
                key={c.name}
                type="button"
                onClick={() => insertColumn(c.name)}
                title={`Insert ${c.name}`}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-muted px-2 py-1 font-mono text-xs hover:border-primary hover:text-primary"
              >
                {c.name}
                <Badge variant="neutral">{inferredByColumn.get(c.name) ?? c.dtype}</Badge>
              </button>
            ))}
          </div>
          <textarea
            ref={textareaRef}
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            rows={8}
            spellCheck={false}
            className="w-full rounded-md border border-border bg-background p-3 font-mono text-sm outline-none focus:ring-2 focus:ring-primary/40"
          />
          <p className="text-xs text-muted-foreground">
            Runs against a single read-only table named <code>dataset</code> — SELECT only, no joins to other
            files. Click a column above to insert it — numeric/date columns insert pre-cast (
            <code>CAST(... AS DOUBLE)</code>).
          </p>
```

Replace it with:

```tsx
function SqlPanel({ d }: { d: DatasetDetail }) {
  const [sql, setSql] = useState(`SELECT * FROM dataset LIMIT 100`);

  // Same query key as Schema/HealthPanel, so this is a no-op fetch if either tab
  // was already visited. inferred_dtype (not c.dtype, which is always VARCHAR for
  // CSV/TSV) is what tells us a column actually needs CAST(... AS DOUBLE) in SQL.
  const health = useQuery<Health>({
    queryKey: ["dataset-health", d.id],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${d.id}/health`);
      if (!res.ok) throw new Error("Failed to compute health");
      return res.json();
    },
  });
  const inferredByColumn = new Map(health.data?.columns.map((c) => [c.name, c.inferred_dtype]));
  const sqlColumns: SqlColumn[] = d.columns.map((c) => ({
    name: c.name,
    dtype: (inferredByColumn.get(c.name) as SqlColumnDtype | undefined) ?? "text",
  }));

  const query = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/datasets/${d.id}/sql`, {
        method: "POST",
        body: JSON.stringify({ sql, limit: 500 }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Query failed");
      return res.json() as Promise<QueryResult>;
    },
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>SQL</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <SqlCodeEditor value={sql} onChange={setSql} columns={sqlColumns} />
          <p className="text-xs text-muted-foreground">
            Runs against a single read-only table named <code>dataset</code> — SELECT only, no joins to other
            files. Start typing a column name for autocomplete — numeric/date columns insert pre-cast (
            <code>CAST(... AS DOUBLE)</code>).
          </p>
```

(The rest of `SqlPanel` - the `Run SQL` button, error `Alert`, and results `Card` below this block - is unchanged; this replacement only spans from the function signature through the hint paragraph.)

- [ ] **Step 4: Typecheck**

Run (from `frontend/`): `npx tsc -b`
Expected: exits 0, no errors.

- [ ] **Step 5: Manual browser verification**

With the dev server running (from Task 2), navigate to any dataset's detail page (`Datasets` -> open a dataset -> `sql` tab, reached via in-app clicks).

Check:
- Syntax highlighting renders, same as the top-level SQL Editor page.
- Typing a numeric column's name autocompletes to its `CAST(... AS DOUBLE)` form.
- `Run SQL` still executes and shows results as before.
- The `Schema` and `Health` tabs on the same page still render their column-dtype badges correctly (proves the shared `Badge` import wasn't broken by this file's other edits).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/analytics/DatasetDetail.tsx
git commit -m "feat: replace dataset SQL tab textarea with syntax-highlighting autocomplete editor"
```
