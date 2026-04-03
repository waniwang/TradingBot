"use client";

import { useEffect, useState } from "react";
import { Header } from "@/components/layout/header";
import { PortfolioCards } from "@/components/dashboard/portfolio-cards";
import { PositionsTable } from "@/components/dashboard/positions-table";
import { EquityChart } from "@/components/dashboard/equity-chart";
import { RecentSignals } from "@/components/dashboard/recent-signals";
import { fetchAPI } from "@/lib/api";
import type { BotStatus, Portfolio, OpenPosition, DailyPnl, SignalToday } from "@/lib/types";

export default function OverviewPage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [positions, setPositions] = useState<OpenPosition[]>([]);
  const [pnl, setPnl] = useState<DailyPnl[]>([]);
  const [signals, setSignals] = useState<SignalToday[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      const [s, p, pos, pnlData, sig] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<Portfolio>("/api/portfolio"),
        fetchAPI<OpenPosition[]>("/api/positions"),
        fetchAPI<DailyPnl[]>("/api/performance/pnl"),
        fetchAPI<SignalToday[]>("/api/signals/today"),
      ]);
      setStatus(s);
      setPortfolio(p);
      setPositions(pos);
      setPnl(pnlData);
      setSignals(sig);
    } catch (e) {
      console.error("Failed to fetch data:", e);
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
        <PortfolioCards data={portfolio} />

        <section>
          <h2 className="mb-3 text-sm font-medium text-muted-foreground">
            Open Positions
          </h2>
          <PositionsTable positions={positions} />
        </section>

        <EquityChart data={pnl} />

        <section>
          <h2 className="mb-3 text-sm font-medium text-muted-foreground">
            Signals Today
          </h2>
          <RecentSignals signals={signals} />
        </section>
      </main>
    </div>
  );
}
