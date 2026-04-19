"use client";

import { cn } from "@/lib/utils";
import type { StrategyInfo } from "@/lib/types";
import { STRATEGY_LABELS } from "@/lib/pipeline-constants";

interface StrategyFilterBarProps {
  value: string;
  onChange: (value: string) => void;
  strategies: StrategyInfo[];
}

export function StrategyFilterBar({ value, onChange, strategies }: StrategyFilterBarProps) {
  // Build pills: All + enabled strategies + System
  const pills: { value: string; label: string }[] = [{ value: "all", label: "All" }];

  for (const s of strategies) {
    if (s.enabled) {
      pills.push({ value: s.slug, label: STRATEGY_LABELS[s.slug] || s.display_name });
    }
  }

  pills.push({ value: "shared", label: "Shared" });

  return (
    <div className="flex gap-1.5 overflow-x-auto pb-1 mb-3">
      {pills.map((pill) => (
        <button
          key={pill.value}
          onClick={() => onChange(pill.value)}
          className={cn(
            "shrink-0 rounded-full px-3 py-1 text-xs font-medium transition-colors",
            value === pill.value
              ? "bg-foreground text-background"
              : "border border-border text-muted-foreground hover:bg-muted/50"
          )}
        >
          {pill.label}
        </button>
      ))}
    </div>
  );
}
