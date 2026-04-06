"use client";

import { useEffect, useState, useCallback } from "react";
import { CalendarOff } from "lucide-react";
import { Header } from "@/components/layout/header";
import { PipelineTimeline } from "@/components/dashboard/pipeline-timeline";
import { PipelineHistoryCalendar } from "@/components/dashboard/pipeline-history-calendar";
import { PipelineDayDetail } from "@/components/dashboard/pipeline-day-detail";
import { PipelineRecentLog } from "@/components/dashboard/pipeline-recent-log";
import { fetchAPI } from "@/lib/api";
import { useAutoRefresh } from "@/lib/hooks";
import type { BotStatus, PipelineData, PipelineHistoryResponse } from "@/lib/types";

export default function PipelinePage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [pipeline, setPipeline] = useState<PipelineData | null>(null);
  const [history, setHistory] = useState<PipelineHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, pipe, hist] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<PipelineData>("/api/pipeline"),
        fetchAPI<PipelineHistoryResponse>("/api/pipeline/history?days=14"),
      ]);
      setStatus(s);
      setPipeline(pipe);
      setHistory(hist);
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

  const isNonTradingDay = pipeline && !pipeline.is_trading_day;
  const selectedDay = history?.days.find((d) => d.date === selectedDate) || null;

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
        <h2 className="text-lg font-semibold">Pipeline</h2>

        {/* Non-trading day banner */}
        {isNonTradingDay && (
          <div className="flex items-center gap-3 rounded-lg border border-border bg-muted/50 px-4 py-3">
            <CalendarOff className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="text-sm">
              <span className="font-medium">Market closed today</span>
              {pipeline.last_trading_date && (
                <span className="ml-2 text-muted-foreground">
                  Last trading day: {pipeline.last_trading_date}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Live pipeline timeline */}
        <PipelineTimeline data={pipeline} />

        {/* History section */}
        <section className="space-y-3">
          <h3 className="text-sm font-medium text-muted-foreground">
            Pipeline History
          </h3>

          {/* Calendar heatmap */}
          {history ? (
            <PipelineHistoryCalendar
              days={history.days}
              selectedDate={selectedDate}
              onSelectDate={setSelectedDate}
            />
          ) : (
            <div className="h-16 animate-pulse rounded-lg bg-muted" />
          )}

          {/* Selected day detail */}
          <PipelineDayDetail day={selectedDay} />

          {/* Recent executions log */}
          {history && (
            <PipelineRecentLog executions={history.recent_executions} />
          )}
        </section>
      </main>
    </div>
  );
}
