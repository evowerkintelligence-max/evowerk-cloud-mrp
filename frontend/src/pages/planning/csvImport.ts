export interface CsvForecastRow {
  item_id: string;
  period_start: string;
  quantity: string;
  notes: string | null;
}

export interface CsvRowError {
  line: number; // 1-based line in the file, header is line 1
  message: string;
}

export const CSV_TEMPLATE = 'sku,period_start,quantity,notes\nRAW-1,2026-08-01,100,"optional note"\n';

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function mapForecastCsv(
  records: string[][],
  itemIdBySku: Map<string, string>,
): { rows: CsvForecastRow[]; errors: CsvRowError[] } {
  const errors: CsvRowError[] = [];
  if (records.length === 0) {
    return { rows: [], errors: [{ line: 1, message: "File is empty" }] };
  }
  const header = records[0].map((h) => h.trim().toLowerCase());
  const okHeader =
    (header.length === 3 || (header.length === 4 && header[3] === "notes")) &&
    header[0] === "sku" &&
    header[1] === "period_start" &&
    header[2] === "quantity";
  if (!okHeader) {
    return {
      rows: [],
      errors: [{ line: 1, message: "Header must be: sku,period_start,quantity[,notes]" }],
    };
  }

  const rows: CsvForecastRow[] = [];
  for (let i = 1; i < records.length; i++) {
    const line = i + 1;
    const [skuRaw = "", dateRaw = "", qtyRaw = "", notesRaw = ""] = records[i];
    const sku = skuRaw.trim();
    const item_id = itemIdBySku.get(sku);
    if (!item_id) {
      errors.push({ line, message: `Unknown SKU: ${sku || "(empty)"}` });
      continue;
    }
    const period_start = dateRaw.trim();
    if (!DATE_RE.test(period_start) || Number.isNaN(Date.parse(period_start))) {
      errors.push({ line, message: `Bad date (expected YYYY-MM-DD): ${period_start}` });
      continue;
    }
    const quantity = qtyRaw.trim();
    const qty = Number(quantity);
    if (quantity === "" || !Number.isFinite(qty) || qty < 0) {
      errors.push({ line, message: `Quantity must be a non-negative number: ${quantity}` });
      continue;
    }
    const notes = notesRaw.trim();
    if (notes.length > 255) {
      errors.push({ line, message: "Notes longer than 255 characters" });
      continue;
    }
    rows.push({ item_id, period_start, quantity, notes: notes === "" ? null : notes });
  }
  return { rows, errors };
}
