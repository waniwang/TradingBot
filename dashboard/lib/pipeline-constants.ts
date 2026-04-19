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

/* ── Category badge colors (filled pill — identifies job type) ── */

export const CATEGORY_COLORS: Record<string, string> = {
  scan: "bg-blue-500/15 text-blue-400",
  trade: "bg-profit/15 text-profit",
  monitor: "bg-purple-500/15 text-purple-400",
  system: "bg-muted text-muted-foreground",
};

/* ── Status text colors (plain text, no pill — identifies execution state) ── */

export const STATUS_TEXT_COLORS: Record<string, string> = {
  success: "text-profit",
  running: "text-blue-400 animate-pulse",
  failed: "text-loss",
  // A missed job (scheduled but never produced a JobExecution row) is a failure
  // as far as the operator is concerned — same color, distinct label.
  missed: "text-loss",
  upcoming: "text-muted-foreground",
  skipped: "text-muted-foreground",
  next: "text-blue-400 animate-pulse",
};

export function getStatusTextClass(status: string): string {
  return STATUS_TEXT_COLORS[status] || STATUS_TEXT_COLORS.upcoming;
}

export function getStatusLabel(
  status: string,
  failureReason: string | null = null
): string {
  if (failureReason === "timeout") return "timed out";
  return status;
}

/* ── Strategy metadata ─────────────────────────────────────────��── */

export const STRATEGY_LABELS: Record<string, string> = {
  ep_earnings: "EP Earnings",
  ep_news: "EP News",
  breakout: "Breakout",
  episodic_pivot: "Episodic Pivot",
  parabolic_short: "Parabolic Short",
};
