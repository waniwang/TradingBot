"use client";

import type { PipelineDayHistory, MergedPipelineJob, SelectedPipelineJob } from "@/lib/types";
import { STATUS_STYLES, formatDuration } from "./pipeline-timeline";
import type { StepStatus } from "./pipeline-timeline";
import {
  PHASE_ORDER,
  PHASE_LABELS,
} from "@/lib/pipeline-constants";

export function PipelineDayDetail({
  day,
  onSelectJob,
  strategyFilter = "all",
  disabledStrategySlugs,
}: {
  day: PipelineDayHistory | null;
  onSelectJob?: (job: SelectedPipelineJob) => void;
  strategyFilter?: string;
  disabledStrategySlugs?: Set<string>;
}) {
  if (!day) return null;

  // Apply same filter logic as timeline
  const filteredJobs = day.jobs.filter((job) => {
    const strat = job.strategy ?? null;
    if (strategyFilter === "all") {
      return strat === null || !disabledStrategySlugs?.has(strat);
    }
    if (strategyFilter === "shared") return strat === null;
    return strat === strategyFilter;
  });

  const successCount = filteredJobs.filter((j) => j.status === "success").length;
  const failedCount = filteredJobs.filter((j) => j.status === "failed").length;
  const missedCount = filteredJobs.filter((j) => j.status === "missed").length;
  const total = filteredJobs.length;

  // Group jobs by phase
  const grouped: { phase: string; jobs: MergedPipelineJob[] }[] = [];
  for (const phase of PHASE_ORDER) {
    const phaseJobs = filteredJobs.filter((j) => j.phase === phase);
    if (phaseJobs.length > 0) {
      grouped.push({ phase, jobs: phaseJobs });
    }
  }

  return (
    <div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
        <span className="tabular-nums">{total} jobs</span>
        {successCount > 0 && (
          <span className="text-profit tabular-nums">• {successCount} passed</span>
        )}
        {failedCount > 0 && (
          <span className="text-loss tabular-nums">• {failedCount} failed</span>
        )}
        {missedCount > 0 && (
          <span className="text-yellow-500 tabular-nums">• {missedCount} missed</span>
        )}
      </div>

      <div className="space-y-0">
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
                <div
                  key={job.job_id}
                  className="flex items-center gap-2.5 py-1.5 text-sm cursor-pointer rounded-md transition-colors hover:bg-muted/50 -mx-1.5 px-1.5"
                  onClick={() =>
                    onSelectJob?.({
                      job_id: job.job_id,
                      label: job.label,
                      status: job.status,
                      failure_reason: job.failure_reason ?? null,
                      started_at: job.started_at ?? null,
                      finished_at: job.finished_at ?? null,
                      duration_seconds: job.duration_seconds ?? null,
                      result_summary: job.result_summary ?? null,
                      error: job.error ?? null,
                      category: job.category,
                      phase: job.phase,
                      description: job.description,
                      scheduled_time: job.scheduled_time,
                      date: day.date,
                      strategy: job.strategy,
                    })
                  }
                >
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full border-2 ${styles.dot}`}
                  />
                  <span className={`flex-1 truncate font-medium ${styles.text}`}>
                    {job.label}
                  </span>
                  <span className="w-16 shrink-0 text-xs tabular-nums text-muted-foreground text-right">
                    {job.started_at
                      ? new Date(job.started_at).toLocaleTimeString("en-US", {
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : "-"}
                  </span>
                  <span className="w-14 shrink-0 text-xs tabular-nums text-muted-foreground text-right">
                    {job.duration_seconds != null ? formatDuration(job.duration_seconds) : "-"}
                  </span>
                  {job.error && (
                    <span className="truncate text-xs text-loss max-w-48" title={job.error}>
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
