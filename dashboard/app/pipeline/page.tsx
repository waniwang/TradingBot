"use client";

import { useEffect, useState, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { PipelineTimeline, formatDuration } from "@/components/dashboard/pipeline-timeline";
import { Badge } from "@/components/ui/badge";
import { fetchAPI } from "@/lib/api";
import { useAutoRefresh } from "@/lib/hooks";
import type { BotStatus, PipelineData, PipelineHistoryDay, PipelineExecution } from "@/lib/types";

export default function PipelinePage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [pipeline, setPipeline] = useState<PipelineData | null>(null);
  const [history, setHistory] = useState<PipelineHistoryDay[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, pipe, hist] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<PipelineData>("/api/pipeline"),
        fetchAPI<{ days: PipelineHistoryDay[] }>("/api/pipeline/history?days=14"),
      ]);
      setStatus(s);
      setPipeline(pipe);
      setHistory(hist.days);
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
      <main className="flex-1 space-y-6 p-6">
        <h2 className="text-lg font-semibold">Pipeline</h2>

        <PipelineTimeline data={pipeline} hideHistory />

        <section>
          <h3 className="mb-3 text-sm font-medium text-muted-foreground">
            Execution History (14 days)
          </h3>
          {!history ? (
            <div className="h-48 animate-pulse rounded-lg bg-muted" />
          ) : history.length === 0 ? (
            <div className="rounded-lg border border-border p-8 text-center text-sm text-muted-foreground">
              No execution history
            </div>
          ) : (
            <div className="space-y-4">
              {history.map((day) => (
                <HistoryDay key={day.date} day={day} />
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

const STALENESS_MS = 10 * 60 * 1000;

function isStale(exec: PipelineExecution): boolean {
  if (exec.status !== "running") return false;
  if (!exec.started_at) return false;
  return Date.now() - new Date(exec.started_at).getTime() > STALENESS_MS;
}

function getDisplayStatus(exec: PipelineExecution): string {
  return isStale(exec) ? "stale" : exec.status;
}

function HistoryDay({ day }: { day: PipelineHistoryDay }) {
  const successCount = day.executions.filter((e) => e.status === "success").length;
  const failedCount = day.executions.filter((e) => e.status === "failed").length;
  const staleCount = day.executions.filter((e) => isStale(e)).length;
  const total = day.executions.length;

  return (
    <div className="rounded-lg border border-border">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <span className="text-sm font-medium">{day.date}</span>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="tabular-nums">{total} jobs</span>
          {successCount > 0 && (
            <span className="text-profit tabular-nums">{successCount} passed</span>
          )}
          {failedCount > 0 && (
            <span className="text-loss tabular-nums">{failedCount} failed</span>
          )}
          {staleCount > 0 && (
            <span className="text-yellow-400 tabular-nums">{staleCount} stale</span>
          )}
        </div>
      </div>
      <div className="divide-y divide-border">
        {day.executions.map((exec) => (
          <HistoryRow key={exec.id} exec={exec} />
        ))}
      </div>
    </div>
  );
}

function HistoryRow({ exec }: { exec: PipelineExecution }) {
  const status = getDisplayStatus(exec);

  const startedTime = exec.started_at
    ? new Date(exec.started_at).toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "-";

  const duration =
    exec.duration_seconds != null ? formatDuration(exec.duration_seconds) : "-";

  return (
    <div className="flex items-center gap-3 px-4 py-2 text-sm">
      <span
        className={`h-2 w-2 shrink-0 rounded-full ${
          status === "success"
            ? "bg-profit"
            : status === "failed"
            ? "bg-loss"
            : status === "stale"
            ? "bg-yellow-500"
            : status === "running"
            ? "bg-blue-500 animate-pulse"
            : "bg-muted-foreground"
        }`}
      />
      <span className="w-40 shrink-0 truncate font-medium">{exec.label}</span>
      <Badge
        className={`text-[10px] px-1.5 py-0 ${
          status === "success"
            ? "bg-profit/15 text-profit"
            : status === "failed"
            ? "bg-loss/15 text-loss"
            : status === "stale"
            ? "bg-yellow-500/15 text-yellow-400"
            : status === "running"
            ? "bg-blue-500/15 text-blue-400"
            : "bg-muted text-muted-foreground"
        }`}
      >
        {status}
      </Badge>
      <span className="w-16 shrink-0 text-xs tabular-nums text-muted-foreground">
        {startedTime}
      </span>
      <span className="w-16 shrink-0 text-xs tabular-nums text-muted-foreground">
        {duration}
      </span>
      {exec.result_summary && (
        <span className="truncate text-xs text-muted-foreground">
          {exec.result_summary}
        </span>
      )}
      {exec.error && (
        <span className="truncate text-xs text-loss" title={exec.error}>
          {exec.error.slice(0, 80)}
        </span>
      )}
    </div>
  );
}
