import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CornerDownLeft, Search } from "lucide-react";
import { ALL_NAV_ITEMS, type NavItem } from "@/lib/nav";
import { cn } from "@/lib/utils";

type PaletteItem = NavItem & { section: string };

/** Ctrl/⌘-K navigation palette over every page in the app.
 * `items` should already be filtered to what the current user can see
 * (see AppShell's visibleNavItems) - this component never gates on its own. */
export function CommandPalette({
  open,
  onClose,
  items = ALL_NAV_ITEMS,
}: {
  open: boolean;
  onClose: () => void;
  items?: PaletteItem[];
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        item.section.toLowerCase().includes(q) ||
        item.keywords?.some((k) => k.toLowerCase().includes(q)),
    );
  }, [query, items]);

  // Reset on (re)open: adjust-state-during-render rather than an effect.
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    if (open) {
      setQuery("");
      setSelected(0);
    }
  }

  useEffect(() => {
    // Focus after the overlay mounts.
    if (open) requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

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
            onChange={(e) => {
              setQuery(e.target.value);
              setSelected(0);
            }}
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
              No pages match “{query}”.
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
