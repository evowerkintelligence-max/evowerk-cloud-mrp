import type { ReactNode } from "react";
import { Boxes, Gauge, TrendingUp } from "lucide-react";
import { BrandMark } from "@/components/BrandMark";

const PITCH = [
  {
    icon: Boxes,
    title: "Lot-level traceability",
    text: "Every unit of stock tracked from receipt to consumption.",
  },
  {
    icon: Gauge,
    title: "Plant analytics built in",
    text: "OEE, downtime, scrap, and SPC straight from production data.",
  },
  {
    icon: TrendingUp,
    title: "Forecast-driven planning",
    text: "MRP runs that turn demand signals into purchase orders.",
  },
];

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
    <div className="relative flex min-h-screen">
      <div className="app-backdrop" />

      {/* Brand / pitch panel */}
      <div className="relative hidden w-1/2 flex-col justify-between overflow-hidden border-r border-border/70 bg-card/30 p-12 lg:flex">
        <div className="flex items-center gap-3">
          <BrandMark className="size-9 text-foreground" title="Evowerk" />
          <div className="leading-tight">
            <div className="text-lg font-bold uppercase tracking-[0.16em] text-foreground">
              Evowerk
            </div>
            <div className="text-[10px] font-medium uppercase tracking-[0.24em] text-muted-foreground">
              Cloud MRP
            </div>
          </div>
        </div>

        <div className="max-w-md">
          <h2 className="text-3xl font-semibold leading-tight tracking-tight text-foreground">
            The operating layer for modern manufacturing.
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
            BOMs, procurement, lot tracking, and plant analytics in one multi-tenant workspace.
          </p>
          <div className="mt-10 space-y-6">
            {PITCH.map((p) => (
              <div key={p.title} className="flex items-start gap-4">
                <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary ring-1 ring-inset ring-primary/20">
                  <p.icon className="size-4" />
                </div>
                <div>
                  <div className="text-sm font-medium text-foreground">{p.title}</div>
                  <div className="mt-0.5 text-sm text-muted-foreground">{p.text}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="text-xs text-muted-foreground">
          © {new Date().getFullYear()} Evowerk · Multi-tenant · Audit-logged · MFA-ready
        </div>
      </div>

      {/* Form panel */}
      <div className="flex flex-1 items-center justify-center px-4 py-12">
        <div className="w-full max-w-sm animate-fade-in">
          <div className="mb-8 flex flex-col items-start">
            <div className="mb-8 flex items-center gap-2.5 lg:hidden">
              <BrandMark className="size-9 text-foreground" title="Evowerk" />
              <span className="text-xl font-bold uppercase tracking-[0.14em] text-foreground">
                Evowerk
              </span>
            </div>
            <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
            {subtitle && <p className="mt-1.5 text-sm text-muted-foreground">{subtitle}</p>}
          </div>
          <div className="glow-card rounded-xl p-6 sm:p-8">{children}</div>
          {footer && <div className="mt-6 text-center text-sm text-muted-foreground">{footer}</div>}
        </div>
      </div>
    </div>
  );
}
