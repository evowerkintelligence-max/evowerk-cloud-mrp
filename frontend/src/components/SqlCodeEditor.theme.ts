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
