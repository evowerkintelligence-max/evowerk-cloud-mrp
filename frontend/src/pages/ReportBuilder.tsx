import { useMemo, useState } from "react";
import { FileStack } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { useCharts, useCurrentUser, useDatasets, useInbox, type InboxItem } from "./analytics/shared";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { Dialog } from "@/components/ui/dialog";

const SECTION_TYPES = [
  "title", "exec_summary", "table", "bi_chart",
  "findings", "risks", "recommendations", "caveats", "text",
] as const;

type SectionType = (typeof SECTION_TYPES)[number];

const TEXT_TYPES = new Set<SectionType>(["title", "exec_summary", "text"]);
const BULLET_TYPES = new Set<SectionType>(["findings", "risks", "recommendations", "caveats"]);
const QUERY_TYPES = new Set<SectionType>(["table"]);

interface SectionDraft {
  type: SectionType;
  title: string;
  text: string; // TEXT_TYPES
  itemsText: string; // BULLET_TYPES, newline-separated
  dataset_id: string; // QUERY_TYPES
  queryText: string; // QUERY_TYPES, DatasetQueryRequest JSON
  chart_id: string; // bi_chart
}

interface SavedReportSummary {
  id: string;
  name: string;
  sections: Record<string, unknown>[];
  updated_at: string;
}

const NARRATION_STYLES = ["narrative", "bullets", "brief"] as const;
type NarrationStyle = (typeof NARRATION_STYLES)[number];

interface SectionNarrative {
  title: string;
  narrative: string;
}

interface ReportNarration {
  executive_summary: string;
  sections: SectionNarrative[];
  error: string | null;
}

function blankSection(): SectionDraft {
  return { type: "text", title: "", text: "", itemsText: "", dataset_id: "", queryText: '{"select":[]}', chart_id: "" };
}

function draftFromSection(s: Record<string, unknown>): SectionDraft {
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

function sectionToPayload(d: SectionDraft): Record<string, unknown> {
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

function sectionSummary(s: Record<string, unknown>): string {
  if (TEXT_TYPES.has(s.type as SectionType)) return String(s.text ?? "").slice(0, 60);
  if (BULLET_TYPES.has(s.type as SectionType)) return `${(s.items as unknown[] | undefined)?.length ?? 0} item(s)`;
  if (QUERY_TYPES.has(s.type as SectionType)) return `dataset ${s.dataset_id ?? "?"}`;
  return `chart ${s.chart_id ?? "?"}`;
}

export function ReportBuilder() {
  const qc = useQueryClient();
  const datasets = useDatasets();
  const charts = useCharts();

  const [editingId, setEditingId] = useState<string | "new" | null>(null);
  const [name, setName] = useState("");
  const [sections, setSections] = useState<Record<string, unknown>[]>([]);
  const [editingSectionIndex, setEditingSectionIndex] = useState<number | null>(null);
  const [sectionDraft, setSectionDraft] = useState<SectionDraft>(blankSection());
  const [error, setError] = useState<string | null>(null);
  const [narrationStyle, setNarrationStyle] = useState<NarrationStyle>("narrative");
  const [narration, setNarration] = useState<ReportNarration | null>(null);
  const inbox = useInbox();
  const me = useCurrentUser();
  const [showInboxPicker, setShowInboxPicker] = useState(false);
  const [checkedInboxIds, setCheckedInboxIds] = useState<Set<string>>(new Set());

  const queryJsonError = useMemo(() => {
    if (!QUERY_TYPES.has(sectionDraft.type)) return null;
    try {
      JSON.parse(sectionDraft.queryText || "{}");
      return null;
    } catch (e) {
      return (e as Error).message;
    }
  }, [sectionDraft.type, sectionDraft.queryText]);

  const reports = useQuery<SavedReportSummary[]>({
    queryKey: ["saved-reports"],
    queryFn: async () => {
      const res = await apiFetch("/reports/custom");
      if (!res.ok) throw new Error("Failed to load reports");
      return res.json();
    },
  });

  function startNew() {
    setEditingId("new");
    setName("");
    setSections([]);
    setError(null);
  }

  function startEdit(r: SavedReportSummary) {
    setEditingId(r.id);
    setName(r.name);
    setSections(r.sections);
    setError(null);
  }

  const save = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(editingId === "new" ? "/reports/custom" : `/reports/custom/${editingId}`, {
        method: editingId === "new" ? "POST" : "PUT",
        body: JSON.stringify({ name, sections }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Save failed");
      return res.json() as Promise<SavedReportSummary>;
    },
    onSuccess: (r) => {
      setError(null);
      setEditingId(r.id);
      qc.invalidateQueries({ queryKey: ["saved-reports"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/reports/custom/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Delete failed");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved-reports"] }),
  });

  const deleteInboxItem = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/reports/inbox/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Delete failed");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["report-inbox"] }),
  });

  function canDeleteInboxItem(item: InboxItem) {
    return item.created_by === me.data?.user_id || me.data?.role === "owner";
  }

  function openAddSection() {
    setSectionDraft(blankSection());
    setEditingSectionIndex(sections.length);
  }

  function addFromInbox() {
    const chosen = (inbox.data ?? []).filter((item) => checkedInboxIds.has(item.id));
    setSections((prev) => [...prev, ...chosen.map((item) => ({ ...item.spec, title: item.spec.title ?? item.label }))]);
    setCheckedInboxIds(new Set());
    setShowInboxPicker(false);
  }

  function openEditSection(i: number) {
    setSectionDraft(draftFromSection(sections[i]));
    setEditingSectionIndex(i);
  }

  function confirmSection() {
    try {
      const payload = sectionToPayload(sectionDraft);
      setSections((prev) => {
        const next = [...prev];
        if (editingSectionIndex !== null && editingSectionIndex < prev.length) next[editingSectionIndex] = payload;
        else next.push(payload);
        return next;
      });
      setEditingSectionIndex(null);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function moveSection(i: number, dir: -1 | 1) {
    setSections((prev) => {
      const next = [...prev];
      const j = i + dir;
      if (j < 0 || j >= next.length) return prev;
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }

  const narrate = useMutation({
    mutationFn: async () => {
      if (!editingId || editingId === "new") throw new Error("Save the report first.");
      const res = await apiFetch(`/reports/custom/${editingId}/narrate?style=${narrationStyle}`, { method: "POST" });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Narration failed");
      return res.json() as Promise<ReportNarration>;
    },
    onSuccess: (n) => {
      setError(null);
      setNarration(n);
    },
    onError: (e: Error) => setError(e.message),
  });

  function useAsExecSummary() {
    if (!narration) return;
    setSections((prev) => {
      const section = { type: "exec_summary", title: "Executive Summary", text: narration.executive_summary };
      const idx = prev.findIndex((s) => s.type === "exec_summary");
      if (idx >= 0) return prev.map((s, i) => (i === idx ? section : s));
      return [section, ...prev];
    });
  }

  function addNarrativeAsSection(sn: SectionNarrative) {
    setSections((prev) => [...prev, { type: "text", title: sn.title, text: sn.narrative }]);
  }

  async function download(format: "html" | "pdf" | "csv" | "xlsx") {
    if (!editingId || editingId === "new") return;
    const res = await apiFetch(`/reports/custom/${editingId}/render?format=${format}`);
    if (!res.ok) {
      setError("Render failed — save the report first.");
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name || "report"}.${format}`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (editingId === null) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Report Builder"
          description="Compose a report from tables, BI charts, and narrative sections, then export it as HTML, PDF or CSV."
          actions={<Button onClick={startNew}>New report</Button>}
        />

        {reports.data && reports.data.length === 0 ? (
          <EmptyState icon={<FileStack />} title="No saved reports yet" description="Build one from your datasets and BI charts." />
        ) : (
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Sections</TH>
                  <TH>Updated</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {reports.data?.map((r) => (
                  <TR key={r.id}>
                    <TD className="font-medium">
                      <button className="hover:underline" onClick={() => startEdit(r)}>{r.name}</button>
                    </TD>
                    <TD className="text-muted-foreground">{r.sections.length}</TD>
                    <TD className="text-muted-foreground">{new Date(r.updated_at).toLocaleString()}</TD>
                    <TD className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button variant="secondary" size="sm" onClick={() => startEdit(r)}>Edit</Button>
                        <Button variant="destructive" size="sm" onClick={() => remove.mutate(r.id)}>Delete</Button>
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={editingId === "new" ? "New report" : name || "Report"}
        description="Add sections in the order you want them to appear, then save and export."
        actions={
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" onClick={() => setEditingId(null)}>Back to list</Button>
            <Button loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
            {editingId !== "new" && (
              <>
                <Button variant="secondary" onClick={() => download("html")}>Export HTML</Button>
                <Button variant="secondary" onClick={() => download("pdf")}>Export PDF</Button>
                <Button variant="secondary" onClick={() => download("csv")}>Export CSV</Button>
                <Button variant="secondary" onClick={() => download("xlsx")}>Export XLSX</Button>
                <Select
                  className="w-auto"
                  value={narrationStyle}
                  onChange={(e) => setNarrationStyle(e.target.value as NarrationStyle)}
                >
                  {NARRATION_STYLES.map((s) => (
                    <option key={s} value={s}>
                      {s === "narrative" ? "Paragraph" : s === "bullets" ? "Bullet points" : "Brief"}
                    </option>
                  ))}
                </Select>
                <Button variant="secondary" loading={narrate.isPending} onClick={() => narrate.mutate()}>
                  Narrate
                </Button>
              </>
            )}
          </div>
        }
      />

      {error && <Alert variant="destructive">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>Name</CardTitle>
        </CardHeader>
        <CardContent>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Weekly ops summary" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Sections</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {sections.length === 0 && <p className="text-sm text-muted-foreground">No sections yet.</p>}
          {sections.map((s, i) => (
            <div key={i} className="flex items-center justify-between gap-3 rounded-md border border-border px-4 py-2.5">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Badge variant="neutral">{String(s.type)}</Badge>
                  <span className="truncate font-medium">{(s.title as string) || "Untitled"}</span>
                </div>
                <div className="truncate text-xs text-muted-foreground">{sectionSummary(s)}</div>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button variant="ghost" size="sm" disabled={i === 0} onClick={() => moveSection(i, -1)}>Up</Button>
                <Button variant="ghost" size="sm" disabled={i === sections.length - 1} onClick={() => moveSection(i, 1)}>Down</Button>
                <Button variant="secondary" size="sm" onClick={() => openEditSection(i)}>Edit</Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setSections((prev) => prev.filter((_, idx) => idx !== i))}
                >
                  Remove
                </Button>
              </div>
            </div>
          ))}
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={openAddSection}>+ Add section</Button>
            <Button variant="secondary" size="sm" onClick={() => setShowInboxPicker(true)}>Add from inbox</Button>
          </div>
        </CardContent>
      </Card>

      {narration && (
        <Card>
          <CardHeader>
            <CardTitle>AI narrative ({narrationStyle})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {narration.error && (
              <Alert variant="warning">AI narration was unavailable — showing a computed fallback summary.</Alert>
            )}
            <div className="rounded-md border border-border bg-secondary/20 p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="text-sm font-semibold">Executive summary</span>
                <Button variant="ghost" size="sm" onClick={useAsExecSummary}>Use as Exec Summary section</Button>
              </div>
              <p className="whitespace-pre-line text-sm text-muted-foreground">{narration.executive_summary}</p>
            </div>
            {narration.sections.map((sn, i) => (
              <div key={i} className="rounded-md border border-border bg-secondary/20 p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold">{sn.title}</span>
                  <Button variant="ghost" size="sm" onClick={() => addNarrativeAsSection(sn)}>Add as text section</Button>
                </div>
                <p className="whitespace-pre-line text-sm text-muted-foreground">{sn.narrative}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Dialog
        open={editingSectionIndex !== null}
        onClose={() => setEditingSectionIndex(null)}
        title={editingSectionIndex !== null && editingSectionIndex < sections.length ? "Edit section" : "Add section"}
      >
        <div className="space-y-4">
          <Field label="Type">
            <Select
              value={sectionDraft.type}
              onChange={(e) => setSectionDraft((d) => ({ ...d, type: e.target.value as SectionType }))}
            >
              {SECTION_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </Select>
          </Field>

          {sectionDraft.type !== "title" && (
            <Field label="Title" hint="optional">
              <Input value={sectionDraft.title} onChange={(e) => setSectionDraft((d) => ({ ...d, title: e.target.value }))} />
            </Field>
          )}
          {sectionDraft.type === "title" && (
            <Field label="Title">
              <Input value={sectionDraft.title} onChange={(e) => setSectionDraft((d) => ({ ...d, title: e.target.value }))} />
            </Field>
          )}

          {TEXT_TYPES.has(sectionDraft.type) && (
            <Field label="Text">
              <textarea
                className="h-28 w-full rounded-md border border-input bg-background/60 p-2 text-sm"
                value={sectionDraft.text}
                onChange={(e) => setSectionDraft((d) => ({ ...d, text: e.target.value }))}
              />
            </Field>
          )}

          {BULLET_TYPES.has(sectionDraft.type) && (
            <Field label="Items" hint="one per line">
              <textarea
                className="h-28 w-full rounded-md border border-input bg-background/60 p-2 text-sm"
                value={sectionDraft.itemsText}
                onChange={(e) => setSectionDraft((d) => ({ ...d, itemsText: e.target.value }))}
              />
            </Field>
          )}

          {QUERY_TYPES.has(sectionDraft.type) && (
            <>
              <Field label="Dataset">
                <Select
                  value={sectionDraft.dataset_id}
                  onChange={(e) => setSectionDraft((d) => ({ ...d, dataset_id: e.target.value }))}
                >
                  <option value="">Select a dataset…</option>
                  {datasets.data?.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Query (JSON)"
                hint='DatasetQueryRequest shape, e.g. {"select":[{"column":"sku"},{"column":"qty","agg":"sum","alias":"total_qty"}],"group_by":["sku"]}'
              >
                <textarea
                  className={`h-32 w-full rounded-md border bg-background/60 p-2 font-mono text-xs ${
                    queryJsonError ? "border-destructive" : "border-input"
                  }`}
                  value={sectionDraft.queryText}
                  onChange={(e) => setSectionDraft((d) => ({ ...d, queryText: e.target.value }))}
                />
                {queryJsonError && <p className="mt-1 text-xs text-destructive">Invalid JSON: {queryJsonError}</p>}
              </Field>
            </>
          )}

          {sectionDraft.type === "bi_chart" && (
            <Field label="Chart">
              <Select
                value={sectionDraft.chart_id}
                onChange={(e) => setSectionDraft((d) => ({ ...d, chart_id: e.target.value }))}
              >
                <option value="">Select a chart…</option>
                {charts.data?.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </Select>
            </Field>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setEditingSectionIndex(null)}>Cancel</Button>
            <Button disabled={!!queryJsonError} onClick={confirmSection}>Save section</Button>
          </div>
        </div>
      </Dialog>

      <Dialog open={showInboxPicker} onClose={() => setShowInboxPicker(false)} title="Add from inbox">
        <div className="space-y-3">
          {deleteInboxItem.isError && (
            <Alert variant="destructive">{(deleteInboxItem.error as Error).message}</Alert>
          )}
          {(inbox.data ?? []).length === 0 && (
            <p className="text-sm text-muted-foreground">
              Nothing captured yet — use "Send to report" on any analytics page, BI chart, or RCA case.
            </p>
          )}
          {(inbox.data ?? []).map((item: InboxItem) => (
            <div key={item.id} className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
              <label className="flex min-w-0 items-center gap-2">
                <input
                  type="checkbox"
                  checked={checkedInboxIds.has(item.id)}
                  onChange={(e) => {
                    setCheckedInboxIds((prev) => {
                      const next = new Set(prev);
                      if (e.target.checked) next.add(item.id);
                      else next.delete(item.id);
                      return next;
                    });
                  }}
                />
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Badge variant="neutral">{item.spec.type}</Badge>
                    <span className="truncate font-medium">{item.label}</span>
                  </div>
                  <div className="text-xs text-muted-foreground">{new Date(item.created_at).toLocaleString()}</div>
                </div>
              </label>
              {canDeleteInboxItem(item) && (
                <Button variant="ghost" size="sm" onClick={() => deleteInboxItem.mutate(item.id)}>Delete</Button>
              )}
            </div>
          ))}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setShowInboxPicker(false)}>Cancel</Button>
            <Button disabled={checkedInboxIds.size === 0} onClick={addFromInbox}>
              Add {checkedInboxIds.size || ""} selected
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
