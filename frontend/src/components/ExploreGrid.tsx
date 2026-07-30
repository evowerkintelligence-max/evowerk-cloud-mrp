import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { Card, CardContent, CardDescription, CardTitle } from "@/components/ui/card";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export interface ExploreCardItem {
  to: string;
  label: string;
  description?: string;
  icon: LucideIcon;
}

/** Cycled per card index so the grid reads as multi-color, not monochrome. */
const EXPLORE_TONES = [
  {
    card: "hover:border-primary/40 hover:shadow-glow-sm",
    icon: "bg-primary/10 text-primary ring-primary/20",
    title: "group-hover:text-primary",
  },
  {
    card: "hover:border-accent/40 hover:shadow-[0_0_18px_-4px_hsl(var(--accent)/0.45)]",
    icon: "bg-accent/10 text-accent ring-accent/20",
    title: "group-hover:text-accent",
  },
  {
    card: "hover:border-success/40 hover:shadow-[0_0_18px_-4px_hsl(var(--success)/0.45)]",
    icon: "bg-success/10 text-success ring-success/20",
    title: "group-hover:text-success",
  },
] as const;

export function ExploreGrid({ items }: { items: ExploreCardItem[] }) {
  return (
    // ponytail: 4 items read best as 2x2; more than that wants 3 columns.
    <div className={cn("grid gap-5 sm:grid-cols-2", items.length > 4 && "lg:grid-cols-3")}>
      {items.map((item, i) => {
        const t = EXPLORE_TONES[i % EXPLORE_TONES.length];
        return (
          <Link key={item.to} to={item.to} className="group rounded-lg focus-visible:outline-none">
            <Card
              className={cn(
                "relative h-full transition-all duration-300 hover:-translate-y-1 group-focus-visible:ring-2 group-focus-visible:ring-ring",
                t.card,
              )}
            >
              <ArrowUpRight className="absolute right-4 top-4 size-4 text-muted-foreground opacity-0 transition-all duration-300 group-hover:-translate-y-1 group-hover:translate-x-1 group-hover:opacity-100" />
              <CardContent className="flex items-start gap-5 p-6">
                <div
                  className={cn(
                    "flex size-12 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset transition-transform duration-300 group-hover:scale-110 [&_svg]:size-6",
                    t.icon,
                  )}
                >
                  <item.icon />
                </div>
                <div className="space-y-1.5">
                  <CardTitle className={cn("text-lg transition-colors", t.title)}>
                    {item.label}
                  </CardTitle>
                  {item.description && <CardDescription>{item.description}</CardDescription>}
                </div>
              </CardContent>
            </Card>
          </Link>
        );
      })}
    </div>
  );
}
