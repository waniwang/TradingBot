"use client";

import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { VariationBadge } from "@/components/strategies/variation-badge";
import type { TradeAttempt, TradeAttemptOutcome } from "@/lib/types";

/**
 * Unified Trade Attempts table — one row per order the bot tried to place.
 *
 * Replaces the old separate "Signal History" + "Trade History" tables. Every
 * attempt shows up exactly once, and the Outcome column collapses the
 * underlying Signal/Order/Position state combinations into a small set of
 * operator-meaningful labels.
 */
export function TradeAttempts({
  attempts,
  showDate = false,
  emptyMessage = "No attempts yet",
}: {
  attempts: TradeAttempt[];
  /** Show full date in the When column. Use on the History page where rows
   *  span multiple days; a bare HH:MM:SS would be misleading. */
  showDate?: boolean;
  emptyMessage?: string;
}) {
  if (attempts.length === 0) {
    return (
      <div className="rounded-lg border border-border p-6 text-center text-sm text-muted-foreground">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{showDate ? "When" : "Time"}</TableHead>
            <TableHead>Ticker</TableHead>
            <TableHead>Setup</TableHead>
            <TableHead className="text-right">Entry</TableHead>
            <TableHead className="text-right">Exit</TableHead>
            <TableHead className="text-right">P&L</TableHead>
            <TableHead className="text-right">Days</TableHead>
            <TableHead>Outcome</TableHead>
            <TableHead>Detail</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {attempts.map((a) => (
            <TableRow key={a.id}>
              <TableCell className="tabular-nums text-muted-foreground">
                {showDate ? formatDateTime(a.fired_at) : formatTime(a.fired_at)}
              </TableCell>
              <TableCell className="font-medium">
                <div className="flex items-center gap-1.5">
                  {a.ticker}
                  <VariationBadge value={a.variation} />
                </div>
              </TableCell>
              <TableCell>
                <Badge variant="outline" className="text-xs">{a.setup}</Badge>
              </TableCell>
              <TableCell className="text-right tabular-nums">
                <EntryCell intended={a.entry_intended} actual={a.entry_actual} />
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {a.exit != null ? `$${a.exit.toFixed(2)}` : "-"}
              </TableCell>
              <TableCell
                className={`text-right tabular-nums font-medium ${
                  a.pnl == null ? "text-muted-foreground" : a.pnl >= 0 ? "text-profit" : "text-loss"
                }`}
              >
                {a.pnl == null
                  ? "-"
                  : `${a.pnl >= 0 ? "+" : ""}$${a.pnl.toFixed(2)}`}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {a.days != null ? a.days : "-"}
              </TableCell>
              <TableCell>
                <OutcomeBadge outcome={a.outcome} />
              </TableCell>
              <TableCell className="text-xs text-muted-foreground capitalize">
                {a.detail ?? "-"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** Show the actual fill price prominently, the intended limit faintly below.
 *  Drops the "intended" line entirely when they match (rounded to 2dp) to
 *  avoid noise on every row. */
function EntryCell({ intended, actual }: { intended: number; actual: number | null }) {
  if (actual == null) {
    return <span className="text-muted-foreground">${intended.toFixed(2)}</span>;
  }
  const same = Math.abs(actual - intended) < 0.005;
  return (
    <div className="leading-tight">
      <div>${actual.toFixed(2)}</div>
      {!same && (
        <div className="text-[10px] text-muted-foreground/70">
          ord ${intended.toFixed(2)}
        </div>
      )}
    </div>
  );
}

/** Color scheme:
 *  green = good (filled), blue = pending, red = trade failed, grey = passive. */
const OUTCOME_STYLES: Record<TradeAttemptOutcome, { cls: string; label: string; tooltip: string }> = {
  filled_open: {
    cls: "bg-profit/20 text-profit",
    label: "Filled — open",
    tooltip: "Order filled, position is currently open",
  },
  filled_closed: {
    cls: "bg-profit/15 text-profit/80",
    label: "Filled — closed",
    tooltip: "Order filled, position has been closed",
  },
  submitted: {
    cls: "bg-blue-500/20 text-blue-400",
    label: "Submitted",
    tooltip: "Order working at broker",
  },
  did_not_fill: {
    cls: "bg-loss/20 text-loss",
    label: "Did not fill",
    tooltip: "Order placed but limit price never printed; bot cancelled it",
  },
  broker_rejected: {
    cls: "bg-loss/30 text-loss",
    label: "Broker rejected",
    tooltip: "Broker refused the order (margin, bad symbol, halted, etc.)",
  },
};

function OutcomeBadge({ outcome }: { outcome: TradeAttemptOutcome }) {
  const style = OUTCOME_STYLES[outcome];
  return (
    <Badge className={`text-xs ${style.cls}`} title={style.tooltip}>
      {style.label}
    </Badge>
  );
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  const date = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `${date} ${time}`;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}
