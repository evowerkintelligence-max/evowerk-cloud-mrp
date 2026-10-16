import type { Dispatch, SetStateAction } from "react";
import type { useCharts, useDatasets } from "../analytics/shared";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { BULLET_TYPES, QUERY_TYPES, SECTION_TYPES, TEXT_TYPES, type SectionDraft, type SectionType } from "./types";

export function SectionDialog({
  editingSectionIndex,
  setEditingSectionIndex,
  sections,
  sectionDraft,
  setSectionDraft,
  datasets,
  charts,
  queryJsonError,
  confirmSection,
}: {
  editingSectionIndex: number | null;
  setEditingSectionIndex: (i: number | null) => void;
  sections: Record<string, unknown>[];
  sectionDraft: SectionDraft;
  setSectionDraft: Dispatch<SetStateAction<SectionDraft>>;
  datasets: ReturnType<typeof useDatasets>;
  charts: ReturnType<typeof useCharts>;
  queryJsonError: string | null;
  confirmSection: () => void;
}) {
  return (
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
  );
}
