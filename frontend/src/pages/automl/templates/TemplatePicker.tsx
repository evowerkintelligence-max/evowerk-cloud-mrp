import { AlertTriangle, Brain, PackageX, Wrench, type LucideIcon } from "lucide-react";
import { EmptyState } from "@/components/EmptyState";
import { Alert } from "@/components/ui/alert";
import { Card } from "@/components/ui/card";
import { useAutoMLTemplates, type AutoMLTemplateSummary } from "../api";

// Known template ids get a domain icon; any future template registered
// server-side without frontend changes still renders (generic Brain icon).
const TEMPLATE_ICONS: Record<string, LucideIcon> = {
  scrap_prediction: PackageX,
  predictive_maintenance: Wrench,
  downtime_risk: AlertTriangle,
};

export function TemplatePicker({ onSelect }: { onSelect: (template: AutoMLTemplateSummary) => void }) {
  const templates = useAutoMLTemplates();

  if (templates.isLoading) return <p className="text-sm text-muted-foreground">Loading templates…</p>;
  if (templates.isError) return <Alert variant="destructive">Failed to load AutoML templates.</Alert>;
  if (!templates.data?.length) {
    return (
      <EmptyState
        icon={<Brain />}
        title="No AutoML templates available"
        description="Ask an admin to register a guided template, or use the Advanced entry point to train a custom model."
      />
    );
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {templates.data.map((t) => {
        const Icon = TEMPLATE_ICONS[t.id] ?? Brain;
        const requiredCount = t.roles.filter((r) => r.required).length;
        return (
          <Card
            key={t.id}
            role="button"
            tabIndex={0}
            onClick={() => onSelect(t)}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(t)}
            className="cursor-pointer p-5 transition hover:border-primary/50 hover:shadow-glow-sm"
          >
            <div className="mb-3 flex size-10 items-center justify-center rounded-lg bg-secondary/50 text-primary [&_svg]:size-5">
              <Icon />
            </div>
            <div className="font-semibold">{t.label}</div>
            <p className="mt-1 text-xs text-muted-foreground">
              {requiredCount} required column{requiredCount === 1 ? "" : "s"} to map
              {t.roles.length > requiredCount ? `, ${t.roles.length - requiredCount} optional` : ""}
            </p>
          </Card>
        );
      })}
    </div>
  );
}
