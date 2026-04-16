"use client";

import { cn } from "@/lib/utils";

/**
 * Small color-coded chip that shows which EP variation fired the signal:
 * A (tight), B (relaxed), A+B (both), or C (day-2 confirm).
 *
 * Renders nothing when value is null/empty so non-EP strategies stay clean.
 */

const STYLES: Record<string, string> = {
  A: "bg-blue-500/15 text-blue-400 ring-1 ring-blue-500/30",
  B: "bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30",
  C: "bg-violet-500/15 text-violet-400 ring-1 ring-violet-500/30",
  "A+B": "bg-teal-500/15 text-teal-400 ring-1 ring-teal-500/30",
};

export function VariationBadge({
  value,
  className,
}: {
  value: string | null | undefined;
  className?: string;
}) {
  if (!value) return null;
  const style = STYLES[value] ?? "bg-muted text-muted-foreground";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0 text-[10px] font-semibold tabular-nums",
        style,
        className,
      )}
      title={labelFor(value)}
    >
      {value}
    </span>
  );
}

function labelFor(v: string): string {
  switch (v) {
    case "A":
      return "Strategy A (tight filters)";
    case "B":
      return "Strategy B (relaxed filters)";
    case "C":
      return "Strategy C (day-2 confirm)";
    case "A+B":
      return "Passed Strategy A and B filters";
    default:
      return v;
  }
}
