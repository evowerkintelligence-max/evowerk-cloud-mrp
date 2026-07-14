export function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export function plusDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
