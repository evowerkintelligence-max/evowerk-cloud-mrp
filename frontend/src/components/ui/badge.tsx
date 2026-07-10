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
        success: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
        warning: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
        danger: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
        info: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
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
