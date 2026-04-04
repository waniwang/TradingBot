"use client";

import { Badge } from "@/components/ui/badge";
import { useRelativeTime } from "@/lib/hooks";
import type { BotStatus, MarketIndex } from "@/lib/types";

export function Header({
  status,
  onRefresh,
  loading,
  lastUpdated,
  error,
  autoRefreshPaused,
  onToggleAutoRefresh,
  marketIndices,
}: {
  status: BotStatus | null;
  onRefresh: () => void;
  loading: boolean;
  lastUpdated?: Date | null;
  error?: string | null;
  autoRefreshPaused?: boolean;
  onToggleAutoRefresh?: () => void;
  marketIndices?: MarketIndex[];
}) {
  const env = status?.environment || "paper";
  const running = status?.running ?? false;
  const relTime = useRelativeTime(lastUpdated ?? null);

  return (
    <>
      <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-border bg-background/80 px-6 backdrop-blur-sm">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span
              className={`h-2 w-2 rounded-full ${
                running ? "bg-profit animate-pulse" : "bg-loss"
              }`}
            />
            <span className="text-sm font-medium">
              {running ? "Running" : "Stopped"}
            </span>
          </div>

          <Badge
            variant={env === "paper" ? "outline" : "destructive"}
            className={
              env === "paper"
                ? "border-yellow-500/50 text-yellow-500"
                : "border-red-500/50 text-red-500"
            }
          >
            {env.toUpperCase()}
          </Badge>

          {status?.phase_label && (
            <span className="text-sm text-muted-foreground">
              {status.phase_label}
              {status.phase_description && (
                <span className="ml-1 text-xs">
                  — {status.phase_description}
                </span>
              )}
            </span>
          )}
        </div>

        <div className="flex items-center gap-3">
          {marketIndices && marketIndices.length > 0 && (
            <div className="flex items-center gap-2 border-r border-border pr-3">
              {marketIndices.map((idx) => (
                <span
                  key={idx.ticker}
                  className={`text-xs tabular-nums font-medium ${
                    idx.change_pct >= 0 ? "text-profit" : "text-loss"
                  }`}
                >
                  {idx.ticker}{" "}
                  {idx.change_pct >= 0 ? "+" : ""}
                  {idx.change_pct.toFixed(2)}%
                </span>
              ))}
            </div>
          )}

          {status?.next_job_label && (
            <span className="text-xs text-muted-foreground">
              Next: {status.next_job_label}
              {status.countdown_seconds != null && status.countdown_seconds > 0 && (
                <span className="ml-1">
                  ({formatCountdown(status.countdown_seconds)})
                </span>
              )}
            </span>
          )}

          {relTime && (
            <span className="text-[11px] text-muted-foreground tabular-nums">
              {relTime}
            </span>
          )}

          {onToggleAutoRefresh && (
            <button
              onClick={onToggleAutoRefresh}
              className={`rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors ${
                autoRefreshPaused
                  ? "text-yellow-500 hover:text-yellow-400"
                  : "text-profit hover:text-profit/80"
              }`}
              title={autoRefreshPaused ? "Resume auto-refresh" : "Pause auto-refresh"}
            >
              {autoRefreshPaused ? "AUTO OFF" : "AUTO"}
            </button>
          )}

          <button
            onClick={onRefresh}
            disabled={loading}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
          >
            {loading ? "Loading..." : "Refresh"}
          </button>
        </div>
      </header>

      {error && (
        <div className="border-b border-loss/30 bg-loss/10 px-6 py-2 text-xs text-loss">
          API error: {error}
          {relTime && <span className="ml-2 text-muted-foreground">Last update: {relTime}</span>}
        </div>
      )}
    </>
  );
}

function formatCountdown(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${seconds}s`;
}
