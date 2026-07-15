# SQL Editor Syntax Highlighting + Autocomplete

Status: Approved for planning
Date: 2026-07-15

## Problem

The two free-text SQL surfaces - `frontend/src/pages/SqlEditor.tsx` (the
top-level "SQL Editor" nav page) and `frontend/src/pages/analytics/
DatasetDetail.tsx`'s `SqlPanel` (the per-dataset "sql" tab) - are both a
plain `<textarea>`. A prior fix added click-to-insert column chips (showing
each column's `inferred_dtype` and pre-casting numeric/date columns on
insert) to soften "I don't remember this column's real type" and "I forgot
the CAST syntax." That's a workaround, not a fix: no highlighting, no
autocomplete, and the chips only help if the user thinks to click one before
typing.

This spec replaces the textarea + chips on both surfaces with a real
editor: SQL syntax highlighting plus autocomplete for SQL keywords and the
dataset's own columns (schema-aware, same cast-on-insert behavior as the
chips, now triggered by typing instead of clicking).

## Scope

In scope: one shared `SqlCodeEditor` component wrapping CodeMirror 6, used
by both surfaces; SQL keyword highlighting/completion (via
`@codemirror/lang-sql`); column-name completion sourced from each surface's
existing `inferred_dtype` data, casting numeric/datetime columns on accept;
a theme built from the app's existing CSS variables so it follows the
app's existing light/dark toggle with no new theme-switching logic.

Out of scope: table-name completion (there's exactly one fixed table,
`dataset`, already named in the hint text below the editor - nothing to
complete); multi-statement/multi-tab editing; query formatting/linting
beyond what `lang-sql` provides for free; a frontend test runner (see
Testing).

## Library choice

`@uiw/react-codemirror` + `@codemirror/lang-sql` (pulls in
`@codemirror/autocomplete` transitively). Chosen over Monaco: CodeMirror 6
is modular (tens of KB for what's used here, vs. Monaco's multi-MB + web
worker setup), and `lang-sql`'s `schema` option is built for exactly this
"autocomplete against a known table/column list" case. React 18 + Vite 5
(current stack) both have first-class support.

## Component contract

New file `frontend/src/components/SqlCodeEditor.tsx`:

```
<SqlCodeEditor
  value={sql}
  onChange={(next: string) => void}
  columns={{ name: string; dtype: "text" | "numeric" | "datetime" }[]}
/>
```

A pure/controlled component - it does not fetch anything itself. Both pages
keep their existing `useQuery(["dataset-health", ...])` call (already
wired up from the prior fix) and pass the resulting column+dtype list down
as a prop instead of rendering chip buttons from it.

Internals:
- `EditorView`/`EditorState` via `@uiw/react-codemirror`, extensions:
  `sql()` from `@codemirror/lang-sql` for keyword highlighting/completion,
  plus a custom `autocompletion({ override: [...] })` completion source
  layered on top for columns.
- Shared helper `castedColumnToken(name, dtype)` (extracted from the
  chip-insert logic added in the prior fix) used both by the completion
  source's `apply` string and anywhere else a cast-wrapped column reference
  is needed. Lives alongside `SqlCodeEditor.tsx` since it's only meaningful
  in that context.
- Theme: a custom CodeMirror `EditorView.theme()` / `HighlightStyle`
  reading the app's existing CSS custom properties (`--background`,
  `--foreground`, `--border`, `--primary`, `--muted`, etc. - confirmed
  present in `index.css` for both the `:root` and dark-class blocks) via
  `hsl(var(--x))`, so it re-renders correctly on the app's existing
  class-based dark-mode toggle with no CodeMirror-side light/dark branching.

## Completion behavior

- Matches the word being typed; offers SQL keywords (from `lang-sql`'s
  built-in list, untouched) and the active dataset's columns, with columns
  tagged/boosted to rank above generic keywords in the dropdown - the
  columns are what the user is actually here for.
- Each column completion's detail text shows its `inferred_dtype`, same
  information the chips surfaced.
- Accepting a `numeric`/`datetime` column inserts `CAST(col AS DOUBLE)` /
  `CAST(col AS TIMESTAMP)` in place of what was typed; `text` columns
  insert the bare name. CodeMirror's completion mechanism replaces the
  matched range directly - no manual cursor/selection bookkeeping needed,
  unlike the ref-based `insertColumn` helper from the prior fix, which is
  deleted (not kept alongside) along with the chip row markup in both
  files.
- Triggers automatically while typing (CodeMirror's default `autocompletion`
  behavior) and manually via Ctrl+Space.

## Error handling / edge cases

- `columns` empty or undefined (e.g. `SqlEditor.tsx` before its health
  query resolves, or no dataset selected/uploaded yet) - completion source
  falls back to keyword-only completions. No loading state needed inside
  `SqlCodeEditor` itself.
- No backend changes. The `/datasets/{id}/sql` endpoint and its error
  handling are unaffected by this spec.

## Files touched

- New: `frontend/src/components/SqlCodeEditor.tsx`
- `frontend/src/pages/SqlEditor.tsx`: replace textarea + chip row with
  `SqlCodeEditor`; drop `textareaRef`/`insertColumn`.
- `frontend/src/pages/analytics/DatasetDetail.tsx` (`SqlPanel`): same
  replacement.
- `frontend/package.json`: add `@uiw/react-codemirror`,
  `@codemirror/lang-sql`.

## Testing

No frontend test runner exists in this repo (`package.json` has only
`dev`/`build`/`preview`/`lint`). Adding Vitest for one small pure-function
helper (`castedColumnToken`) would be disproportionate to this change's
size - deliberate call, not an oversight. Verification is manual/browser:
`tsc -b` typecheck, then in both surfaces - type a partial numeric column
name and accept the completion to confirm the `CAST` wrap; confirm keyword
highlighting renders; toggle dark/light and confirm the editor's colors
follow.
