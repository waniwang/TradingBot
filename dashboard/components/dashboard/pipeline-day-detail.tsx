"use client";

import type { PipelineDayHistory, MergedPipelineJob, SelectedPipelineJob } from "@/lib/types";
import { STATUS_STYLES, formatDuration, formatTime } from "./pipeline-timeline";
import type { StepStatus } from "./pipeline-timeline";
import {
  PHASE_ORDER,
  PHASE_LABELS,
  getStatusTextClass,
  getStatusLabel,
} from "@/lib/pipeline-constants";

export function PipelineDayDetail({
  day,
  onSelectJob,
}: {
  day: PipelineDayHistory | null;
  onSelectJob?: (job: SelectedPipelineJob) => void;
}) {
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
                    })
                  }
                >
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full border-2 ${styles.dot}`}
                  />
                  <span className={`w-44 shrink-0 truncate font-medium ${styles.text}`}>
                    {job.label}
                  </span>
                  <span className={`text-[10px] font-medium w-16 shrink-0 ${getStatusTextClass(status)}`}>
                    {getStatusLabel(status, job.failure_reason)}
                  </span>
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
