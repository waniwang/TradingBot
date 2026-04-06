"use client";

import { Badge } from "@/components/ui/badge";
import type { PipelineDayHistory, MergedPipelineJob } from "@/lib/types";
import { STATUS_STYLES, formatDuration, formatTime } from "./pipeline-timeline";
import type { StepStatus } from "./pipeline-timeline";

const PHASE_ORDER = ["overnight", "premarket", "market_open", "afternoon", "close"];
const PHASE_LABELS: Record<string, string> = {
  overnight: "Overnight",
  premarket: "Pre-Market",
  market_open: "Market Open",
  afternoon: "Afternoon Swing",
  close: "Close",
};

const CATEGORY_COLORS: Record<string, string> = {
  scan: "bg-blue-500/15 text-blue-400",
  trade: "bg-profit/15 text-profit",
  monitor: "bg-purple-500/15 text-purple-400",
  system: "bg-muted text-muted-foreground",
};

export function PipelineDayDetail({ day }: { day: PipelineDayHistory | null }) {
  if (!day) return null;

  const successCount = day.jobs.filter((j) => j.status === "success").length;
  const failedCount = day.jobs.filter((j) => j.status === "failed").length;
  const missedCount = day.jobs.filter((j) => j.status === "missed").length;
  const total = day.jobs.length;

  // Group jobs by phase
  const grouped: { phase: string; jobs: MergedPipelineJob[] }[] = [];
  for (const phase of PHASE_ORDER) {
    const phaseJobs = day.jobs.filter((j) => j.phase === phase);
    if (phaseJobs.length > 0) {
      grouped.push({ phase, jobs: phaseJobs });
    }
  }

  return (
    <div className="rounded-lg border border-border mt-3">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="text-sm font-medium">{day.date}</span>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="tabular-nums">{total} jobs</span>
          {successCount > 0 && (
            <span className="text-profit tabular-nums">{successCount} passed</span>
          )}
          {failedCount > 0 && (
            <span className="text-loss tabular-nums">{failedCount} failed</span>
          )}
          {missedCount > 0 && (
            <span className="text-yellow-500 tabular-nums">{missedCount} missed</span>
          )}
        </div>
      </div>

      <div className="p-3 space-y-0">
        {grouped.map((group) => (
          <div key={group.phase}>
            <div className="flex items-center gap-2 pt-2 pb-1 first:pt-0">
              <span className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground/60">
                {PHASE_LABELS[group.phase] || group.phase}
              </span>
            </div>
            {group.jobs.map((job) => {
              const status = job.status as StepStatus;
              const styles = STATUS_STYLES[status] || STATUS_STYLES.upcoming;

              return (
                <div key={job.job_id} className="flex items-center gap-2.5 py-1.5 text-sm">
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full border-2 ${styles.dot}`}
                  />
                  <span className={`w-44 shrink-0 truncate font-medium ${styles.text}`}>
                    {job.label}
                  </span>
                  <Badge
                    className={`text-[10px] px-1.5 py-0 ${
                      status === "success"
                        ? "bg-profit/15 text-profit"
                        : status === "failed"
                          ? "bg-loss/15 text-loss"
                          : status === "missed"
                            ? "bg-yellow-500/15 text-yellow-400"
                            : "bg-muted text-muted-foreground"
                    }`}
                  >
                    {job.failure_reason === "timeout" ? "timed out" : status}
                  </Badge>
                  <span className="w-16 shrink-0 text-xs tabular-nums text-muted-foreground">
                    {job.started_at
                      ? new Date(job.started_at).toLocaleTimeString("en-US", {
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : "-"}
                  </span>
                  <span className="w-16 shrink-0 text-xs tabular-nums text-muted-foreground">
                    {job.duration_seconds != null ? formatDuration(job.duration_seconds) : "-"}
                  </span>
                  {job.result_summary && (
                    <span className="truncate text-xs text-muted-foreground">
                      {job.result_summary}
                    </span>
                  )}
                  {job.error && (
                    <span className="truncate text-xs text-loss" title={job.error}>
                      {job.error.slice(0, 60)}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
