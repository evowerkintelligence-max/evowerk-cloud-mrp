import { X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { AGG_FNS, FILTER_OPS, type AggFn, type FilterOp, type FilterRow, type SelectRow } from "./stepConfig";

export function FilterRowsEditor({
  rows, columns, onChange,
}: {
  rows: FilterRow[];
  columns: string[];
  onChange: (rows: FilterRow[]) => void;
}) {
  function update(i: number, patch: Partial<FilterRow>) {
    onChange(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }
  function remove(i: number) {
    onChange(rows.filter((_, idx) => idx !== i));
  }
  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-2">
          <Select className="w-32" value={row.column} onChange={(e) => update(i, { column: e.target.value })}>
            <option value="">column…</option>
            {columns.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </Select>
          <Select className="w-28" value={row.op} onChange={(e) => update(i, { op: e.target.value as FilterOp })}>
            {FILTER_OPS.map((op) => (
              <option key={op} value={op}>{op}</option>
            ))}
          </Select>
          <Input
            className="flex-1"
            value={row.value}
            placeholder={row.op === "in" ? "comma, separated, values" : "value"}
            onChange={(e) => update(i, { value: e.target.value })}
          />
          <Button variant="ghost" size="icon" aria-label="Remove filter" onClick={() => remove(i)}>
            <X className="size-4" />
          </Button>
        </div>
      ))}
      <Button
        variant="secondary"
        size="sm"
        onClick={() => onChange([...rows, { column: columns[0] ?? "", op: "eq", value: "" }])}
      >
        + Add filter
      </Button>
    </div>
  );
}

export function SelectRowsEditor({
  rows, columns, onChange,
}: {
  rows: SelectRow[];
  columns: string[];
  onChange: (rows: SelectRow[]) => void;
}) {
  function update(i: number, patch: Partial<SelectRow>) {
    onChange(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }
  function remove(i: number) {
    onChange(rows.filter((_, idx) => idx !== i));
  }
  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-2">
          <Select className="w-32" value={row.column} onChange={(e) => update(i, { column: e.target.value })}>
            <option value="">column…</option>
            {columns.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </Select>
          <Select className="w-24" value={row.agg} onChange={(e) => update(i, { agg: e.target.value as AggFn })}>
            {AGG_FNS.map((agg) => (
              <option key={agg} value={agg}>{agg}</option>
            ))}
          </Select>
          <Input
            className="flex-1"
            value={row.alias}
            placeholder="alias (optional)"
            onChange={(e) => update(i, { alias: e.target.value })}
          />
          <Button variant="ghost" size="icon" aria-label="Remove aggregation" onClick={() => remove(i)}>
            <X className="size-4" />
          </Button>
        </div>
      ))}
      <Button
        variant="secondary"
        size="sm"
        onClick={() => onChange([...rows, { column: columns[0] ?? "", agg: "sum", alias: "" }])}
      >
        + Add aggregation
      </Button>
    </div>
  );
}
