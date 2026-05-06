"use client";

import { useEffect, useState, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { PositionsTable } from "@/components/dashboard/positions-table";
import { fetchAPI } from "@/lib/api";
import { useAutoRefresh } from "@/lib/hooks";
import type { BotStatus, OpenPosition } from "@/lib/types";

export default function PositionsPage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [positions, setPositions] = useState<OpenPosition[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, pos] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<OpenPosition[]>("/api/positions"),
      ]);
      setStatus(s);
      setPositions(pos);
      setLastUpdated(new Date());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  const { paused, setPaused } = useAutoRefresh(refresh, 30_000, 300_000);

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
      <main className="flex-1 space-y-4 p-6">
        <div className="flex items-baseline justify-between">
          <div>
            <h2 className="text-lg font-semibold">Open Positions</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Closed trades live on the History page.
            </p>
          </div>
          <span className="text-sm text-muted-foreground">
            {positions.length} open
          </span>
        </div>
        <PositionsTable positions={positions} />
      </main>
    </div>
  );
}
