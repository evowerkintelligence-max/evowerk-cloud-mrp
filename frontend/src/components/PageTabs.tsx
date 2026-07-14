import { cn } from "@/lib/utils";

export function PageTabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { value: T; label: string }[];
  active: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-border bg-muted/60 p-1 shadow-card">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          onClick={() => onChange(tab.value)}
          className={cn(
            "rounded-md px-3 py-1.5 text-sm font-medium transition-all duration-150",
            active === tab.value
              ? "bg-card text-foreground shadow-[0_1px_2px_0_hsl(224_42%_2%/0.5),inset_0_0_0_1px_hsl(var(--border))]"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
