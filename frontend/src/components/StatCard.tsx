import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const TONE = {
  primary: {
    icon: "bg-primary/15 text-primary ring-primary/30",
    blob: "bg-primary/10",
    glow: "hover:border-primary/50 hover:shadow-glow-sm",
  },
  accent: {
    icon: "bg-accent/15 text-accent ring-accent/30",
    blob: "bg-accent/10",
    glow: "hover:border-accent/50 hover:shadow-[0_0_18px_-4px_hsl(var(--accent)/0.5)]",
  },
  success: {
    icon: "bg-success/15 text-success ring-success/30",
    blob: "bg-success/10",
    glow: "hover:border-success/50 hover:shadow-[0_0_18px_-4px_hsl(var(--success)/0.5)]",
  },
  warning: {
    icon: "bg-warning/15 text-warning ring-warning/30",
    blob: "bg-warning/10",
    glow: "hover:border-warning/50 hover:shadow-[0_0_18px_-4px_hsl(var(--warning)/0.5)]",
  },
} as const;

/** Cycled by index across a row of StatCards so it reads as multi-color, not monochrome. */
export const STAT_TONES = Object.keys(TONE) as (keyof typeof TONE)[];

export function StatCard({
  label,
  value,
  icon,
  hint,
  tone = "primary",
  title,
}: {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  hint?: ReactNode;
  tone?: keyof typeof TONE;
  /** Hover tooltip explaining what this metric means. */
  title?: string;
}) {
  const t = TONE[tone];
  return (
    <Card
      className={cn(
        "group relative overflow-hidden rounded-xl bg-card/70 p-5 backdrop-blur-sm transition-all duration-300 hover:-translate-y-0.5",
        t.glow,
      )}
    >
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute -right-8 -top-8 size-24 rounded-full opacity-0 blur-2xl transition-opacity group-hover:opacity-100",
          t.blob,
        )}
      />
      <div className="flex items-center justify-between gap-3">
        <span
          className={cn(
            "text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground",
            title && "cursor-help",
          )}
          title={title}
        >
          {label}
        </span>
        {icon && (
          <span
            className={cn(
              "flex size-9 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset [&_svg]:size-4",
              t.icon,
            )}
          >
            {icon}
          </span>
        )}
      </div>
      <div className="tabular mt-3 text-3xl font-extrabold leading-none tracking-tight">
        {value}
      </div>
      {hint && <div className="mt-2 text-xs text-muted-foreground">{hint}</div>}
    </Card>
  );
}
