import type { UseQueryResult } from "@tanstack/react-query";
import type { SupplierResponse, SupplierScorecard } from "./types";

export function SupplierScorecardSection({
  editing,
  scorecard,
}: {
  editing: SupplierResponse | null;
  scorecard: UseQueryResult<SupplierScorecard>;
}) {
  return (
    <>
      {editing && scorecard.data && (
        <div className="mt-6 space-y-2 border-t border-border pt-4">
          <h3 className="text-sm font-semibold">Performance</h3>
          {scorecard.data.sample_size === 0 ? (
            <p className="text-sm text-muted-foreground">Not enough data yet.</p>
          ) : (
            <div className="flex gap-4 text-sm">
              <span>
                On-time delivery:{" "}
                <strong>
                  {scorecard.data.on_time_pct != null
                    ? `${Math.round(scorecard.data.on_time_pct * 100)}%`
                    : "—"}
                </strong>
              </span>
              <span>
                Defect rate:{" "}
                <strong>
                  {scorecard.data.defect_pct != null
                    ? `${Math.round(scorecard.data.defect_pct * 100)}%`
                    : "—"}
                </strong>
              </span>
            </div>
          )}
        </div>
      )}
    </>
  );
}
