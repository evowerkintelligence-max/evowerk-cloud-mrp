export const SECTION_TYPES = [
  "title", "exec_summary", "table", "bi_chart",
  "findings", "risks", "recommendations", "caveats", "text",
] as const;

export type SectionType = (typeof SECTION_TYPES)[number];

export const TEXT_TYPES = new Set<SectionType>(["title", "exec_summary", "text"]);
export const BULLET_TYPES = new Set<SectionType>(["findings", "risks", "recommendations", "caveats"]);
export const QUERY_TYPES = new Set<SectionType>(["table"]);

export interface SectionDraft {
  type: SectionType;
  title: string;
  text: string; // TEXT_TYPES
  itemsText: string; // BULLET_TYPES, newline-separated
  dataset_id: string; // QUERY_TYPES
  queryText: string; // QUERY_TYPES, DatasetQueryRequest JSON
  chart_id: string; // bi_chart
}

export interface SavedReportSummary {
  id: string;
  name: string;
  sections: Record<string, unknown>[];
  updated_at: string;
}

export const NARRATION_STYLES = ["narrative", "bullets", "brief"] as const;
export type NarrationStyle = (typeof NARRATION_STYLES)[number];

export interface SectionNarrative {
  title: string;
  narrative: string;
}

export interface ReportNarration {
  executive_summary: string;
  sections: SectionNarrative[];
  error: string | null;
}

export function blankSection(): SectionDraft {
  return { type: "text", title: "", text: "", itemsText: "", dataset_id: "", queryText: '{"select":[]}', chart_id: "" };
}

export function draftFromSection(s: Record<string, unknown>): SectionDraft {
  const type = (s.type as SectionType) ?? "text";
  return {
    type,
    title: (s.title as string) ?? "",
    text: (s.text as string) ?? "",
    itemsText: Array.isArray(s.items) ? (s.items as string[]).join("\n") : "",
    dataset_id: (s.dataset_id as string) ?? "",
    queryText: s.query ? JSON.stringify(s.query, null, 2) : '{"select":[]}',
    chart_id: (s.chart_id as string) ?? "",
  };
}

export function sectionToPayload(d: SectionDraft): Record<string, unknown> {
  const base: Record<string, unknown> = { type: d.type, title: d.title || undefined };
  if (TEXT_TYPES.has(d.type)) return { ...base, text: d.text };
  if (BULLET_TYPES.has(d.type)) {
    return { ...base, items: d.itemsText.split("\n").map((s) => s.trim()).filter(Boolean) };
  }
  if (QUERY_TYPES.has(d.type)) {
    let query: unknown;
    try {
      query = JSON.parse(d.queryText || "{}");
    } catch {
      throw new Error(`Section "${d.title || d.type}" has invalid query JSON`);
    }
    return { ...base, dataset_id: d.dataset_id, query };
  }
  return { ...base, chart_id: d.chart_id };
}

export function sectionSummary(s: Record<string, unknown>): string {
  if (TEXT_TYPES.has(s.type as SectionType)) return String(s.text ?? "").slice(0, 60);
  if (BULLET_TYPES.has(s.type as SectionType)) return `${(s.items as unknown[] | undefined)?.length ?? 0} item(s)`;
  if (QUERY_TYPES.has(s.type as SectionType)) return `dataset ${s.dataset_id ?? "?"}`;
  return `chart ${s.chart_id ?? "?"}`;
}
