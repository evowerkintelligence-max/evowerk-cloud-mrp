# Components

Shared UI primitives (`frontend/src/components/ui/`) and reusable non-page-specific
components (`frontend/src/components/`). Built with a custom shadcn-style kit:
`class-variance-authority` (cva) for variants, `clsx` + `tailwind-merge` (via `cn()`)
for class composition, `lucide-react` for icons.

---

## UI Primitives (`frontend/src/components/ui/`)

### Button
- File: `frontend/src/components/ui/button.tsx`
- Description: Primary button with 5 visual variants and 4 sizes, cva-driven, supports a `loading` spinner state.
- Key props: `variant?: "primary" | "secondary" | "outline" | "ghost" | "destructive"`, `size?: "sm" | "md" | "lg" | "icon"`, `loading?: boolean`, plus all native `<button>` attrs.

```tsx
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 active:translate-y-px [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary:
          "bg-primary font-semibold text-primary-foreground shadow-[inset_0_1px_0_0_hsl(0_0%_100%/0.25),0_1px_2px_0_hsl(224_42%_2%/0.5)] hover:bg-primary/90 hover:shadow-glow-sm active:bg-primary/85",
        secondary:
          "border border-input bg-secondary text-secondary-foreground shadow-card hover:bg-secondary/70",
        outline:
          "border border-input bg-transparent text-foreground hover:border-muted-foreground/40 hover:bg-secondary/50",
        ghost: "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
        destructive:
          "bg-destructive font-semibold text-destructive-foreground shadow-[inset_0_1px_0_0_hsl(0_0%_100%/0.15)] hover:bg-destructive/90 active:bg-destructive/85",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-10 px-4 text-sm",
        lg: "h-11 px-6 text-sm",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, loading, disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(buttonVariants({ variant, size }), className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Loader2 className="animate-spin" />}
      {children}
    </button>
  ),
);
Button.displayName = "Button";

export { buttonVariants };
```

### Card / CardHeader / CardTitle / CardDescription / CardContent
- File: `frontend/src/components/ui/card.tsx`
- Description: Standard card container with header/title/description/content sub-parts, used everywhere for panels.
- Key props: all are `React.forwardRef` wrappers over `div`/`h3`/`p` with `className` passthrough.

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "rounded-lg border border-border bg-card text-card-foreground shadow-card",
        className,
      )}
      {...props}
    />
  ),
);
Card.displayName = "Card";

export const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("flex flex-col gap-1.5 p-6", className)} {...props} />
  ),
);
CardHeader.displayName = "CardHeader";

export const CardTitle = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <h3
      ref={ref}
      className={cn("text-lg font-semibold leading-tight tracking-tight", className)}
      {...props}
    />
  ),
);
CardTitle.displayName = "CardTitle";

export const CardDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p ref={ref} className={cn("text-sm text-muted-foreground", className)} {...props} />
));
CardDescription.displayName = "CardDescription";

export const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("p-6 pt-0", className)} {...props} />
  ),
);
CardContent.displayName = "CardContent";
```

### Dialog
- File: `frontend/src/components/ui/dialog.tsx`
- Description: Simple controlled modal overlay (no portal/focus-trap library) with backdrop-click-to-close and a titled header.
- Key props: `open: boolean`, `onClose: () => void`, `title: string`, `children: ReactNode`, `className?: string`.

```tsx
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export function Dialog({
  open,
  onClose,
  title,
  children,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  className?: string;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-background/70 backdrop-blur-sm animate-fade-in"
        onClick={onClose}
      />
      <div
        className={cn(
          "relative w-full max-w-lg animate-scale-in rounded-lg bg-popover shadow-overlay",
          className,
        )}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <h2 className="text-base font-semibold">{title}</h2>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <X />
          </Button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}
```

### Skeleton
- File: `frontend/src/components/ui/skeleton.tsx`
- Description: Shimmering loading placeholder block.
- Key props: `HTMLAttributes<HTMLDivElement>` (size controlled via `className`).

```tsx
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "animate-shimmer rounded-md bg-gradient-to-r from-secondary/60 via-secondary/30 to-secondary/60 bg-[length:200%_100%]",
        className,
      )}
      {...props}
    />
  );
}
```

### Table / TableWrap / THead / TBody / TR / TH / TD
- File: `frontend/src/components/ui/table.tsx`
- Description: Full table primitive set; `TableWrap` provides the scrollable bordered container used around every `<Table>`.
- Key props: all forward native table element attrs + `className`.

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

/** Wrap Table in this to keep wide tables scrollable instead of blowing out the layout. */
export const TableWrap = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "w-full overflow-x-auto rounded-lg border border-border bg-card/60 shadow-card",
        className,
      )}
      {...props}
    />
  ),
);
TableWrap.displayName = "TableWrap";

export const Table = React.forwardRef<HTMLTableElement, React.HTMLAttributes<HTMLTableElement>>(
  ({ className, ...props }, ref) => (
    <table ref={ref} className={cn("w-full caption-bottom text-sm", className)} {...props} />
  ),
);
Table.displayName = "Table";

export const THead = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <thead
    ref={ref}
    className={cn("border-b border-border bg-secondary/40", className)}
    {...props}
  />
));
THead.displayName = "THead";

export const TBody = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tbody ref={ref} className={cn("divide-y divide-border", className)} {...props} />
));
TBody.displayName = "TBody";

export const TR = React.forwardRef<
  HTMLTableRowElement,
  React.HTMLAttributes<HTMLTableRowElement>
>(({ className, ...props }, ref) => (
  <tr ref={ref} className={cn("transition-colors hover:bg-secondary/30", className)} {...props} />
));
TR.displayName = "TR";

export const TH = React.forwardRef<
  HTMLTableCellElement,
  React.ThHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <th
    ref={ref}
    className={cn(
      "h-10 px-4 text-left align-middle text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground",
      className,
    )}
    {...props}
  />
));
TH.displayName = "TH";

export const TD = React.forwardRef<
  HTMLTableCellElement,
  React.TdHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <td ref={ref} className={cn("tabular px-4 py-3 align-middle", className)} {...props} />
));
TD.displayName = "TD";
```

### Alert
- File: `frontend/src/components/ui/alert.tsx`
- Description: Inline banner with 4 semantic variants, each pairing a lucide icon + tinted colors.
- Key props: `variant?: "info" | "success" | "warning" | "destructive"` (default `"info"`).

```tsx
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
```

### Badge
- File: `frontend/src/components/ui/badge.tsx`
- Description: Small pill label with 9 color variants used for status/type tags throughout tables and cards.
- Key props: `variant?: "neutral" | "primary" | "accent" | "success" | "warning" | "danger" | "info" | "indigo" | "teal"` (default `"neutral"`).

```tsx
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
```

### Checkbox
- File: `frontend/src/components/ui/checkbox.tsx`
- Description: Native checkbox input restyled to the theme's accent color.
- Key props: `Omit<React.InputHTMLAttributes<HTMLInputElement>, "type">`.

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export const Checkbox = React.forwardRef<
  HTMLInputElement,
  Omit<React.InputHTMLAttributes<HTMLInputElement>, "type">
>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    type="checkbox"
    className={cn(
      "size-4 shrink-0 cursor-pointer rounded border border-input bg-background accent-[hsl(var(--primary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
Checkbox.displayName = "Checkbox";
```

### Input
- File: `frontend/src/components/ui/input.tsx`
- Description: Themed text input.
- Key props: `React.InputHTMLAttributes<HTMLInputElement>`.

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "flex h-10 w-full rounded-md border border-input bg-background/60 px-3 py-2 text-sm text-foreground shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-60",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";
```

### Label
- File: `frontend/src/components/ui/label.tsx`
- Description: Form label text.
- Key props: `React.LabelHTMLAttributes<HTMLLabelElement>`.

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export const Label = React.forwardRef<HTMLLabelElement, React.LabelHTMLAttributes<HTMLLabelElement>>(
  ({ className, ...props }, ref) => (
    <label
      ref={ref}
      className={cn(
        "text-sm font-medium leading-none text-foreground peer-disabled:opacity-60",
        className,
      )}
      {...props}
    />
  ),
);
Label.displayName = "Label";
```

### Select
- File: `frontend/src/components/ui/select.tsx`
- Description: Native `<select>` restyled with a custom chevron icon overlay.
- Key props: `React.SelectHTMLAttributes<HTMLSelectElement>`; children are `<option>`s.

```tsx
import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export const Select = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, ...props }, ref) => (
  <div className="relative">
    <select
      ref={ref}
      className={cn(
        "flex h-10 w-full appearance-none rounded-md border border-input bg-background/60 py-2 pl-3 pr-9 text-sm text-foreground shadow-sm transition-colors focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-60 [&>option]:bg-popover [&>option]:text-popover-foreground",
        className,
      )}
      {...props}
    >
      {children}
    </select>
    <ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
  </div>
));
Select.displayName = "Select";
```

---

## Reusable Non-Page Components (`frontend/src/components/`)

Layout components (`AppShell`, `AuthLayout`, `PageHeader`, `PageTabs`) are documented in
full in `layouts.md` — only summarized here for completeness. `EmptyState`, `StatCard`,
`CommandPalette`, `BrandMark`, `ItemSelect`, and `Field` are documented in full below.

### EmptyState
- File: `frontend/src/components/EmptyState.tsx`
- Description: Dashed-border placeholder block for empty lists/tables, with optional icon + description.
- Key props: `icon?: ReactNode`, `title: string`, `description?: ReactNode`, `className?: string`.

```tsx
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function EmptyState({
  icon,
  title,
  description,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-input bg-card/40 px-6 py-14 text-center",
        className,
      )}
    >
      {icon && (
        <div className="mb-2 flex size-12 items-center justify-center rounded-lg border border-border bg-secondary/50 text-muted-foreground [&_svg]:size-5">
          {icon}
        </div>
      )}
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
    </div>
  );
}
```

### StatCard
- File: `frontend/src/components/StatCard.tsx`
- Description: KPI tile — label, big value, optional icon badge and hint text, glow-on-hover accent. Wraps `Card`.
- Key props: `label: string`, `value: ReactNode`, `icon?: ReactNode`, `hint?: ReactNode`.

```tsx
import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";

export function StatCard({
  label,
  value,
  icon,
  hint,
}: {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <Card className="group relative overflow-hidden p-5 transition-colors hover:border-primary/30">
      <div
        aria-hidden
        className="pointer-events-none absolute -right-8 -top-8 size-24 rounded-full bg-primary/[0.06] blur-2xl transition-opacity opacity-0 group-hover:opacity-100"
      />
      <div className="flex items-center justify-between gap-3">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          {label}
        </span>
        {icon && (
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary ring-1 ring-inset ring-primary/20 [&_svg]:size-4">
            {icon}
          </span>
        )}
      </div>
      <div className="tabular mt-3 text-3xl font-semibold leading-none tracking-tight">
        {value}
      </div>
      {hint && <div className="mt-2 text-xs text-muted-foreground">{hint}</div>}
    </Card>
  );
}
```

### CommandPalette
- File: `frontend/src/components/CommandPalette.tsx` *(untracked — new)*
- Description: Ctrl/Cmd-K modal navigation palette that fuzzy-filters `ALL_NAV_ITEMS` (from `@/lib/nav`) by label/section, with keyboard up/down/enter/escape handling. Opened/closed by `AppShell`.
- Key props: `open: boolean`, `onClose: () => void`.

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CornerDownLeft, Search } from "lucide-react";
import { ALL_NAV_ITEMS } from "@/lib/nav";
import { cn } from "@/lib/utils";

/** Ctrl/⌘-K navigation palette over every page in the app. */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return ALL_NAV_ITEMS;
    return ALL_NAV_ITEMS.filter(
      (item) =>
        item.label.toLowerCase().includes(q) || item.section.toLowerCase().includes(q),
    );
  }, [query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setSelected(0);
      // Focus after the overlay mounts.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => setSelected(0), [query]);

  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-index="${selected}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  if (!open) return null;

  function go(to: string) {
    onClose();
    navigate(to);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((s) => Math.min(s + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((s) => Math.max(s - 1, 0));
    } else if (e.key === "Enter" && results[selected]) {
      e.preventDefault();
      go(results[selected].to);
    } else if (e.key === "Escape") {
      onClose();
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[14vh]">
      <div
        className="absolute inset-0 animate-fade-in bg-background/70 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        className="relative w-full max-w-xl animate-scale-in overflow-hidden rounded-xl bg-popover shadow-overlay"
        onKeyDown={onKeyDown}
      >
        <div className="flex items-center gap-3 border-b border-border px-4">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Go to page…"
            className="h-12 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
          />
          <kbd className="rounded border border-border bg-secondary/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            ESC
          </kbd>
        </div>
        <div ref={listRef} className="max-h-[50vh] overflow-y-auto p-2">
          {results.length === 0 && (
            <p className="px-3 py-8 text-center text-sm text-muted-foreground">
              No pages match "{query}".
            </p>
          )}
          {results.map((item, i) => (
            <button
              key={item.to}
              type="button"
              data-index={i}
              onClick={() => go(item.to)}
              onMouseMove={() => setSelected(i)}
              className={cn(
                "flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm transition-colors",
                i === selected ? "bg-secondary text-foreground" : "text-muted-foreground",
              )}
            >
              <item.icon className="size-4 shrink-0 opacity-70" />
              <span className="flex-1 truncate font-medium">{item.label}</span>
              <span className="text-[11px] uppercase tracking-wide text-muted-foreground/70">
                {item.section}
              </span>
              {i === selected && (
                <CornerDownLeft className="size-3.5 shrink-0 text-muted-foreground" />
              )}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
```

### BrandMark
- File: `frontend/src/components/BrandMark.tsx`
- Description: Inline SVG logo mark (ring + angular waveform), `currentColor`-based so it inverts with theme.
- Key props: `className?: string`, `title?: string` (sets `role="img"`/`aria-label` when present).

```tsx
// Evowerk brand mark: a ring enclosing the angular waveform — a bold rounded
// zig-zag (low-left → peak → valley → peak → valley → high top-right tail).
// Monochrome (currentColor), so it inverts with the theme to match the
// EVOWERK logo (white on dark / black on light).

export function BrandMark({ className, title }: { className?: string; title?: string }) {
  return (
    <svg
      viewBox="0 0 100 100"
      className={className}
      fill="none"
      stroke="currentColor"
      role={title ? "img" : "presentation"}
      aria-hidden={title ? undefined : true}
      aria-label={title}
      xmlns="http://www.w3.org/2000/svg"
    >
      {title && <title>{title}</title>}
      <circle cx="50" cy="50" r="40" strokeWidth="5.5" />
      <polyline
        points="30,58 40,42 48,58 56,45 63,57 74,39"
        strokeWidth="8.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
```

### ItemSelect
- File: `frontend/src/components/ItemSelect.tsx`
- Description: Domain-specific item-picker `<select>` used wherever a feature references an Item (BOM components, PO lines). Selecting "+ New item..." swaps in an inline 2-field quick-create form (SKU + Name) that POSTs `/items` and invalidates the `items` query on success.
- Key props: `id?`, `items: {id,sku,name}[] | undefined`, `value: string`, `onChange: (itemId: string) => void`, `excludeId?: string`, `required?: boolean`.

```tsx
import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

interface ItemOption {
  id: string;
  sku: string;
  name: string;
}

const NEW_ITEM_VALUE = "__new__";

// Item dropdown used wherever a feature needs to reference an item (BOM
// components, PO lines, ...). Picking "+ New item" swaps in a 2-field quick
// create instead of sending the user to the Items catalog and back.
export function ItemSelect({
  id,
  items,
  value,
  onChange,
  excludeId,
  required,
}: {
  id?: string;
  items: ItemOption[] | undefined;
  value: string;
  onChange: (itemId: string) => void;
  excludeId?: string;
  required?: boolean;
}) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [sku, setSku] = useState("");
  const [name, setName] = useState("");
  const requiredMirrorRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    requiredMirrorRef.current?.setCustomValidity(
      required && !value ? "Select an item, or finish creating the new one." : "",
    );
  }, [required, value]);

  const createItem = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/items", {
        method: "POST",
        body: JSON.stringify({ sku, name }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create item");
      }
      return res.json() as Promise<ItemOption>;
    },
    onSuccess: (item) => {
      queryClient.invalidateQueries({ queryKey: ["items"] });
      onChange(item.id);
      setAdding(false);
      setSku("");
      setName("");
    },
  });

  if (adding) {
    return (
      <div className="flex flex-wrap items-start gap-2">
        {/* Swapping the <select> out for these inputs would otherwise drop
            the surrounding form's `required` coverage for this field -
            mirror it here so submitting mid-create still gets blocked.
            Zero-size but left in normal flow (not sr-only's absolute clip)
            so the native validation bubble anchors right here, next to the
            SKU/Name inputs, instead of off in a detached corner. */}
        <input
          ref={requiredMirrorRef}
          id={id}
          aria-label="Item"
          required={required}
          value={value}
          onChange={() => {}}
          tabIndex={-1}
          style={{ position: "static", width: 0, height: 0, padding: 0, margin: 0, border: 0, opacity: 0 }}
        />
        <Input
          placeholder="SKU"
          value={sku}
          onChange={(e) => setSku(e.target.value)}
          className="w-28"
          autoFocus
        />
        <Input
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="min-w-[140px] flex-1"
        />
        <Button
          type="button"
          size="sm"
          variant="secondary"
          loading={createItem.isPending}
          disabled={!sku || !name}
          onClick={() => createItem.mutate()}
        >
          Create
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => {
            setAdding(false);
            createItem.reset();
            setSku("");
            setName("");
          }}
        >
          Cancel
        </Button>
        {createItem.isError && (
          <p className="w-full text-xs text-destructive">{(createItem.error as Error).message}</p>
        )}
      </div>
    );
  }

  return (
    <Select
      id={id}
      value={value}
      required={required}
      onChange={(e) => {
        if (e.target.value === NEW_ITEM_VALUE) {
          onChange("");
          setAdding(true);
        } else {
          onChange(e.target.value);
        }
      }}
    >
      <option value="" disabled>
        Select an item
      </option>
      {items
        ?.filter((item) => item.id !== excludeId)
        .map((item) => (
          <option key={item.id} value={item.id}>
            {item.sku} - {item.name}
          </option>
        ))}
      <option value={NEW_ITEM_VALUE}>+ New item...</option>
    </Select>
  );
}
```

### Field
- File: `frontend/src/components/Field.tsx`
- Description: Form-field wrapper: `Label` + child input + optional hint text, consistent vertical spacing. Used in nearly every form across the app.
- Key props: `label: string`, `htmlFor?: string`, `hint?: ReactNode`, `className?: string`, `children: ReactNode`.

```tsx
import type { ReactNode } from "react";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

export function Field({
  label,
  htmlFor,
  hint,
  className,
  children,
}: {
  label: string;
  htmlFor?: string;
  hint?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("space-y-2", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
```

---

## Supporting utility

### `cn()` helper
- File: `frontend/src/lib/utils.ts`

```tsx
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge conditional class names, de-duplicating conflicting Tailwind utilities. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```
