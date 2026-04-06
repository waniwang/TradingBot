"use client";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import type { SelectedPipelineJob } from "@/lib/types";
import { formatDuration } from "./pipeline-timeline";

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

function statusBadge(status: string, failureReason: string | null) {
  const label =
    failureReason === "timeout"
      ? "timed out"
      : status;

  const color =
    status === "success"
      ? "bg-profit/15 text-profit"
      : status === "failed"
        ? "bg-loss/15 text-loss"
        : status === "missed"
          ? "bg-yellow-500/15 text-yellow-400"
          : status === "running"
            ? "bg-blue-500/15 text-blue-400"
            : "bg-muted text-muted-foreground";

  return (
    <Badge className={`text-[10px] px-1.5 py-0 ${color}`}>{label}</Badge>
  );
}

function formatTimestamp(iso: string | null) {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1">
      <span className="text-xs text-muted-foreground shrink-0">{label}</span>
      <span className="text-xs text-right">{children}</span>
    </div>
  );
}

export function PipelineJobDetailModal({
  job,
  onClose,
}: {
  job: SelectedPipelineJob | null;
  onClose: () => void;
}) {
  if (!job) return null;

  const isTerminal = ["success", "failed"].includes(job.status);
  const hasExecution = job.started_at != null;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <div className="flex items-center gap-2 flex-wrap">
            <DialogTitle>{job.label}</DialogTitle>
            {statusBadge(job.status, job.failure_reason)}
            {job.category && (
              <Badge
                className={`text-[10px] px-1.5 py-0 ${CATEGORY_COLORS[job.category] || CATEGORY_COLORS.system}`}
              >
                {job.category}
              </Badge>
            )}
          </div>
          {job.description && (
            <DialogDescription>{job.description}</DialogDescription>
          )}
        </DialogHeader>

        {/* Details grid */}
        <div className="divide-y divide-border">
          {job.phase && (
            <DetailRow label="Phase">
              {PHASE_LABELS[job.phase] || job.phase}
            </DetailRow>
          )}
          {job.date && (
            <DetailRow label="Date">{job.date}</DetailRow>
          )}
          {job.scheduled_time && (
            <DetailRow label="Scheduled">{job.scheduled_time}</DetailRow>
          )}
          {hasExecution && (
            <>
              <DetailRow label="Started">{formatTimestamp(job.started_at)}</DetailRow>
              <DetailRow label="Finished">{formatTimestamp(job.finished_at)}</DetailRow>
              <DetailRow label="Duration">
                {job.duration_seconds != null
                  ? formatDuration(job.duration_seconds)
                  : "-"}
              </DetailRow>
            </>
          )}
        </div>

        {/* Result summary */}
        {job.result_summary && (
          <div className="rounded-md border border-border bg-muted/30 p-3">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
              Result
            </span>
            <p className="mt-1 text-sm">{job.result_summary}</p>
          </div>
        )}

        {/* Error / timeout explanation */}
        {job.error && (
          <div className="rounded-md border border-loss/20 bg-loss/5 p-3">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-loss">
              {job.failure_reason === "timeout" ? "Timeout" : "Error"}
            </span>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-loss/90">
              {job.error}
            </pre>
          </div>
        )}

        {/* Status messages for non-executed jobs */}
        {!hasExecution && !isTerminal && (
          <p className="text-sm text-muted-foreground">
            {job.status === "upcoming"
              ? "This job has not run yet."
              : job.status === "missed"
                ? "This job was scheduled but did not execute."
                : job.status === "skipped"
                  ? "This job was skipped."
                  : null}
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
