"use client";

import { useEffect, useState, useCallback } from "react";
import { Header } from "@/components/layout/header";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { fetchAPI } from "@/lib/api";
import { useAutoRefresh } from "@/lib/hooks";
import { RecentSignals } from "@/components/dashboard/recent-signals";
import { VariationBadge } from "@/components/strategies/variation-badge";
import type { BotStatus, ClosedPosition, SignalToday } from "@/lib/types";

export default function HistoryPage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [trades, setTrades] = useState<ClosedPosition[]>([]);
  const [signals, setSignals] = useState<SignalToday[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, t, sig] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<ClosedPosition[]>("/api/positions/closed?limit=100"),
        fetchAPI<SignalToday[]>("/api/signals/history?limit=100"),
      ]);
      setStatus(s);
      setTrades(t);
      setSignals(sig);
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

  // Stats
  const totalPnl = trades.reduce((sum, t) => sum + t.pnl, 0);
  const winners = trades.filter((t) => t.pnl > 0).length;
  const winRate = trades.length > 0 ? (winners / trades.length) * 100 : 0;

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
      <main className="flex-1 space-y-8 p-6">
        <section className="space-y-4">
          <div className="flex items-baseline justify-between">
            <h2 className="text-lg font-semibold">Signal History</h2>
            <div className="text-sm text-muted-foreground">
              {signals.length} signals
            </div>
          </div>
          <RecentSignals
            signals={signals}
            showDate
            emptyMessage="No signals recorded yet"
          />
        </section>

        <div className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold">Trade History</h2>
          <div className="flex gap-4 text-sm text-muted-foreground">
            <span>{trades.length} trades</span>
            <span className={totalPnl >= 0 ? "text-profit" : "text-loss"}>
              P&L: ${totalPnl.toFixed(2)}
            </span>
            <span>Win: {winRate.toFixed(0)}%</span>
          </div>
        </div>

        {trades.length === 0 ? (
          <div className="rounded-lg border border-border p-8 text-center text-sm text-muted-foreground">
            No closed trades yet
          </div>
        ) : (
          <div className="rounded-lg border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Ticker</TableHead>
                  <TableHead>Setup</TableHead>
                  <TableHead>Side</TableHead>
                  <TableHead className="text-right">Entry</TableHead>
                  <TableHead className="text-right">Exit</TableHead>
                  <TableHead className="text-right">P&L</TableHead>
                  <TableHead className="text-right">Days</TableHead>
                  <TableHead>Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trades.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="tabular-nums text-muted-foreground">
                      {t.date
                        ? new Date(t.date).toLocaleDateString("en-US", {
                            month: "short",
                            day: "numeric",
                            year: "2-digit",
                          })
                        : "-"}
                    </TableCell>
                    <TableCell className="font-medium">{t.ticker}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <Badge variant="outline" className="text-xs">{t.setup}</Badge>
                        <VariationBadge value={t.variation} />
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={t.side === "LONG" ? "default" : "destructive"}
                        className="text-xs"
                      >
                        {t.side}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      ${t.entry.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {t.exit != null ? `$${t.exit.toFixed(2)}` : "-"}
                    </TableCell>
                    <TableCell
                      className={`text-right tabular-nums font-medium ${
                        t.pnl >= 0 ? "text-profit" : "text-loss"
                      }`}
                    >
                      {t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{t.days}</TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="text-xs capitalize">
                        {t.reason || "-"}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </main>
    </div>
  );
}
