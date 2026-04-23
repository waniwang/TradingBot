import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Format an ISO timestamp as "Xs ago" / "Xm ago" / "Xh Ym ago" / "Nd ago". */
export function formatRelativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "-";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "-";
  const seconds = Math.max(0, Math.floor((now - t) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/**
 * Friendly display label for a watchlist stage.
 *
 * The DB stage names carry different semantics per strategy (e.g. "active"
 * means "live today" for breakout but "scan pool snapshot" for EP swing),
 * so the tooltip explains the actual meaning in context. See CLAUDE.md and
 * docs/architecture.md for the full lifecycle.
 */
export function stageLabel(stage: string | null | undefined): string {
  const s = (stage ?? "").toLowerCase();
  switch (s) {
    case "active":    return "Candidates";
    case "ready":     return "Queued";
    case "watching":  return "Awaiting Day-2";
    case "triggered": return "Entered";
    case "filled":    return "Filled";
    case "cancelled": return "Cancelled";
    case "expired":   return "Expired";
    default:          return stage ?? "-";
  }
}

export function stageTooltip(stage: string | null | undefined): string {
  const s = (stage ?? "").toLowerCase();
  switch (s) {
    case "active":    return "Scan pool — passed filters, awaiting execution decision";
    case "ready":     return "Queued for the next execute window";
    case "watching":  return "Awaiting day-2 price confirmation (EP Strategy C)";
    case "triggered": return "Order placed with the broker";
    case "filled":    return "Order filled — position opened";
    case "cancelled": return "Order cancelled/rejected, or bot failed to trigger (e.g. snapshot error at day-2 confirm)";
    case "expired":   return "Day-2 price did not confirm (price <= gap-day close)";
    default:          return "";
  }
}
