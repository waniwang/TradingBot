"use client";

import { useEffect, useState } from "react";
import { Header } from "@/components/layout/header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EquityChart } from "@/components/dashboard/equity-chart";
import { fetchAPI } from "@/lib/api";
import type { BotStatus, DailyPnl, PerformanceSummary } from "@/lib/types";

export default function PerformancePage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [pnl, setPnl] = useState<DailyPnl[]>([]);
  const [summary, setSummary] = useState<PerformanceSummary | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
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
    } catch (e) {
      console.error("Failed to fetch:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  return (
    <div className="flex min-h-screen flex-col">
      <Header status={status} onRefresh={refresh} loading={loading} />
      <main className="flex-1 space-y-6 p-6">
        <h2 className="text-lg font-semibold">Performance (Last 90 Days)</h2>

        {summary && <StatsCards summary={summary} />}

        <EquityChart data={pnl} />

        {summary && Object.keys(summary.strategy_breakdown).length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">By Strategy</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {Object.entries(summary.strategy_breakdown).map(([name, stats]) => (
                  <div
                    key={name}
                    className="rounded-lg border border-border p-3"
                  >
                    <p className="text-xs font-medium text-muted-foreground">{name}</p>
                    <p className={`mt-1 text-lg font-bold tabular-nums ${stats.pnl >= 0 ? "text-profit" : "text-loss"}`}>
                      ${stats.pnl.toFixed(2)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {stats.trades} trades &middot; {stats.trades > 0 ? Math.round((stats.winners / stats.trades) * 100) : 0}% win
                    </p>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  );
}

function StatsCards({ summary }: { summary: PerformanceSummary }) {
  const cards = [
    { title: "Total P&L", value: `$${summary.total_pnl.toFixed(2)}`, color: summary.total_pnl >= 0 ? "text-profit" : "text-loss" },
    { title: "Win Rate", value: `${summary.win_rate.toFixed(1)}%`, color: "" },
    { title: "Total Trades", value: `${summary.total_trades}`, color: "" },
    { title: "Profit Factor", value: `${summary.profit_factor.toFixed(2)}`, color: "" },
    { title: "Best Day", value: `$${summary.best_day.toFixed(2)}`, color: "text-profit" },
    { title: "Worst Day", value: `$${summary.worst_day.toFixed(2)}`, color: "text-loss" },
    { title: "Avg Win", value: `$${summary.avg_win.toFixed(2)}`, color: "text-profit" },
    { title: "Avg Loss", value: `$${summary.avg_loss.toFixed(2)}`, color: "text-loss" },
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
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
