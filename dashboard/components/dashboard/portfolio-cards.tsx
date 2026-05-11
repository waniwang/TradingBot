"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Portfolio } from "@/lib/types";

export function PortfolioCards({ data }: { data: Portfolio | null }) {
  if (!data) return <PortfolioSkeleton />;

  const unrealizedColor = data.daily_unrealized >= 0 ? "text-profit" : "text-loss";
  const ytdColor = data.ytd_realized >= 0 ? "text-profit" : "text-loss";

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {/* Combined Portfolio Value + Cash */}
      <Card>
        <CardHeader className="pb-1">
          <CardTitle className="text-[11px] font-medium text-muted-foreground">
            Portfolio Value
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-lg font-bold tabular-nums">
            {formatCurrency(data.portfolio_value)}
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground tabular-nums">
            Cash {formatCurrency(data.cash)}
          </p>
        </CardContent>
      </Card>

      {/* Unrealized P&L — R is primary, $ is secondary */}
      <Card>
        <CardHeader className="pb-1">
          <CardTitle className="text-[11px] font-medium text-muted-foreground">
            Unrealized P&L
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className={`text-lg font-bold tabular-nums ${unrealizedColor}`}>
            {formatR(data.unrealized_total_r)}
          </div>
          <p className={`mt-0.5 text-[11px] tabular-nums ${unrealizedColor}`}>
            {formatCurrency(data.daily_unrealized, true)} ·{" "}
            {data.unrealized_pnl_pct >= 0 ? "+" : ""}
            {data.unrealized_pnl_pct.toFixed(2)}%
          </p>
        </CardContent>
      </Card>

      {/* YTD Realized — R is primary, $ is secondary */}
      <Card>
        <CardHeader className="pb-1">
          <CardTitle className="text-[11px] font-medium text-muted-foreground">
            YTD Realized
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className={`text-lg font-bold tabular-nums ${ytdColor}`}>
            {formatR(data.ytd_total_r)}
          </div>
          <p className={`mt-0.5 text-[11px] tabular-nums ${ytdColor}`}>
            {formatCurrency(data.ytd_realized, true)} ·{" "}
            {data.ytd_realized_pct >= 0 ? "+" : ""}
            {data.ytd_realized_pct.toFixed(2)}%
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-1">
          <CardTitle className="text-[11px] font-medium text-muted-foreground">
            Open Positions
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-lg font-bold tabular-nums">{data.open_positions}</div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {data.max_positions === null ? "no cap" : `max ${data.max_positions}`}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-1">
          <CardTitle className="text-[11px] font-medium text-muted-foreground">
            Trades Today
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-lg font-bold tabular-nums">{data.trades_today}</div>
        </CardContent>
      </Card>
    </div>
  );
}

function PortfolioSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {Array.from({ length: 5 }).map((_, i) => (
        <Card key={i}>
          <CardHeader className="pb-1">
            <div className="h-3 w-20 animate-pulse rounded bg-muted" />
          </CardHeader>
          <CardContent>
            <div className="h-5 w-24 animate-pulse rounded bg-muted" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function formatCurrency(value: number, showSign = false): string {
  const formatted = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(Math.abs(value));
  if (showSign) {
    return value >= 0 ? `+${formatted}` : `-${formatted}`;
  }
  return formatted;
}

function formatR(r: number): string {
  return `${r >= 0 ? "+" : ""}${r.toFixed(2)}R`;
}
