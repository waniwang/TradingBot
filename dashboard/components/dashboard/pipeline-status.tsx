"use client";

import Link from "next/link";
import { CheckCircle2, AlertTriangle, Loader2, ArrowRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import type { PipelineData } from "@/lib/types";
import { deriveSteps } from "./pipeline-timeline";

export function PipelineStatus({ data }: { data: PipelineData | null }) {
  if (!data) {
    return (
      <Card>
        <CardContent className="py-3 px-4">
          <div className="h-5 w-48 animate-pulse rounded bg-muted" />
        </CardContent>
      </Card>
    );
  }

  const steps = deriveSteps(data);
  const total = steps.length;
  const completed = steps.filter((s) => s.status === "success").length;
  const running = steps.filter((s) => s.status === "running").length;
  const failed = steps.filter((s) => s.status === "failed").length;

  const allDone = completed === total;
  const hasIssues = failed > 0;

  return (
    <Link href="/pipeline">
      <Card className="transition-colors hover:bg-card/80">
        <CardContent className="flex items-center gap-3 py-3 px-4">
          {hasIssues ? (
            <AlertTriangle className="h-4 w-4 shrink-0 text-loss" />
          ) : allDone ? (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-profit" />
          ) : running > 0 ? (
            <Loader2 className="h-4 w-4 shrink-0 text-blue-400 animate-spin" />
          ) : (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}

          <div className="flex items-center gap-2 text-sm">
            <span className="font-medium tabular-nums">
              {completed}/{total} jobs
            </span>

            {running > 0 && (
              <span className="text-blue-400 text-xs">
                {running} running
              </span>
            )}
            {failed > 0 && (
              <span className="text-loss text-xs">
                {failed} failed
              </span>
            )}

            {data.next_job && (
              <span className="text-xs text-muted-foreground">
                · next: {data.next_job.label}
              </span>
            )}
          </div>

          <ArrowRight className="ml-auto h-3.5 w-3.5 text-muted-foreground" />
        </CardContent>
      </Card>
    </Link>
  );
}
