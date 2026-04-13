"use client";

import type { MergedPipelineJob, SelectedPipelineJob } from "@/lib/types";
import { STATUS_STYLES, formatDuration } from "@/components/dashboard/pipeline-timeline";
import type { StepStatus } from "@/components/dashboard/pipeline-timeline";
import { getStatusTextClass } from "@/lib/pipeline-constants";

export function StrategyJobsTable({
  jobs,
  onSelectJob,
}: {
  jobs: { date: string; job: MergedPipelineJob }[];
  onSelectJob?: (job: SelectedPipelineJob) => void;
}) {
  if (jobs.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        No pipeline jobs recorded for this strategy yet.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-xs text-muted-foreground">
            <th className="pb-2 pr-4 text-left font-medium">Date</th>
            <th className="pb-2 pr-4 text-left font-medium">Job</th>
            <th className="pb-2 pr-4 text-left font-medium">Status</th>
            <th className="pb-2 pr-4 text-right font-medium">Duration</th>
            <th className="pb-2 text-left font-medium">Result</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map(({ date, job }, i) => {
            const status = job.status as StepStatus;
            const styles = STATUS_STYLES[status] || STATUS_STYLES.upcoming;

            return (
              <tr
                key={`${date}-${job.job_id}-${i}`}
                className="border-b border-border/50 cursor-pointer transition-colors hover:bg-muted/50"
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
                    date,
                    strategy: job.strategy,
                  })
                }
              >
                <td className="py-2 pr-4 text-xs tabular-nums text-muted-foreground">
                  {date}
                </td>
                <td className="py-2 pr-4 font-medium">{job.label}</td>
                <td className="py-2 pr-4">
                  <div className="flex items-center gap-1.5">
                    <span className={`h-2 w-2 shrink-0 rounded-full border-2 ${styles.dot}`} />
                    <span className={`text-xs font-medium ${getStatusTextClass(job.status)}`}>
                      {job.status}
                    </span>
                  </div>
                </td>
                <td className="py-2 pr-4 text-right tabular-nums text-xs text-muted-foreground">
                  {job.duration_seconds != null ? formatDuration(job.duration_seconds) : "-"}
                </td>
                <td className="py-2 text-xs text-muted-foreground truncate max-w-64">
                  {job.result_summary || (job.error ? job.error.slice(0, 60) : "-")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
