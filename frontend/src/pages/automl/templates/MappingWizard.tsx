import { useMemo, useState } from "react";
import { Field } from "@/components/Field";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { useDatasets } from "../../analytics/shared";
import { useCreateTemplateRun, type AutoMLTemplateSummary } from "../api";

/** Step 2 of the guided wizard: pick a Dataset, map each of the template's
 * declared roles to one of that dataset's columns (with an optional
 * friendly-label override), then create the run. Mirrors the dataset +
 * column-mapping form pattern already used by Scrap.tsx (EngineScreen) and
 * Predictive.tsx, generalized to whatever roles the chosen template declares. */
export function MappingWizard({
  template,
  onCreated,
  onBack,
}: {
  template: AutoMLTemplateSummary;
  onCreated: (runId: string) => void;
  onBack: () => void;
}) {
  const datasets = useDatasets();
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [columnMap, setColumnMap] = useState<Record<string, string>>({});
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const create = useCreateTemplateRun();

  const columns = useMemo(
    () => datasets.data?.find((d) => d.id === datasetId)?.columns.map((c) => c.name) ?? [],
    [datasets.data, datasetId],
  );

  function pickDataset(id: string) {
    setDatasetId(id);
    setColumnMap({});
  }

  const ready = Boolean(datasetId) && template.roles.every((r) => !r.required || columnMap[r.key]);

  function onSubmit() {
    if (!ready) {
      setError("Pick a dataset and map every required column first.");
      return;
    }
    setError(null);
    const friendlyLabels = Object.fromEntries(Object.entries(labels).filter(([, v]) => v.trim()));
    create.mutate(
      {
        templateId: template.id,
        body: {
          name: name || `${template.label} run`,
          dataset_id: datasetId,
          column_map: columnMap,
          config: Object.keys(friendlyLabels).length ? { friendly_labels: friendlyLabels } : {},
        },
      },
      {
        onSuccess: (r) => onCreated(r.id),
        onError: (e: Error) => setError(e.message),
      },
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{template.label}: dataset &amp; columns</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Run name">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={`${template.label} run`} />
            </Field>
            <Field label="Dataset">
              <Select value={datasetId} onChange={(e) => pickDataset(e.target.value)}>
                <option value="">Select a dataset…</option>
                {datasets.data?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({d.row_count.toLocaleString()} rows)
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          {datasetId && (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {template.roles.map((role) => (
                <Field key={role.key} label={role.required ? role.label : `${role.label} (optional)`}>
                  <Select
                    value={columnMap[role.key] ?? ""}
                    onChange={(e) => setColumnMap({ ...columnMap, [role.key]: e.target.value })}
                  >
                    <option value="">{role.required ? "Select column…" : "(none)"}</option>
                    {columns.map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </Select>
                  {columnMap[role.key] && (
                    <Input
                      className="mt-2"
                      placeholder="Friendly label (optional)"
                      value={labels[role.key] ?? ""}
                      onChange={(e) => setLabels({ ...labels, [role.key]: e.target.value })}
                    />
                  )}
                </Field>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {error && <Alert variant="destructive">{error}</Alert>}

      <div className="flex gap-2">
        <Button variant="secondary" onClick={onBack}>Back</Button>
        <Button disabled={!ready} loading={create.isPending} onClick={onSubmit}>Run</Button>
      </div>
    </div>
  );
}
