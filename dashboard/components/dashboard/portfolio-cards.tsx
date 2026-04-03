"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Portfolio } from "@/lib/types";

export function PortfolioCards({ data }: { data: Portfolio | null }) {
  if (!data) return <PortfolioSkeleton />;

  const cards = [
    {
      title: "Portfolio Value",
      value: formatCurrency(data.portfolio_value),
      sub: null,
    },
    {
      title: "Cash",
      value: formatCurrency(data.cash),
      sub: null,
    },
    {
      title: "Daily P&L",
      value: formatCurrency(data.daily_pnl, true),
      sub: `${data.daily_pnl_pct >= 0 ? "+" : ""}${data.daily_pnl_pct.toFixed(2)}%`,
      color: data.daily_pnl >= 0 ? "text-profit" : "text-loss",
      subColor: data.daily_pnl_pct >= 0 ? "text-profit" : "text-loss",
    },
    {
      title: "Open Positions",
      value: `${data.open_positions}`,
      sub: `max ${data.max_positions}`,
    },
    {
      title: "Trades Today",
      value: `${data.trades_today}`,
      sub: null,
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      {cards.map((c) => (
        <Card key={c.title}>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-medium text-muted-foreground">
              {c.title}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className={`text-2xl font-bold tabular-nums ${c.color || ""}`}>
              {c.value}
            </div>
            {c.sub && (
              <p className={`mt-1 text-xs ${c.subColor || "text-muted-foreground"}`}>
                {c.sub}
              </p>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function PortfolioSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      {Array.from({ length: 5 }).map((_, i) => (
        <Card key={i}>
          <CardHeader className="pb-2">
            <div className="h-3 w-20 animate-pulse rounded bg-muted" />
          </CardHeader>
          <CardContent>
            <div className="h-7 w-28 animate-pulse rounded bg-muted" />
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
