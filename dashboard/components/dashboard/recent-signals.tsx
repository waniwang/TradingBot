"use client";

import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import type { SignalToday } from "@/lib/types";
import { VariationBadge } from "@/components/strategies/variation-badge";

export function RecentSignals({ signals }: { signals: SignalToday[] }) {
  if (signals.length === 0) {
    return (
      <div className="rounded-lg border border-border p-6 text-center text-sm text-muted-foreground">
        No signals fired today
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Time</TableHead>
            <TableHead>Ticker</TableHead>
            <TableHead>Setup</TableHead>
            <TableHead className="text-right">Entry</TableHead>
            <TableHead className="text-right">Stop</TableHead>
            <TableHead className="text-right">Gap %</TableHead>
            <TableHead>Order</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {signals.map((s) => (
            <TableRow key={s.id}>
              <TableCell className="tabular-nums text-muted-foreground">{s.time}</TableCell>
              <TableCell className="font-medium">
                <div className="flex items-center gap-1.5">
                  {s.ticker}
                  <VariationBadge value={s.variation} />
                </div>
              </TableCell>
              <TableCell>
                <Badge variant="outline" className="text-xs">{s.setup}</Badge>
              </TableCell>
              <TableCell className="text-right tabular-nums">${s.entry.toFixed(2)}</TableCell>
              <TableCell className="text-right tabular-nums">${s.stop.toFixed(2)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {s.gap_pct != null ? `${s.gap_pct.toFixed(1)}%` : "-"}
              </TableCell>
              <TableCell>
                <OrderStatusCell signal={s} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** Surfaces fill outcome, not just "did we submit". Today's MCRI case was
 * acted=true + cancelled because the limit never printed — this column now
 * makes that visible without a job-detail dig. */
function OrderStatusCell({ signal }: { signal: SignalToday }) {
  if (!signal.acted) {
    return <span className="text-xs text-muted-foreground">Not acted</span>;
  }
  const status = signal.order_status;
  if (!status) {
    return <Badge className="bg-profit/20 text-profit text-xs">Submitted</Badge>;
  }
  const label = status.replace("_", " ");
  let cls = "bg-muted text-muted-foreground";
  let title = label;
  if (status === "filled") {
    cls = "bg-profit/20 text-profit";
    if (signal.filled_qty != null && signal.filled_avg_price != null) {
      title = `Filled ${signal.filled_qty} @ $${signal.filled_avg_price.toFixed(2)}`;
    }
  } else if (status === "partially_filled") {
    cls = "bg-amber-500/20 text-amber-400";
    if (signal.filled_qty != null && signal.order_qty != null) {
      title = `Partial ${signal.filled_qty}/${signal.order_qty}`;
    }
  } else if (status === "submitted" || status === "pending") {
    cls = "bg-blue-500/20 text-blue-400";
    title = "Working at broker";
  } else if (status === "cancelled") {
    cls = "bg-loss/20 text-loss";
    title = "Submitted but never filled (e.g. limit not reached)";
  } else if (status === "rejected") {
    cls = "bg-loss/20 text-loss";
    title = "Broker rejected the order";
  }
  return (
    <Badge className={`text-xs capitalize ${cls}`} title={title}>
      {label}
    </Badge>
  );
}
