"use client";

import { useEffect, useState, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { PortfolioCards } from "@/components/dashboard/portfolio-cards";
import { PositionsTable } from "@/components/dashboard/positions-table";
import { EquityChart } from "@/components/dashboard/equity-chart";
import { RecentSignals } from "@/components/dashboard/recent-signals";
import { PipelineTimeline } from "@/components/dashboard/pipeline-timeline";
import { RiskMeter } from "@/components/dashboard/risk-meter";
import { fetchAPI } from "@/lib/api";
import { useAutoRefresh } from "@/lib/hooks";
import type { BotStatus, Portfolio, OpenPosition, DailyPnl, SignalToday, PipelineData, RiskData, MarketData, MarketIndex } from "@/lib/types";

export default function OverviewPage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [positions, setPositions] = useState<OpenPosition[]>([]);
  const [pnl, setPnl] = useState<DailyPnl[]>([]);
  const [signals, setSignals] = useState<SignalToday[]>([]);
  const [pipeline, setPipeline] = useState<PipelineData | null>(null);
  const [riskData, setRiskData] = useState<RiskData | null>(null);
  const [marketIndices, setMarketIndices] = useState<MarketIndex[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, p, pos, pnlData, sig, pipe, rd, mkt] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<Portfolio>("/api/portfolio"),
        fetchAPI<OpenPosition[]>("/api/positions"),
        fetchAPI<DailyPnl[]>("/api/performance/pnl"),
        fetchAPI<SignalToday[]>("/api/signals/today"),
        fetchAPI<PipelineData>("/api/pipeline"),
        fetchAPI<RiskData>("/api/risk"),
        fetchAPI<MarketData>("/api/market"),
      ]);
      setStatus(s);
      setPortfolio(p);
      setPositions(pos);
      setPnl(pnlData);
      setSignals(sig);
      setPipeline(pipe);
      setRiskData(rd);
      setMarketIndices(mkt.indices || []);
      setLastUpdated(new Date());
      setError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Unknown error";
      setError(msg);
      console.error("Failed to fetch data:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  const { paused, setPaused } = useAutoRefresh(refresh);

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
        marketIndices={marketIndices}
      />
      <main className="flex-1 space-y-6 p-6">
        <PipelineTimeline data={pipeline} />

        <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
          <PortfolioCards data={portfolio} />
          <RiskMeter data={riskData} />
        </div>

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
