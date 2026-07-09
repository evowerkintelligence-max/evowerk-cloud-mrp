import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

const alertVariants = cva("flex items-start gap-3 rounded-md border px-4 py-3 text-sm", {
  variants: {
    variant: {
      info: "border-sky-500/30 bg-sky-500/10 text-sky-200",
      success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
      warning: "border-amber-500/30 bg-amber-500/10 text-amber-200",
      destructive: "border-destructive/40 bg-destructive/10 text-rose-200",
    },
  },
  defaultVariants: { variant: "info" },
});

const icons = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  destructive: XCircle,
} as const;

export interface AlertProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof alertVariants> {}

export function Alert({ className, variant = "info", children, ...props }: AlertProps) {
  const Icon = icons[variant ?? "info"];
  return (
    <div role="alert" className={cn(alertVariants({ variant }), className)} {...props}>
      <Icon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}
