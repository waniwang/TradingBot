"use client";

import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { StrategyInfo } from "@/lib/types";
import { getStatusTextClass } from "@/lib/pipeline-constants";

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function StrategyCard({ strategy }: { strategy: StrategyInfo }) {
  const { stats, last_run } = strategy;

  return (
    <Link href={`/strategies/${strategy.slug}`}>
      <Card className="cursor-pointer transition-all hover:ring-1 hover:ring-foreground/20">
        <CardContent className="p-4">
          {/* Header row */}
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold">{strategy.display_name}</h3>
            <Badge
              className={
                strategy.enabled
                  ? "bg-profit/15 text-profit text-[10px]"
                  : "bg-muted text-muted-foreground text-[10px]"
              }
            >
              {strategy.enabled ? "active" : "disabled"}
            </Badge>
          </div>

          {/* Description */}
          <p className="text-xs text-muted-foreground mb-3 line-clamp-2">
            {strategy.description}
          </p>

          {/* Stats row */}
          <div className="grid grid-cols-4 gap-2 mb-3">
            <div>
              <p className="text-[10px] text-muted-foreground">Win Rate</p>
              <p className="text-sm font-bold tabular-nums">{stats.win_rate}%</p>
            </div>
            <div>
              <p className="text-[10px] text-muted-foreground">Trades</p>
              <p className="text-sm font-bold tabular-nums">{stats.total_closed}</p>
            </div>
            <div>
              <p className="text-[10px] text-muted-foreground">P&L</p>
              <p
                className={`text-sm font-bold tabular-nums ${
                  stats.total_pnl >= 0 ? "text-profit" : "text-loss"
                }`}
              >
                ${stats.total_pnl.toFixed(2)}
              </p>
            </div>
            <div>
              <p className="text-[10px] text-muted-foreground">Open</p>
              <p className="text-sm font-bold tabular-nums">{stats.open_positions}</p>
            </div>
          </div>

          {/* Last run */}
          {last_run ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className={`font-medium ${getStatusTextClass(last_run.status)}`}>
                {last_run.label}
              </span>
              {last_run.result_summary && (
                <>
                  <span>&mdash;</span>
                  <span className="truncate">{last_run.result_summary}</span>
                </>
              )}
              {last_run.ran_at && (
                <span className="ml-auto shrink-0">{timeAgo(last_run.ran_at)}</span>
              )}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No runs yet</p>
          )}
        </CardContent>
      </Card>
    </Link>
  );
}
