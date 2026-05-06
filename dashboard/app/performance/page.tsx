"use client";

import { useEffect, useState, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EquityChart } from "@/components/dashboard/equity-chart";
import { fetchAPI } from "@/lib/api";
import { useAutoRefresh } from "@/lib/hooks";
import type { BotStatus, DailyPnl, PerformanceSummary } from "@/lib/types";

export default function PerformancePage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [pnl, setPnl] = useState<DailyPnl[]>([]);
  const [summary, setSummary] = useState<PerformanceSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, p, sum] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<DailyPnl[]>("/api/performance/pnl?days=90"),
        fetchAPI<PerformanceSummary>("/api/performance/summary?days=90"),
      ]);
      setStatus(s);
      setPnl(p);
      setSummary(sum);
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
      <main className="flex-1 space-y-6 p-6">
        <h2 className="text-lg font-semibold">Performance (Last 90 Days)</h2>

        {summary && <StatsCards summary={summary} />}

        <EquityChart data={pnl} />

        {summary && Object.keys(summary.strategy_breakdown).length > 0 && (
          <StrategyBreakdown breakdown={summary.strategy_breakdown} />
        )}
      </main>
    </div>
  );
}

function formatR(r: number): string {
  return `${r >= 0 ? "+" : ""}${r.toFixed(2)}R`;
}

function formatDollars(value: number, signed = false): string {
  const abs = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (signed) return `${value >= 0 ? "+" : "-"}$${abs}`;
  return value < 0 ? `-$${abs}` : `$${abs}`;
}

function StatsCards({ summary }: { summary: PerformanceSummary }) {
  const cards = [
    {
      title: "Total Return",
      value: `${summary.total_return_pct >= 0 ? "+" : ""}${summary.total_return_pct.toFixed(2)}%`,
      sub: formatDollars(summary.total_pnl_dollars, true),
      color: summary.total_return_pct >= 0 ? "text-profit" : "text-loss",
    },
    {
      title: "Expectancy",
      value: formatR(summary.expectancy_r),
      sub: "avg R per trade",
      color: summary.expectancy_r >= 0 ? "text-profit" : "text-loss",
    },
    {
      title: "Win Rate",
      value: `${summary.win_rate.toFixed(1)}%`,
      sub: `${summary.total_trades} trade${summary.total_trades === 1 ? "" : "s"}`,
      color: "",
    },
    {
      title: "Profit Factor",
      value: summary.profit_factor.toFixed(2),
      sub: "wins$ / losses$",
      color: "",
    },
    {
      title: "Avg Win",
      value: formatR(summary.avg_win_r),
      sub: formatDollars(summary.avg_win_dollars, true),
      color: "text-profit",
    },
    {
      title: "Avg Loss",
      value: formatR(summary.avg_loss_r),
      sub: formatDollars(summary.avg_loss_dollars, true),
      color: "text-loss",
    },
    {
      title: "Best Trade",
      value: formatR(summary.best_trade_r),
      sub: formatDollars(summary.best_trade_pnl, true),
      color: "text-profit",
    },
    {
      title: "Worst Trade",
      value: formatR(summary.worst_trade_r),
      sub: formatDollars(summary.worst_trade_pnl, true),
      color: "text-loss",
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      {cards.map((c) => (
        <Card key={c.title}>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-medium text-muted-foreground">{c.title}</CardTitle>
          </CardHeader>
          <CardContent>
            <div className={`text-xl font-bold tabular-nums ${c.color}`}>{c.value}</div>
            <p className="mt-1 text-xs text-muted-foreground tabular-nums">{c.sub}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function StrategyBreakdown({
  breakdown,
}: {
  breakdown: PerformanceSummary["strategy_breakdown"];
}) {
  // Sort by avg_r descending so the strongest strategies surface first.
  const entries = Object.entries(breakdown).sort(
    (a, b) => b[1].avg_r - a[1].avg_r,
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">By Strategy (avg R per trade)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {entries.map(([name, stats]) => (
            <div key={name} className="rounded-lg border border-border p-3">
              <p className="text-xs font-medium text-muted-foreground">{name}</p>
              <p
                className={`mt-1 text-lg font-bold tabular-nums ${
                  stats.avg_r >= 0 ? "text-profit" : "text-loss"
                }`}
              >
                {formatR(stats.avg_r)}
              </p>
              <p
                className={`text-xs tabular-nums ${
                  stats.total_pnl >= 0 ? "text-profit" : "text-loss"
                }`}
              >
                {formatDollars(stats.total_pnl, true)}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {stats.trades} trade{stats.trades === 1 ? "" : "s"} &middot;{" "}
                {stats.win_rate.toFixed(0)}% win
              </p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
