import { useState } from "react";
import { Hash, ShieldCheck } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { STAT_TONES, StatCard } from "@/components/StatCard";
import { Field } from "@/components/Field";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

interface CvFinding {
  label: string;
  confidence: string;
  location: string | null;
  severity: string | null;
}

interface CvResult {
  task: string;
  mode: string;
  summary: string;
  findings: CvFinding[];
  compliance_pct: number | null;
  count: number | null;
}

const TASKS = [
  { value: "defect_detection", label: "Defect Detection" },
  { value: "ppe_compliance", label: "PPE Compliance" },
  { value: "counting", label: "Object Counting" },
];

const SEVERITY_VARIANT: Record<string, "success" | "warning" | "danger"> = { ok: "success", warning: "warning", critical: "danger" };

export function ComputerVision() {
  const [task, setTask] = useState("defect_detection");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);

  const analyze = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose an image first");
      const fd = new FormData();
      fd.append("task", task);
      fd.append("image", file);
      const res = await apiFetch("/computer-vision/analyze", { method: "POST", body: fd });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Analysis failed");
      return res.json() as Promise<CvResult>;
    },
  });

  function pickFile(f: File | null) {
    setFile(f);
    setPreview(f ? URL.createObjectURL(f) : null);
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Computer Vision" description="Upload a shop-floor image for defect detection, PPE compliance, or object counting." />

      <Card>
        <CardHeader>
          <CardTitle>Analyze an image</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Task">
              <Select value={task} onChange={(e) => setTask(e.target.value)}>
                {TASKS.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Image">
              <Input
                type="file"
                accept="image/jpeg,image/png,image/webp,image/gif"
                onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
                className="file:mr-3 file:rounded file:border-0 file:bg-secondary file:px-3 file:py-1 file:text-foreground"
              />
            </Field>
          </div>
          {preview && <img src={preview} alt="Preview" className="max-h-64 rounded-md border border-border object-contain" />}
          <Button disabled={!file} loading={analyze.isPending} onClick={() => analyze.mutate()}>
            Analyze
          </Button>
          {analyze.isError && <Alert variant="destructive">{(analyze.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {analyze.isSuccess && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              Results
              {analyze.data.mode === "demo" && <Badge variant="warning">demo mode</Badge>}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Alert>{analyze.data.summary}</Alert>
            {(analyze.data.compliance_pct !== null || analyze.data.count !== null) && (
              <div className="grid gap-4 sm:grid-cols-2">
                {analyze.data.compliance_pct !== null && (
                  <StatCard label="Compliance" value={`${analyze.data.compliance_pct}%`} icon={<ShieldCheck />} tone={STAT_TONES[0]} />
                )}
                {analyze.data.count !== null && (
                  <StatCard label="Count" value={analyze.data.count} icon={<Hash />} tone={STAT_TONES[1]} />
                )}
              </div>
            )}
            <div className="space-y-2">
              {analyze.data.findings.map((f, i) => (
                <div key={i} className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm">
                  <div>
                    <span className="font-medium">{f.label}</span>
                    {f.location && <span className="text-muted-foreground"> — {f.location}</span>}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">{f.confidence}</span>
                    {f.severity && <Badge variant={SEVERITY_VARIANT[f.severity] ?? "neutral"}>{f.severity}</Badge>}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
