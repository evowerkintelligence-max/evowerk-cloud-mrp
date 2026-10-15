import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { fmt } from "./shared";
import { TABS, type DatasetDetail as DatasetDetailData, type Tab } from "./dataset-detail/types";
import { HealthPanel, Overview, Preview, ReadinessPanel, Schema } from "./dataset-detail/InfoPanels";
import { QueryPanel, SqlPanel } from "./dataset-detail/QueryPanels";
import { CleanPanel } from "./dataset-detail/CleanPanel";
import { TransformPanel } from "./dataset-detail/TransformPanel";
import { QualityPanel } from "./dataset-detail/QualityPanel";
import { ProcessMiningPanel } from "./dataset-detail/ProcessMiningPanel";

export function DatasetDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");

  const detail = useQuery<DatasetDetailData>({
    queryKey: ["dataset", id],
    queryFn: async () => {
      const res = await apiFetch(`/datasets/${id}`);
      if (!res.ok) throw new Error(res.status === 404 ? "Dataset not found" : `Failed to load dataset (${res.status})`);
      return res.json();
    },
    enabled: !!id,
  });

  function openDataset(newId: string) {
    qc.invalidateQueries({ queryKey: ["datasets"] });
    navigate(`/datasets/${newId}`);
  }

  if (detail.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (!detail.data) {
    return (
      <Alert variant="destructive">
        {detail.error instanceof Error ? detail.error.message : "Dataset not found."}
      </Alert>
    );
  }
  const d = detail.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title={d.name}
        description={`${d.fmt.toUpperCase()} · ${fmt(d.row_count)} rows · ${d.columns.length} columns`}
        actions={
          <Button variant="ghost" size="sm" onClick={() => navigate("/datasets")}>
            <ArrowLeft /> Back to datasets
          </Button>
        }
      />

      <div className="flex gap-1 overflow-x-auto border-b border-border">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium capitalize transition-colors",
              tab === t
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "overview" && <Overview d={d} />}
      {tab === "readiness" && <ReadinessPanel d={d} onOpenTab={setTab} />}
      {tab === "schema" && <Schema d={d} />}
      {tab === "preview" && <Preview d={d} />}
      {tab === "health" && <HealthPanel id={d.id} />}
      {tab === "query" && <QueryPanel d={d} />}
      {tab === "sql" && <SqlPanel d={d} />}
      {tab === "clean" && <CleanPanel d={d} onCreated={openDataset} />}
      {tab === "transform" && <TransformPanel d={d} onCreated={openDataset} />}
      {tab === "quality" && <QualityPanel d={d} />}
      {tab === "process" && <ProcessMiningPanel d={d} />}
    </div>
  );
}
