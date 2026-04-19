"use client";

import { useEffect, useState, useCallback } from "react";
import { CalendarOff } from "lucide-react";
import { Header } from "@/components/layout/header";
import { PipelineTimeline } from "@/components/dashboard/pipeline-timeline";
import { PipelineHistoryCalendar } from "@/components/dashboard/pipeline-history-calendar";
import { PipelineDayDetail } from "@/components/dashboard/pipeline-day-detail";
import { fetchAPI } from "@/lib/api";
import { useAutoRefresh } from "@/lib/hooks";
import { PipelineJobDetailModal } from "@/components/dashboard/pipeline-job-detail-modal";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { BotStatus, PipelineData, PipelineHistoryResponse, SelectedPipelineJob, StrategyListResponse, StrategyInfo } from "@/lib/types";

export default function PipelinePage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [pipeline, setPipeline] = useState<PipelineData | null>(null);
  const [history, setHistory] = useState<PipelineHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<SelectedPipelineJob | null>(null);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategyFilter, setStrategyFilter] = useState<string>("all");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, pipe, hist, strats] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<PipelineData>("/api/pipeline"),
        fetchAPI<PipelineHistoryResponse>("/api/pipeline/history?days=14"),
        fetchAPI<StrategyListResponse>("/api/strategies"),
      ]);
      setStatus(s);
      setPipeline(pipe);
      setHistory(hist);
      setStrategies(strats.strategies);
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
  const disabledStrategySlugs = new Set(strategies.filter((s) => !s.enabled).map((s) => s.slug));

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
        <PipelineTimeline
          data={pipeline}
          onSelectJob={setSelectedJob}
          strategyFilter={strategyFilter}
          onStrategyFilterChange={setStrategyFilter}
          strategies={strategies}
        />

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

        </section>

        {/* Day detail modal (opens when a date is clicked in the calendar) */}
        <Dialog
          open={!!selectedDay}
          onOpenChange={(open) => !open && setSelectedDate(null)}
        >
          <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-auto">
            <DialogHeader>
              <DialogTitle>
                {selectedDay
                  ? new Date(selectedDay.date + "T12:00:00").toLocaleDateString(
                      "en-US",
                      {
                        weekday: "long",
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                      },
                    )
                  : ""}
              </DialogTitle>
            </DialogHeader>
            <PipelineDayDetail
              day={selectedDay}
              onSelectJob={setSelectedJob}
              strategyFilter={strategyFilter}
              disabledStrategySlugs={disabledStrategySlugs}
            />
          </DialogContent>
        </Dialog>

        <PipelineJobDetailModal
          job={selectedJob}
          onClose={() => setSelectedJob(null)}
        />
      </main>
    </div>
  );
}
