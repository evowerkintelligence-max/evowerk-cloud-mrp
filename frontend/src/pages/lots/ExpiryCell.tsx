import { Badge } from "@/components/ui/badge";

// Mirrors LOT_EXPIRY_WARNING_DAYS in backend/app/services/morning_brief.py -
// same "worth a look" threshold, so the badge here agrees with the brief.
export const EXPIRY_WARNING_DAYS = 14;

export function daysUntil(dateStr: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(dateStr);
  target.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
}

export function ExpiryCell({ expiryDate }: { expiryDate: string | null }) {
  if (!expiryDate) return <span className="text-muted-foreground">—</span>;
  const days = daysUntil(expiryDate);
  if (days < 0) {
    return <Badge variant="danger">Expired {Math.abs(days)}d ago</Badge>;
  }
  if (days <= EXPIRY_WARNING_DAYS) {
    return (
      <Badge variant={days <= 3 ? "danger" : "warning"}>
        {expiryDate} · {days}d left
      </Badge>
    );
  }
  return <span className="text-muted-foreground">{expiryDate}</span>;
}
