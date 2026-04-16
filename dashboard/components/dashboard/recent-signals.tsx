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
            <TableHead>Acted</TableHead>
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
                {s.acted ? (
                  <Badge className="bg-profit/20 text-profit text-xs">Yes</Badge>
                ) : (
                  <span className="text-xs text-muted-foreground">No</span>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
