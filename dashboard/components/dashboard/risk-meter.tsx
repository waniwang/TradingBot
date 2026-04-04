"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { RiskData } from "@/lib/types";

function ProgressBar({
  label,
  value,
  limit,
  formatValue,
}: {
  label: string;
  value: number;
  limit: number;
  formatValue: string;
}) {
  // limit is negative (e.g. -3%), value can be positive or negative
  // ratio: how far toward the limit (0 = no loss, 1 = at limit)
  const ratio = limit !== 0 ? Math.min(1, Math.max(0, value / limit)) : 0;
  const pct = Math.round(ratio * 100);

  // Color transitions: green (0-50%) → amber (50-80%) → red (80-100%)
  let barColor = "bg-profit";
  if (ratio > 0.8) barColor = "bg-loss";
  else if (ratio > 0.5) barColor = "bg-yellow-500";

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {formatValue} / {limit}%
        </span>
      </div>
      <div className="h-2 w-full rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function RiskMeter({ data }: { data: RiskData | null }) {
  if (!data) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-medium text-muted-foreground">
          Risk Exposure
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <ProgressBar
          label="Daily Loss"
          value={data.daily_pnl}
          limit={data.daily_limit_pct}
          formatValue={`$${data.daily_pnl.toFixed(0)}`}
        />
        <ProgressBar
          label="Weekly Loss"
          value={data.weekly_pnl}
          limit={data.weekly_limit_pct}
          formatValue={`$${data.weekly_pnl.toFixed(0)}`}
        />
        <div className="flex items-center justify-between pt-1">
          <span className="text-xs text-muted-foreground">Positions</span>
          <span className="text-xs tabular-nums text-muted-foreground">
            {data.open_positions} / {data.max_positions}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
