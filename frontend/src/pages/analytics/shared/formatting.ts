export const fmt = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : n.toLocaleString("en-US");
export const money = (n: number) => "$" + Math.round(n).toLocaleString("en-US");
export const pct = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${n}%`);
export const fileSize = (b: number) =>
  b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`;
