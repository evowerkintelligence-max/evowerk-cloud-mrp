export function BreakdownBars({ items }: { items: { label: string; value: number }[] }) {
  if (items.length === 0) return null;
  const max = Math.max(...items.map((i) => Math.abs(i.value)), 1);
  return (
    <div className="space-y-1.5">
      {items.map((item) => (
        <div key={item.label} className="flex items-center gap-2 text-xs">
          <span className="w-24 shrink-0 truncate text-muted-foreground" title={item.label}>
            {item.label}
          </span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-secondary">
            <div
              className="h-full rounded-full bg-primary/70"
              style={{ width: `${(Math.abs(item.value) / max) * 100}%` }}
            />
          </div>
          <span className="w-14 shrink-0 text-right tabular-nums text-foreground">
            {item.value.toLocaleString(undefined, { maximumFractionDigits: 1 })}
          </span>
        </div>
      ))}
    </div>
  );
}
