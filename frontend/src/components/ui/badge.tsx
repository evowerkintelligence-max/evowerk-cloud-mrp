import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap",
  {
    variants: {
      variant: {
        neutral: "bg-secondary text-secondary-foreground ring-border",
        primary: "bg-primary/15 text-primary ring-primary/30",
        accent: "bg-accent/15 text-accent ring-accent/30",
        success: "bg-success/15 text-success ring-success/30",
        warning: "bg-warning/15 text-warning ring-warning/30",
        danger: "bg-destructive/15 text-destructive ring-destructive/30",
        info: "bg-accent/15 text-accent ring-accent/30",
        terracotta: "bg-terracotta/15 text-terracotta ring-terracotta/30",
        indigo: "bg-indigo-500/15 text-indigo-300 ring-indigo-500/30",
        teal: "bg-teal-500/15 text-teal-300 ring-teal-500/30",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { badgeVariants };
