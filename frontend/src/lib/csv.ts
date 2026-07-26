// Minimal quote-aware CSV parser: double-quoted fields may contain commas,
// newlines are row separators (\n or \r\n), "" inside a quoted field is an
// escaped quote. Enough for forecast imports; not a general RFC 4180 reader.
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let field = "";
  let row: string[] = [];
  let inQuotes = false;
  let sawAny = false;

  const pushField = () => {
    row.push(field);
    field = "";
  };
  const pushRow = () => {
    pushField();
    if (row.length > 1 || row[0] !== "") rows.push(row);
    row = [];
  };

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
      sawAny = true;
    } else if (ch === ",") {
      pushField();
      sawAny = true;
    } else if (ch === "\n") {
      pushRow();
    } else if (ch !== "\r") {
      field += ch;
      sawAny = true;
    }
  }
  if (sawAny && (field !== "" || row.length > 0)) pushRow();
  return rows;
}
