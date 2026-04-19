"use client";

import type { PipelineDayHistory } from "@/lib/types";

// Only green (all passed), red (any failure — including missed), blue (in-progress),
// or neutral (no data / non-trading day). No yellow — missed jobs are failures.
const SUMMARY_COLORS: Record<string, string> = {
  all_passed: "bg-profit/15 border-profit/40 hover:bg-profit/25",
  some_issues: "bg-loss/15 border-loss/40 hover:bg-loss/25",
  failures: "bg-loss/15 border-loss/40 hover:bg-loss/25",
  in_progress: "bg-blue-500/15 border-blue-500/40 hover:bg-blue-500/25",
  no_data: "bg-muted/40 border-border hover:bg-muted/60",
};

const COUNT_COLORS: Record<string, string> = {
  all_passed: "text-profit/90",
  some_issues: "text-loss/90",
  failures: "text-loss/90",
  in_progress: "text-blue-400/90",
  no_data: "text-muted-foreground",
};

function formatShortDate(dateStr: string): { weekday: string; label: string } {
  // Noon to avoid timezone shift from YYYY-MM-DD → UTC parse.
  const d = new Date(dateStr + "T12:00:00");
  const weekday = d.toLocaleDateString("en-US", { weekday: "short" });
  const label = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return { weekday, label };
}

export function PipelineHistoryCalendar({
  days,
  selectedDate,
  onSelectDate,
}: {
  days: PipelineDayHistory[];
  selectedDate: string | null;
  onSelectDate: (date: string) => void;
}) {
  if (!days || days.length === 0) {
    return (
      <div className="rounded-lg border border-border p-6 text-center text-sm text-muted-foreground">
        No history data
      </div>
    );
  }

  // Show days in chronological order (oldest first) so the most recent day sits on the right.
  const sorted = [...days].sort((a, b) => a.date.localeCompare(b.date));

  return (
    <div className="grid grid-cols-7 gap-1.5 md:grid-cols-14">
      {sorted.map((day) => {
        const { weekday, label } = formatShortDate(day.date);
        const isSelected = selectedDate === day.date;
        const colorClass = SUMMARY_COLORS[day.summary] ?? SUMMARY_COLORS.no_data;
        const countClass = COUNT_COLORS[day.summary] ?? COUNT_COLORS.no_data;

        const successCount = day.jobs.filter((j) => j.status === "success").length;
        // Missed is a failure — same severity, same color.
        const failedCount = day.jobs.filter(
          (j) => j.status === "failed" || j.status === "missed",
        ).length;
        const total = day.jobs.length;

        const title =
          total === 0
            ? day.date
            : `${day.date}: ${successCount}/${total} passed${
                failedCount > 0 ? ` · ${failedCount} failed` : ""
              }`;

        return (
          <button
            key={day.date}
            onClick={() => onSelectDate(day.date)}
            className={`flex flex-col items-center justify-center rounded-md border px-1 py-2 text-center transition-all ${colorClass} ${
              isSelected ? "ring-2 ring-ring ring-offset-1 ring-offset-background" : ""
            }`}
            title={title}
          >
            <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              {weekday}
            </div>
            <div className="text-[12px] font-semibold tabular-nums text-foreground/90 leading-none mt-0.5">
              {label}
            </div>
            {total > 0 && (
              <div className={`text-[10px] tabular-nums mt-1 ${countClass}`}>
                {successCount}/{total}
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}
