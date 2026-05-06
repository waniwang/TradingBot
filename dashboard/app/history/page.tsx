"use client";

import { useEffect, useState, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { fetchAPI } from "@/lib/api";
import { useAutoRefresh } from "@/lib/hooks";
import { TradeAttempts } from "@/components/dashboard/trade-attempts";
import type { BotStatus, TradeAttempt } from "@/lib/types";

export default function HistoryPage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [attempts, setAttempts] = useState<TradeAttempt[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, a] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<TradeAttempt[]>("/api/attempts?limit=100"),
      ]);
      setStatus(s);
      setAttempts(a);
      setLastUpdated(new Date());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  const { paused, setPaused } = useAutoRefresh(refresh, 60_000, 300_000);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Stats over filled & closed attempts only — that's where P&L is meaningful.
  const closed = attempts.filter((a) => a.outcome === "filled_closed" && a.pnl != null);
  const totalPnl = closed.reduce((sum, a) => sum + (a.pnl ?? 0), 0);
  const winners = closed.filter((a) => (a.pnl ?? 0) > 0).length;
  const winRate = closed.length > 0 ? (winners / closed.length) * 100 : 0;
  const filledCount = attempts.filter(
    (a) => a.outcome === "filled_open" || a.outcome === "filled_closed",
  ).length;
  const failedCount = attempts.filter(
    (a) => a.outcome === "did_not_fill" || a.outcome === "broker_rejected",
  ).length;

  return (
    <div className="flex min-h-screen flex-col">
      <Header
        status={status}
        onRefresh={refresh}
        loading={loading}
        lastUpdated={lastUpdated}
        error={error}
        autoRefreshPaused={paused}
        onToggleAutoRefresh={() => setPaused(!paused)}
      />
      <main className="flex-1 space-y-4 p-6">
        <div className="flex items-baseline justify-between">
          <div>
            <h2 className="text-lg font-semibold">Trade Attempts</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Every order the bot tried to place — filled, unfilled, or rejected.
            </p>
          </div>
          <div className="flex gap-4 text-sm text-muted-foreground">
            <span>{attempts.length} attempts</span>
            <span>{filledCount} filled</span>
            {failedCount > 0 && (
              <span className="text-loss">{failedCount} failed</span>
            )}
            {closed.length > 0 && (
              <>
                <span className={totalPnl >= 0 ? "text-profit" : "text-loss"}>
                  P&L: {totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)}
                </span>
                <span>Win: {winRate.toFixed(0)}%</span>
              </>
            )}
          </div>
        </div>
        <TradeAttempts
          attempts={attempts}
          showDate
          emptyMessage="No attempts recorded yet"
        />
      </main>
    </div>
  );
}
