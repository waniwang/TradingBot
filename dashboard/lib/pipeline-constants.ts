/* ── Phase metadata ──────────────────────────────────────────────── */

export const PHASE_ORDER = [
  "overnight",
  "premarket",
  "market_open",
  "afternoon",
  "close",
] as const;

export const PHASE_LABELS: Record<string, string> = {
  overnight: "Overnight",
  premarket: "Pre-Market",
  market_open: "Market Open",
  afternoon: "Afternoon Swing",
  close: "Close",
};

/* ── Category badge colors (filled style — identifies job type) ── */

export const CATEGORY_COLORS: Record<string, string> = {
  scan: "bg-blue-500/15 text-blue-400",
  trade: "bg-profit/15 text-profit",
  monitor: "bg-purple-500/15 text-purple-400",
  system: "bg-muted text-muted-foreground",
};

/* ── Status badge colors (outlined style — identifies execution state) ── */

export const STATUS_BADGE_COLORS: Record<string, string> = {
  success: "border-profit/40 text-profit bg-transparent",
  running: "border-blue-400/40 text-blue-400 bg-transparent animate-pulse",
  failed: "border-loss/40 text-loss bg-transparent",
  missed: "border-yellow-500/40 text-yellow-400 bg-transparent",
  upcoming: "border-border text-muted-foreground bg-transparent",
  skipped: "border-border text-muted-foreground bg-transparent",
  next: "border-blue-400/40 text-blue-400 bg-transparent animate-pulse",
};

export function getStatusBadgeClass(status: string): string {
  return STATUS_BADGE_COLORS[status] || STATUS_BADGE_COLORS.upcoming;
}

export function getStatusLabel(
  status: string,
  failureReason: string | null = null
): string {
  if (failureReason === "timeout") return "timed out";
  return status;
}
