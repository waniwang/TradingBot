"use client";

import { cn } from "@/lib/utils";
import type { ParamPhase, PhaseLabel } from "@/lib/types";

/**
 * Small phase chip shown next to each parameter row on the Strategies page.
 *
 * Tells the user when a parameter is actually consulted — at scan time
 * (pre-filter), at execute time (order placement), or during the day-2
 * confirmation check for Strategy C.
 */

const STYLES: Record<ParamPhase, string> = {
  scan: "bg-blue-500/10 text-blue-400",
  execute: "bg-profit/10 text-profit",
  day2_confirm: "bg-violet-500/10 text-violet-400",
};

const FALLBACK_LABELS: Record<ParamPhase, PhaseLabel> = {
  scan: { short: "scan", long: "Scan", description: "Used at scan time." },
  execute: { short: "execute", long: "Execute", description: "Used at execution time." },
  day2_confirm: {
    short: "day-2",
    long: "Day-2 Confirm",
    description: "Used during the Strategy C day-2 check.",
  },
};

export function PhaseBadge({
  phase,
  labels,
  className,
}: {
  phase: ParamPhase;
  labels?: Record<ParamPhase, PhaseLabel>;
  className?: string;
}) {
  const label = labels?.[phase] ?? FALLBACK_LABELS[phase];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0 text-[10px] font-medium",
        STYLES[phase],
        className,
      )}
      title={label.description}
    >
      {label.short}
    </span>
  );
}
