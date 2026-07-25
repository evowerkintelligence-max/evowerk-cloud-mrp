export interface ExistingForecastLite {
  item_id: string;
  period_start: string; // YYYY-MM-DD
  source: string;
}

export interface ConflictSummary {
  total: number;
  manual: number;
  defaultChecked: boolean;
}

// ISO date strings compare correctly as strings, so the window check is
// plain lexical comparison, inclusive on both ends to match the accept
// route's delete window.
export function summarizeConflicts(
  itemId: string,
  windowStart: string,
  windowEnd: string,
  existing: ExistingForecastLite[],
): ConflictSummary {
  let total = 0;
  let manual = 0;
  for (const fc of existing) {
    if (fc.item_id !== itemId) continue;
    if (fc.period_start < windowStart || fc.period_start > windowEnd) continue;
    total++;
    if (fc.source === "manual") manual++;
  }
  return { total, manual, defaultChecked: total === 0 };
}
