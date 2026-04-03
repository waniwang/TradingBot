"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { fetchAPI } from "@/lib/api";
import type { BotStatus } from "@/lib/types";

export function Header({
  status,
  onRefresh,
  loading,
}: {
  status: BotStatus | null;
  onRefresh: () => void;
  loading: boolean;
}) {
  const env = status?.environment || "paper";
  const running = status?.running ?? false;

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-border bg-background/80 px-6 backdrop-blur-sm">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span
            className={`h-2 w-2 rounded-full ${
              running ? "bg-profit animate-pulse" : "bg-loss"
            }`}
          />
          <span className="text-sm font-medium">
            {running ? "Running" : "Stopped"}
          </span>
        </div>

        <Badge
          variant={env === "paper" ? "outline" : "destructive"}
          className={
            env === "paper"
              ? "border-yellow-500/50 text-yellow-500"
              : "border-red-500/50 text-red-500"
          }
        >
          {env.toUpperCase()}
        </Badge>

        {status?.phase_label && (
          <span className="text-sm text-muted-foreground">
            {status.phase_label}
            {status.phase_description && (
              <span className="ml-1 text-xs">
                — {status.phase_description}
              </span>
            )}
          </span>
        )}
      </div>

      <div className="flex items-center gap-4">
        {status?.next_job_label && (
          <span className="text-xs text-muted-foreground">
            Next: {status.next_job_label}
            {status.countdown_seconds != null && status.countdown_seconds > 0 && (
              <span className="ml-1">
                ({formatCountdown(status.countdown_seconds)})
              </span>
            )}
          </span>
        )}

        <button
          onClick={onRefresh}
          disabled={loading}
          className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
        >
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>
    </header>
  );
}

function formatCountdown(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${seconds}s`;
}
