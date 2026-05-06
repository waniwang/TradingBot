"use client";

import { useEffect, useState, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { PortfolioCards } from "@/components/dashboard/portfolio-cards";
import { PositionsTable } from "@/components/dashboard/positions-table";
import { TradeAttempts } from "@/components/dashboard/trade-attempts";
import { PipelineStatus } from "@/components/dashboard/pipeline-status";
import { fetchAPI } from "@/lib/api";
import { useAutoRefresh } from "@/lib/hooks";
import type { BotStatus, Portfolio, OpenPosition, TradeAttempt, PipelineData, MarketData, MarketIndex } from "@/lib/types";

export default function OverviewPage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [positions, setPositions] = useState<OpenPosition[]>([]);
  const [attempts, setAttempts] = useState<TradeAttempt[]>([]);
  const [pipeline, setPipeline] = useState<PipelineData | null>(null);
  const [marketIndices, setMarketIndices] = useState<MarketIndex[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, p, pos, att, pipe, mkt] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<Portfolio>("/api/portfolio"),
        fetchAPI<OpenPosition[]>("/api/positions"),
        fetchAPI<TradeAttempt[]>("/api/attempts/today"),
        fetchAPI<PipelineData>("/api/pipeline"),
        fetchAPI<MarketData>("/api/market"),
      ]);
      setStatus(s);
      setPortfolio(p);
      setPositions(pos);
      setAttempts(att);
      setPipeline(pipe);
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
        <PipelineStatus data={pipeline} />

        <PortfolioCards data={portfolio} />

        <section>
          <h2 className="mb-3 text-sm font-medium text-muted-foreground">
            Open Positions
          </h2>
          <PositionsTable positions={positions} />
        </section>

        <section>
          <h2 className="mb-3 text-sm font-medium text-muted-foreground">
            Today&apos;s Attempts
          </h2>
          <TradeAttempts
            attempts={attempts}
            emptyMessage="No attempts fired today"
          />
        </section>
      </main>
    </div>
  );
}
