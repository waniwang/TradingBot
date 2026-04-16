"use client";

import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import type { OpenPosition } from "@/lib/types";
import { VariationBadge } from "@/components/strategies/variation-badge";

export function PositionsTable({ positions }: { positions: OpenPosition[] }) {
  if (positions.length === 0) {
    return (
      <div className="rounded-lg border border-border p-8 text-center text-sm text-muted-foreground">
        No open positions
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Ticker</TableHead>
            <TableHead>Setup</TableHead>
            <TableHead>Side</TableHead>
            <TableHead className="text-right">Shares</TableHead>
            <TableHead className="text-right">Entry</TableHead>
            <TableHead className="text-right">Stop</TableHead>
            <TableHead className="text-right">Current</TableHead>
            <TableHead className="text-right">Gain %</TableHead>
            <TableHead className="text-right">Unrealized</TableHead>
            <TableHead className="text-right">Days</TableHead>
            <TableHead>Partial</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {positions.map((p) => (
            <TableRow key={p.id}>
              <TableCell className="font-medium">
                <div className="flex items-center gap-1.5">
                  {p.ticker}
                  <VariationBadge value={p.variation} />
                </div>
              </TableCell>
              <TableCell>
                <Badge variant="outline" className="text-xs">
                  {p.setup}
                </Badge>
              </TableCell>
              <TableCell>
                <Badge
                  variant={p.side === "LONG" ? "default" : "destructive"}
                  className="text-xs"
                >
                  {p.side}
                </Badge>
              </TableCell>
              <TableCell className="text-right tabular-nums">{p.shares}</TableCell>
              <TableCell className="text-right tabular-nums">${p.entry.toFixed(2)}</TableCell>
              <TableCell className="text-right tabular-nums">${p.stop.toFixed(2)}</TableCell>
              <TableCell className="text-right tabular-nums">${p.current.toFixed(2)}</TableCell>
              <TableCell className={`text-right tabular-nums font-medium ${p.gain_pct >= 0 ? "text-profit" : "text-loss"}`}>
                {p.gain_pct >= 0 ? "+" : ""}{p.gain_pct.toFixed(2)}%
              </TableCell>
              <TableCell className={`text-right tabular-nums font-medium ${p.unrealized_pnl >= 0 ? "text-profit" : "text-loss"}`}>
                {p.unrealized_pnl >= 0 ? "+" : ""}{p.unrealized_pnl.toFixed(2)}
              </TableCell>
              <TableCell className="text-right tabular-nums">{p.days}</TableCell>
              <TableCell>
                {p.partial && (
                  <span className="text-xs text-muted-foreground">Yes</span>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
