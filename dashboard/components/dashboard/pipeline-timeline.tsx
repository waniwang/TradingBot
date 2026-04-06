"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { PipelineData, PipelineExecution, SelectedPipelineJob } from "@/lib/types";
import {
  CATEGORY_COLORS,
  getStatusTextClass,
} from "@/lib/pipeline-constants";

export type StepStatus = "success" | "running" | "failed" | "skipped" | "upcoming" | "missed";

export interface TimelineStep {
  job_id: string;
  label: string;
  time: string;
  category: string;
  phase: string;
  description: string;
  status: StepStatus;
  failure_reason: string | null;
  execution: PipelineExecution | null;
  isNext: boolean;
  displayMinutes: number;
}

export function deriveSteps(data: PipelineData): TimelineStep[] {
  const execMap = new Map<string, PipelineExecution>();
  for (const exec of data.executions) {
    execMap.set(exec.job_id, exec);
  }

  // Sort: display_day_offset=1 first (overnight), then by time
  const sorted = [...data.schedule].sort((a, b) => {
    if (a.display_day_offset !== b.display_day_offset) {
      return b.display_day_offset - a.display_day_offset; // offset=1 first
    }
    const toMin = (t: string) => {
      const [h, m] = t.split(":").map(Number);
      return h * 60 + m;
    };
    return toMin(a.time) - toMin(b.time);
  });

  const now = new Date();
  const etOffset = getETOffset();
  const etNow = new Date(now.getTime() + etOffset);
  const nowMinutes = etNow.getHours() * 60 + etNow.getMinutes();

  return sorted.map((job) => {
    const exec = execMap.get(job.job_id) || null;
    let status: StepStatus;
    let failure_reason: string | null = null;

    if (exec) {
      if (exec.failure_reason === "timeout") {
        status = "failed";
        failure_reason = "timeout";
      } else {
        status = exec.status as StepStatus;
      }
    } else {
      // No execution row — check if job time has passed
      if (job.display_day_offset === 1) {
        // Overnight job (ran previous day) — no execution means missed or upcoming
        status = "upcoming";
      } else {
        const [h, m] = job.time.split(":").map(Number);
        const jobMinutes = h * 60 + m;
        status = jobMinutes <= nowMinutes ? "missed" : "upcoming";
      }
    }

    const isNext = data.next_job?.job_id === job.job_id;

    // Display minutes: overnight (offset=1) gets -1 so it sorts to top
    const [h, m] = job.time.split(":").map(Number);
    const displayMinutes = job.display_day_offset === 1 ? -1 : h * 60 + m;

    return {
      ...job,
      status,
      failure_reason,
      execution: exec,
      isNext,
      displayMinutes,
    };
  });
}

export function getETOffset(): number {
  const jan = new Date(new Date().getFullYear(), 0, 1).getTimezoneOffset();
  const jul = new Date(new Date().getFullYear(), 6, 1).getTimezoneOffset();
  const isDST = new Date().getTimezoneOffset() < Math.max(jan, jul);
  const etOffsetHours = isDST ? -4 : -5;
  const localOffsetMinutes = new Date().getTimezoneOffset();
  return (localOffsetMinutes + etOffsetHours * 60) * 60 * 1000;
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function formatTime(time24: string): string {
  const [h, m] = time24.split(":").map(Number);
  const ampm = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 || 12;
  return `${h12}:${m.toString().padStart(2, "0")} ${ampm}`;
}

function formatCountdown(seconds: number): string {
  if (seconds <= 0) return "now";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function getCurrentETMinutes(): number {
  const now = new Date();
  const etOffset = getETOffset();
  const etNow = new Date(now.getTime() + etOffset);
  return etNow.getHours() * 60 + etNow.getMinutes();
}

function formatETTime(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  const ampm = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 || 12;
  return `${h12}:${m.toString().padStart(2, "0")} ${ampm}`;
}

export const STATUS_STYLES: Record<StepStatus, { dot: string; line: string; text: string }> = {
  success: {
    dot: "bg-profit border-profit",
    line: "bg-border",
    text: "text-foreground",
  },
  running: {
    dot: "bg-blue-500 border-blue-500 animate-pulse",
    line: "bg-border",
    text: "text-foreground",
  },
  failed: {
    dot: "bg-loss border-loss",
    line: "bg-border",
    text: "text-foreground",
  },
  skipped: {
    dot: "bg-muted border-muted-foreground/30",
    line: "bg-border",
    text: "text-muted-foreground",
  },
  upcoming: {
    dot: "bg-transparent border-muted-foreground/40",
    line: "bg-border",
    text: "text-muted-foreground",
  },
  missed: {
    dot: "bg-transparent border-yellow-500/60",
    line: "bg-border",
    text: "text-yellow-500",
  },
};

function NowIndicator({ countdownSeconds }: { countdownSeconds?: number | null }) {
  const nowMinutes = getCurrentETMinutes();
  return (
    <div className="flex gap-3 -mx-2 px-2 py-1">
      {/* Time column */}
      <div className="w-16 shrink-0 text-right">
        <span className="text-[10px] font-semibold tabular-nums text-blue-400">
          {formatETTime(nowMinutes)}
        </span>
      </div>
      {/* Line column — aligned with dots */}
      <div className="flex flex-col items-center justify-center">
        <div className="h-1.5 w-1.5 rounded-full bg-blue-400" />
      </div>
      {/* Label */}
      <div className="flex flex-1 items-center">
        <div className="h-px flex-1 bg-blue-400/30" />
        <span className="ml-2 text-[10px] font-medium text-blue-400/70">
          NOW
          {countdownSeconds != null && countdownSeconds > 0 && (
            <span className="ml-1.5 text-muted-foreground font-normal">
              next in {formatCountdown(countdownSeconds)}
            </span>
          )}
        </span>
      </div>
    </div>
  );
}

export function PipelineTimeline({
  data,
  onSelectJob,
}: {
  data: PipelineData | null;
  onSelectJob?: (job: SelectedPipelineJob) => void;
}) {
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

  // Group steps by phase, preserving order
  const phases = data.phase_order || ["overnight", "premarket", "market_open", "afternoon", "close"];
  const phaseMeta = data.phases || {};
  const grouped: { phase: string; steps: TimelineStep[] }[] = [];

  for (const phase of phases) {
    const phaseSteps = steps.filter((s) => s.phase === phase);
    if (phaseSteps.length > 0) {
      grouped.push({ phase, steps: phaseSteps });
    }
  }

  // Determine where to place the "now" indicator
  const nowMinutes = getCurrentETMinutes();
  // Only show on trading days
  const showNowIndicator = data.is_trading_day;

  // Find which phase/step index the now indicator should appear after
  // Returns { phaseIdx, stepIdx } — indicator goes after that step
  // Or { phaseIdx: -1 } if before all steps
  let nowPosition: { phaseIdx: number; stepIdx: number } | null = null;

  if (showNowIndicator) {
    // Walk through all steps in display order and find where "now" falls
    let lastPhaseIdx = -1;
    let lastStepIdx = -1;

    for (let pi = 0; pi < grouped.length; pi++) {
      for (let si = 0; si < grouped[pi].steps.length; si++) {
        const step = grouped[pi].steps[si];
        if (step.displayMinutes === -1) continue; // overnight — always past, skip
        if (step.displayMinutes <= nowMinutes) {
          lastPhaseIdx = pi;
          lastStepIdx = si;
        }
      }
    }

    if (lastPhaseIdx === -1) {
      // Before all regular jobs — place after the overnight phase header
      nowPosition = { phaseIdx: 0, stepIdx: -1 };
    } else {
      nowPosition = { phaseIdx: lastPhaseIdx, stepIdx: lastStepIdx };
    }
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">
          Daily Pipeline
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            {completedCount}/{steps.length} completed
            {failedCount > 0 && (
              <span className="ml-1 text-loss">{failedCount} failed</span>
            )}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="space-y-0">
          {grouped.map((group, phaseIdx) => {
            const meta = phaseMeta[group.phase];
            return (
              <div key={group.phase}>
                {/* Phase header */}
                <div className="flex gap-3 pt-3 pb-1.5 first:pt-0">
                  <div className="w-16 shrink-0" />
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                      {meta?.label || group.phase}
                    </span>
                    {meta?.time_range && (
                      <span className="text-[10px] text-muted-foreground/50">
                        {meta.time_range}
                      </span>
                    )}
                  </div>
                </div>

                {/* Now indicator before first step in this phase */}
                {nowPosition &&
                  nowPosition.phaseIdx === phaseIdx &&
                  nowPosition.stepIdx === -1 && (
                    <NowIndicator countdownSeconds={data.next_job?.countdown_seconds} />
                  )}

                {/* Steps in this phase */}
                {group.steps.map((step, i) => {
                  const styles = STATUS_STYLES[step.status];
                  const isLastInPhase = i === group.steps.length - 1;
                  const isLastOverall =
                    group.phase === grouped[grouped.length - 1].phase && isLastInPhase;

                  return (
                    <div key={step.job_id}>
                      <div
                        className={`flex gap-3 cursor-pointer rounded-md transition-colors hover:bg-muted/50 ${step.isNext ? "bg-blue-500/5 -mx-2 px-2" : "-mx-2 px-2"}`}
                        onClick={() =>
                          onSelectJob?.({
                            job_id: step.job_id,
                            label: step.label,
                            status: step.status,
                            failure_reason: step.failure_reason,
                            started_at: step.execution?.started_at ?? null,
                            finished_at: step.execution?.finished_at ?? null,
                            duration_seconds: step.execution?.duration_seconds ?? null,
                            result_summary: step.execution?.result_summary ?? null,
                            error: step.execution?.error ?? null,
                            category: step.category,
                            phase: step.phase,
                            description: step.description,
                          })
                        }
                      >
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
                          {!isLastOverall && (
                            <div className={`w-0.5 flex-1 min-h-4 ${styles.line}`} />
                          )}
                        </div>

                        {/* Content column */}
                        <div className="flex-1 pb-3">
                          <div className="flex items-center gap-2">
                            <span className={`text-sm font-medium leading-tight ${styles.text}`}>
                              {step.label}
                            </span>
                            {/* Category badge — filled style */}
                            <Badge
                              className={`text-[10px] px-1.5 py-0 ${CATEGORY_COLORS[step.category] || CATEGORY_COLORS.system}`}
                            >
                              {step.category}
                            </Badge>
                            {/* Status — plain text, visually distinct from filled category badges */}
                            {step.isNext && (
                              <span className={`text-[10px] font-medium ${getStatusTextClass("next")}`}>
                                next
                              </span>
                            )}
                            {step.status === "running" && !step.isNext && (
                              <span className={`text-[10px] font-medium ${getStatusTextClass("running")}`}>
                                running
                              </span>
                            )}
                            {step.status === "failed" && (
                              <span className={`text-[10px] font-medium ${getStatusTextClass("failed")}`}>
                                {step.failure_reason === "timeout" ? "timed out" : "failed"}
                              </span>
                            )}
                          </div>
                          <p className="text-[11px] text-muted-foreground/70 mt-0.5 leading-tight">
                            {step.description}
                          </p>
                          {step.execution && step.status !== "upcoming" && (
                            <div className="mt-0.5 text-xs text-muted-foreground">
                              {step.execution.duration_seconds != null && (
                                <span>{formatDuration(step.execution.duration_seconds)}</span>
                              )}
                              {step.execution.result_summary && (
                                <span className="ml-1.5">
                                  &mdash; {step.execution.result_summary}
                                </span>
                              )}
                              {step.execution.error && (
                                <span className="ml-1.5 text-loss" title={step.execution.error}>
                                  &mdash; {step.execution.error.slice(0, 80)}
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Now indicator after this step */}
                      {nowPosition &&
                        nowPosition.phaseIdx === phaseIdx &&
                        nowPosition.stepIdx === i && (
                          <NowIndicator countdownSeconds={data.next_job?.countdown_seconds} />
                        )}
                    </div>
                  );
                })}
              </div>
            );
          })}

          {/* Now indicator after all steps — only if no steps matched (all jobs in the future) */}
        </div>
      </CardContent>
    </Card>
  );
}
