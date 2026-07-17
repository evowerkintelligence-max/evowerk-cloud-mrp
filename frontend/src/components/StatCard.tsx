import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useCountUp } from "@/lib/useCountUp";

const TONE = {
  primary: {
    icon: "bg-primary/15 text-primary ring-primary/30",
    blob: "bg-primary/10",
    glow: "hover:border-primary/50 hover:shadow-glow-sm",
    text: "text-primary",
  },
  accent: {
    icon: "bg-accent/15 text-accent ring-accent/30",
    blob: "bg-accent/10",
    glow: "hover:border-accent/50 hover:shadow-[0_0_18px_-4px_hsl(var(--accent)/0.5)]",
    text: "text-accent",
  },
  success: {
    icon: "bg-success/15 text-success ring-success/30",
    blob: "bg-success/10",
    glow: "hover:border-success/50 hover:shadow-[0_0_18px_-4px_hsl(var(--success)/0.5)]",
    text: "text-success",
  },
  warning: {
    icon: "bg-warning/15 text-warning ring-warning/30",
    blob: "bg-warning/10",
    glow: "hover:border-warning/50 hover:shadow-[0_0_18px_-4px_hsl(var(--warning)/0.5)]",
    text: "text-warning",
  },
  info: {
    icon: "bg-accent/15 text-accent ring-accent/30",
    blob: "bg-accent/10",
    glow: "hover:border-accent/50 hover:shadow-[0_0_18px_-4px_hsl(var(--accent)/0.5)]",
    text: "text-accent",
  },
  terracotta: {
    icon: "bg-terracotta/15 text-terracotta ring-terracotta/30",
    blob: "bg-terracotta/10",
    glow: "hover:border-terracotta/50 hover:shadow-[0_0_18px_-4px_hsl(var(--terracotta)/0.5)]",
    text: "text-terracotta",
  },
} as const;

/** Cycled by index across a row of StatCards so it reads as multi-color, not monochrome. */
export const STAT_TONES = Object.keys(TONE) as (keyof typeof TONE)[];

function HeroValue({
  heroValue,
  heroFormat,
  heroColor,
}: {
  heroValue: number;
  heroFormat: (n: number) => string;
  heroColor?: string;
}) {
  const animated = useCountUp(heroValue);
  return (
    <span style={heroColor ? { color: heroColor } : undefined}>
      {heroFormat(animated)}
    </span>
  );
}

export function StatCard({
  label,
  value,
  icon,
  hint,
  tone = "primary",
  title,
  size = "default",
  heroValue,
  heroFormat = (n) => String(Math.round(n)),
  heroColor,
  delta,
  className,
}: {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  hint?: ReactNode;
  tone?: keyof typeof TONE;
  /** Hover tooltip explaining what this metric means. */
  title?: string;
  /** "hero" renders an oversized, animated primary metric with the label demoted below it. */
  size?: "default" | "hero";
  /** Numeric value to animate via useCountUp. Only used when size="hero"; falls back to static `value` if omitted. */
  heroValue?: number;
  /** Formats the animated number for display, e.g. (n) => `${n.toFixed(1)}%`. */
  heroFormat?: (n: number) => string;
  /** Overrides the hero number's text color (e.g. a dynamic threshold color), instead of the tone's default foreground. */
  heroColor?: string;
  /** Inline delta shown next to the hero number, e.g. "+2.1%". Only rendered when size="hero". */
  delta?: ReactNode;
  className?: string;
}) {
  const t = TONE[tone];
  const isHero = size === "hero";
  return (
    <Card
      className={cn(
        "group relative overflow-hidden rounded-xl bg-card/70 p-5 backdrop-blur-sm transition-all duration-300 hover:-translate-y-0.5",
        t.glow,
        className,
      )}
    >
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute -right-8 -top-8 size-24 rounded-full opacity-0 blur-2xl transition-opacity group-hover:opacity-100",
          t.blob,
        )}
      />
      {isHero ? (
        <>
          <div className="flex items-center justify-between gap-3">
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
          <div className="tabular mt-1 text-4xl font-extrabold leading-none tracking-tight lg:text-5xl">
            {heroValue !== undefined ? (
              <HeroValue heroValue={heroValue} heroFormat={heroFormat} heroColor={heroColor} />
            ) : (
              value
            )}
            {delta && <span className={cn("ml-2 text-sm font-semibold", t.text)}>{delta}</span>}
          </div>
          <div
            className={cn(
              "mt-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground",
              title && "cursor-help",
            )}
            title={title}
          >
            {label}
          </div>
          {hint && <div className="mt-2 text-xs text-muted-foreground">{hint}</div>}
        </>
      ) : (
        <>
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
        </>
      )}
    </Card>
  );
}
