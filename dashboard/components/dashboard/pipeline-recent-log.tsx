"use client";

import { Badge } from "@/components/ui/badge";
import type { FlatExecution, SelectedPipelineJob } from "@/lib/types";
import { formatDuration } from "./pipeline-timeline";
import { getStatusBadgeClass, getStatusLabel } from "@/lib/pipeline-constants";

export function PipelineRecentLog({
  executions,
  onSelectJob,
}: {
  executions: FlatExecution[] | undefined;
  onSelectJob?: (job: SelectedPipelineJob) => void;
}) {
  if (!executions || executions.length === 0) {
    return null;
  }

  return (
    <div className="rounded-lg border border-border">
      <div className="border-b border-border px-4 py-2">
        <span className="text-xs font-medium text-muted-foreground">
          Recent Executions ({executions.length})
        </span>
      </div>
      <div className="divide-y divide-border">
        {executions.map((exec, i) => {
          const startedTime = exec.started_at
            ? new Date(exec.started_at).toLocaleTimeString("en-US", {
                hour: "2-digit",
                minute: "2-digit",
              })
            : "-";

          return (
            <div
              key={`${exec.date}-${exec.job_id}-${i}`}
              className="flex items-center gap-2.5 px-4 py-1.5 text-sm cursor-pointer transition-colors hover:bg-muted/50"
              onClick={() =>
                onSelectJob?.({
                  job_id: exec.job_id,
                  label: exec.label,
                  status: exec.status,
                  failure_reason: exec.failure_reason ?? null,
                  started_at: exec.started_at ?? null,
                  finished_at: exec.finished_at ?? null,
                  duration_seconds: exec.duration_seconds ?? null,
                  result_summary: exec.result_summary ?? null,
                  error: exec.error ?? null,
                  date: exec.date,
                })
              }
            >
              <span
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                  exec.status === "success"
                    ? "bg-profit"
                    : exec.status === "failed"
                      ? "bg-loss"
                      : exec.status === "running"
                        ? "bg-blue-500 animate-pulse"
                        : "bg-muted-foreground"
                }`}
              />
              <span className="w-16 shrink-0 text-xs tabular-nums text-muted-foreground">
                {exec.date?.slice(5)}
              </span>
              <span className="w-40 shrink-0 truncate text-xs font-medium">
                {exec.label}
              </span>
              <Badge
                variant="outline"
                className={`text-[10px] px-1.5 py-0 ${getStatusBadgeClass(exec.status)}`}
              >
                {getStatusLabel(exec.status, exec.failure_reason)}
              </Badge>
              <span className="w-16 shrink-0 text-xs tabular-nums text-muted-foreground">
                {startedTime}
              </span>
              <span className="w-16 shrink-0 text-xs tabular-nums text-muted-foreground">
                {exec.duration_seconds != null ? formatDuration(exec.duration_seconds) : "-"}
              </span>
              {exec.result_summary && (
                <span className="truncate text-xs text-muted-foreground">
                  {exec.result_summary}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
