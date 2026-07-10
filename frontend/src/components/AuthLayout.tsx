import type { ReactNode } from "react";
import { BrandMark } from "@/components/BrandMark";

export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="relative flex min-h-screen items-center justify-center px-4 py-12">
      <div className="app-backdrop" />
      <div className="w-full max-w-md animate-fade-in">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-6 flex items-center gap-3">
            <BrandMark className="size-11 text-foreground" title="Evowerk" />
            <span className="text-3xl font-bold uppercase tracking-[0.14em] text-foreground">
              Evowerk
            </span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {subtitle && <p className="mt-1.5 text-sm text-muted-foreground">{subtitle}</p>}
        </div>
        <div className="rounded-lg border border-border bg-card/80 p-6 shadow-glow backdrop-blur-sm sm:p-8">
          {children}
        </div>
        {footer && <div className="mt-6 text-center text-sm text-muted-foreground">{footer}</div>}
      </div>
    </div>
  );
}
