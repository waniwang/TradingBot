"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { PipelineData, PipelineExecution, PipelineHistoryDay } from "@/lib/types";
import { fetchAPI } from "@/lib/api";

type StepStatus = "success" | "running" | "failed" | "skipped" | "upcoming" | "missed";

interface TimelineStep {
  job_id: string;
  label: string;
  time: string;
  category: string;
  status: StepStatus;
  execution: PipelineExecution | null;
}

function deriveSteps(data: PipelineData): TimelineStep[] {
  // Build a map of executions by job_id (use last one if multiple)
  const execMap = new Map<string, PipelineExecution>();
  for (const exec of data.executions) {
    execMap.set(exec.job_id, exec);
  }

  // Sort schedule by time for display
  // The schedule comes pre-ordered: nightly (17:00) first, then 06:00..15:55
  // We want chronological order within a trading day: 06:00..17:00
  const sorted = [...data.schedule].sort((a, b) => {
    const toMin = (t: string) => {
      const [h, m] = t.split(":").map(Number);
      // Treat 17:00 as previous day = push to front with negative offset
      // Actually for display, nightly scan at 17:00 is the LAST job of the cycle
      return h * 60 + m;
    };
    return toMin(a.time) - toMin(b.time);
  });

  // Current ET time for determining upcoming vs missed
  const now = new Date();
  // Convert to ET (approximate — good enough for UI)
  const etOffset = getETOffset();
  const etNow = new Date(now.getTime() + etOffset);
  const nowMinutes = etNow.getHours() * 60 + etNow.getMinutes();

  return sorted.map((job) => {
    const exec = execMap.get(job.job_id) || null;
    let status: StepStatus;

    if (exec) {
      status = exec.status as StepStatus;
    } else {
      const [h, m] = job.time.split(":").map(Number);
      const jobMinutes = h * 60 + m;
      status = jobMinutes <= nowMinutes ? "missed" : "upcoming";
    }

    return { ...job, status, execution: exec };
  });
}

function getETOffset(): number {
  // Rough ET offset in ms from UTC
  // ET is UTC-5 (EST) or UTC-4 (EDT)
  const jan = new Date(new Date().getFullYear(), 0, 1).getTimezoneOffset();
  const jul = new Date(new Date().getFullYear(), 6, 1).getTimezoneOffset();
  const isDST = new Date().getTimezoneOffset() < Math.max(jan, jul);
  const etOffsetHours = isDST ? -4 : -5;
  const localOffsetMs = new Date().getTimezoneOffset() * 60 * 1000;
  const etOffsetMs = etOffsetHours * 60 * 60 * 1000;
  return localOffsetMs + etOffsetMs;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function formatTime(time24: string): string {
  const [h, m] = time24.split(":").map(Number);
  const ampm = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 || 12;
  return `${h12}:${m.toString().padStart(2, "0")} ${ampm}`;
}

const STATUS_STYLES: Record<StepStatus, { dot: string; line: string; text: string }> = {
  success: {
    dot: "bg-profit border-profit",
    line: "bg-profit/40",
    text: "text-foreground",
  },
  running: {
    dot: "bg-blue-500 border-blue-500 animate-pulse",
    line: "bg-blue-500/40",
    text: "text-foreground",
  },
  failed: {
    dot: "bg-loss border-loss",
    line: "bg-loss/40",
    text: "text-foreground",
  },
  skipped: {
    dot: "bg-muted border-muted-foreground/30",
    line: "bg-muted",
    text: "text-muted-foreground",
  },
  upcoming: {
    dot: "bg-transparent border-muted-foreground/40",
    line: "bg-muted",
    text: "text-muted-foreground",
  },
  missed: {
    dot: "bg-transparent border-yellow-500/60",
    line: "bg-muted",
    text: "text-yellow-500",
  },
};

const CATEGORY_COLORS: Record<string, string> = {
  scan: "bg-blue-500/15 text-blue-400",
  trade: "bg-profit/15 text-profit",
  monitor: "bg-purple-500/15 text-purple-400",
  system: "bg-muted text-muted-foreground",
};

export function PipelineTimeline({ data }: { data: PipelineData | null }) {
  const [showHistory, setShowHistory] = useState(false);
  const [history, setHistory] = useState<PipelineHistoryDay[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  if (!data) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium">Pipeline</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-32 animate-pulse rounded bg-muted" />
        </CardContent>
      </Card>
    );
  }

  const steps = deriveSteps(data);
  const completedCount = steps.filter((s) => s.status === "success").length;
  const failedCount = steps.filter((s) => s.status === "failed").length;

  const loadHistory = async () => {
    if (history) {
      setShowHistory(!showHistory);
      return;
    }
    setHistoryLoading(true);
    try {
      const res = await fetchAPI<{ days: PipelineHistoryDay[] }>("/api/pipeline/history?days=5");
      setHistory(res.days);
      setShowHistory(true);
    } catch (e) {
      console.error("Failed to load pipeline history:", e);
    } finally {
      setHistoryLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium">
            Daily Pipeline
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              {completedCount}/{steps.length} completed
              {failedCount > 0 && (
                <span className="ml-1 text-loss">{failedCount} failed</span>
              )}
            </span>
          </CardTitle>
          <button
            onClick={loadHistory}
            disabled={historyLoading}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            {historyLoading ? "Loading..." : showHistory ? "Hide history" : "Past 5 days"}
          </button>
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="space-y-0">
          {steps.map((step, i) => {
            const styles = STATUS_STYLES[step.status];
            const isLast = i === steps.length - 1;

            return (
              <div key={step.job_id} className="flex gap-3">
                {/* Time column */}
                <div className="w-16 shrink-0 pt-0.5 text-right">
                  <span className="text-[11px] tabular-nums text-muted-foreground">
                    {formatTime(step.time)}
                  </span>
                </div>

                {/* Dot + line column */}
                <div className="flex flex-col items-center">
                  <div
                    className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full border-2 ${styles.dot}`}
                  />
                  {!isLast && (
                    <div className={`w-0.5 flex-1 min-h-4 ${styles.line}`} />
                  )}
                </div>

                {/* Content column */}
                <div className={`flex-1 pb-3 ${isLast ? "" : ""}`}>
                  <div className="flex items-center gap-2">
                    <span className={`text-sm font-medium leading-tight ${styles.text}`}>
                      {step.label}
                    </span>
                    <Badge className={`text-[10px] px-1.5 py-0 ${CATEGORY_COLORS[step.category] || CATEGORY_COLORS.system}`}>
                      {step.category}
                    </Badge>
                    {step.status === "running" && (
                      <Badge className="bg-blue-500/20 text-blue-400 text-[10px] px-1.5 py-0 animate-pulse">
                        running
                      </Badge>
                    )}
                    {step.status === "failed" && (
                      <Badge className="bg-loss/20 text-loss text-[10px] px-1.5 py-0">
                        failed
                      </Badge>
                    )}
                  </div>
                  {step.execution && step.status !== "upcoming" && (
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      {step.execution.duration_seconds != null && (
                        <span>{formatDuration(step.execution.duration_seconds)}</span>
                      )}
                      {step.execution.result_summary && (
                        <span className="ml-1.5">
                          — {step.execution.result_summary}
                        </span>
                      )}
                      {step.execution.error && (
                        <span className="ml-1.5 text-loss" title={step.execution.error}>
                          — {step.execution.error.slice(0, 80)}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* History section */}
        {showHistory && history && (
          <div className="mt-4 border-t border-border pt-3">
            <p className="mb-2 text-xs font-medium text-muted-foreground">Recent Pipeline History</p>
            <div className="space-y-3">
              {history.map((day) => (
                <div key={day.date}>
                  <p className="text-xs font-medium text-muted-foreground mb-1">{day.date}</p>
                  <div className="grid grid-cols-1 gap-1">
                    {day.executions.map((exec) => (
                      <div
                        key={exec.id}
                        className="flex items-center gap-2 text-xs"
                      >
                        <span
                          className={`h-1.5 w-1.5 rounded-full shrink-0 ${
                            exec.status === "success"
                              ? "bg-profit"
                              : exec.status === "failed"
                              ? "bg-loss"
                              : exec.status === "running"
                              ? "bg-blue-500"
                              : "bg-muted-foreground"
                          }`}
                        />
                        <span className="text-muted-foreground w-28 shrink-0 truncate">
                          {exec.label}
                        </span>
                        <span className="tabular-nums text-muted-foreground">
                          {exec.duration_seconds != null
                            ? formatDuration(exec.duration_seconds)
                            : "-"}
                        </span>
                        {exec.result_summary && (
                          <span className="text-muted-foreground truncate">
                            {exec.result_summary}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
