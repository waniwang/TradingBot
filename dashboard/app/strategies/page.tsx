"use client";

import { useEffect, useState, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { StrategyCard } from "@/components/strategies/strategy-card";
import { fetchAPI } from "@/lib/api";
import { useAutoRefresh } from "@/lib/hooks";
import type { BotStatus, StrategyListResponse, StrategyInfo } from "@/lib/types";

export default function StrategiesPage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, strats] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<StrategyListResponse>("/api/strategies"),
      ]);
      setStatus(s);
      setStrategies(strats.strategies);
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

  const enabled = strategies.filter((s) => s.enabled);
  const disabled = strategies.filter((s) => !s.enabled);

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
        <h2 className="text-lg font-semibold">Strategies</h2>

        {/* Active strategies */}
        {enabled.length > 0 && (
          <div className="grid gap-4 sm:grid-cols-2">
            {enabled.map((s) => (
              <StrategyCard key={s.slug} strategy={s} />
            ))}
          </div>
        )}

        {/* Disabled strategies */}
        {disabled.length > 0 && (
          <section>
            <h3 className="mb-3 text-sm font-medium text-muted-foreground">
              Disabled
            </h3>
            <div className="grid gap-4 sm:grid-cols-2 opacity-60">
              {disabled.map((s) => (
                <StrategyCard key={s.slug} strategy={s} />
              ))}
            </div>
          </section>
        )}

        {strategies.length === 0 && !loading && (
          <p className="text-sm text-muted-foreground">No strategies configured.</p>
        )}
      </main>
    </div>
  );
}
