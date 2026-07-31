import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Alert } from "@/components/ui/alert";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { DIMENSION_OPTIONS, rcaBannerSentence, type RcaResult } from "./types";

async function fetchRca(path: string): Promise<RcaResult> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error("Failed to load the insight");
  return res.json();
}

function dimensionLabel(dimension: string): string {
  return DIMENSION_OPTIONS.find((o) => o.value === dimension)?.label ?? dimension;
}

export function InsightBanner() {
  const [dismissed, setDismissed] = useState(false);
  const [showWhy, setShowWhy] = useState(false);
  const [manualDimension, setManualDimension] = useState<string | null>(null);

  const auto = useQuery<RcaResult>({
    queryKey: ["inventory-rca-auto"],
    queryFn: () => fetchRca("/inventory/rca/auto"),
    enabled: manualDimension === null,
  });
  const manual = useQuery<RcaResult>({
    queryKey: ["inventory-rca", manualDimension],
    queryFn: () => fetchRca(`/inventory/rca?dimension=${manualDimension}`),
    enabled: manualDimension !== null,
  });

  const result = manualDimension === null ? auto.data : manual.data;
  if (dismissed || !result) return null;

  const sentence = rcaBannerSentence(result);
  if (manualDimension === null && !sentence) return null;

  return (
    <Alert variant="info" className="mb-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <p>{sentence ?? `Nothing stands out for ${dimensionLabel(manualDimension ?? "").toLowerCase()}.`}</p>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
            {sentence && (
              <button type="button" className="underline" onClick={() => setShowWhy((v) => !v)}>
                {showWhy ? "Hide the numbers" : "See why"}
              </button>
            )}
            <button
              type="button"
              className="underline"
              onClick={() =>
                setManualDimension(manualDimension === null ? result.dimension || "supplier" : null)
              }
            >
              {manualDimension === null ? "Check a different cause" : "Back to the top cause"}
            </button>
            {manualDimension !== null && (
              <Select
                aria-label="Cause"
                value={manualDimension}
                onChange={(e) => setManualDimension(e.target.value)}
              >
                {DIMENSION_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </Select>
            )}
          </div>
          {showWhy && sentence && (
            <TableWrap className="mt-3">
              <Table>
                <THead>
                  <TR>
                    <TH>Value</TH>
                    <TH>At risk</TH>
                    <TH>Expected</TH>
                    <TH>Significant</TH>
                  </TR>
                </THead>
                <TBody>
                  {result.values.map((v) => (
                    <TR key={v.value}>
                      <TD>{v.value}</TD>
                      <TD>{v.at_risk_count}</TD>
                      <TD>{v.expected_at_risk_count.toFixed(1)}</TD>
                      <TD>{v.is_significant ? "Yes" : "No"}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </div>
        <button
          type="button"
          className="text-xs text-muted-foreground underline"
          onClick={() => setDismissed(true)}
        >
          Dismiss
        </button>
      </div>
    </Alert>
  );
}
