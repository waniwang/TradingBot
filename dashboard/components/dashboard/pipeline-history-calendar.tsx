"use client";

import type { PipelineDayHistory } from "@/lib/types";

const SUMMARY_COLORS: Record<string, string> = {
  all_passed: "bg-profit/20 border-profit/30 hover:bg-profit/30",
  some_issues: "bg-yellow-500/20 border-yellow-500/30 hover:bg-yellow-500/30",
  failures: "bg-loss/20 border-loss/30 hover:bg-loss/30",
  in_progress: "bg-blue-500/20 border-blue-500/30 hover:bg-blue-500/30",
  no_data: "bg-muted border-border hover:bg-muted/80",
};

function formatShortDate(dateStr: string): { weekday: string; day: string } {
  const d = new Date(dateStr + "T12:00:00"); // noon to avoid timezone shift
  const weekday = d.toLocaleDateString("en-US", { weekday: "short" });
  const day = `${d.getMonth() + 1}/${d.getDate()}`;
  return { weekday, day };
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

  // Show days in chronological order (oldest first)
  const sorted = [...days].sort((a, b) => a.date.localeCompare(b.date));

  return (
    <div className="grid grid-cols-7 gap-1.5 md:[grid-template-columns:repeat(14,minmax(0,1fr))]">
      {sorted.map((day) => {
        const { weekday, day: shortDay } = formatShortDate(day.date);
        const isSelected = selectedDate === day.date;
        const colorClass = SUMMARY_COLORS[day.summary] || SUMMARY_COLORS.no_data;
        const successCount = day.jobs.filter((j) => j.status === "success").length;
        const failedCount = day.jobs.filter((j) => j.status === "failed").length;
        const total = day.jobs.length;

        return (
          <button
            key={day.date}
            onClick={() => onSelectDate(day.date)}
            className={`rounded-md border px-1.5 py-2 text-center transition-all ${colorClass} ${
              isSelected ? "ring-2 ring-ring ring-offset-1 ring-offset-background" : ""
            }`}
            title={`${day.date}: ${successCount}/${total} passed${failedCount > 0 ? `, ${failedCount} failed` : ""}`}
          >
            <div className="text-[9px] font-medium text-muted-foreground">{weekday}</div>
            <div className="text-[11px] font-semibold tabular-nums">{shortDay}</div>
          </button>
        );
      })}
    </div>
  );
}
