"use client";

import { useEffect, useState } from "react";
import { Header } from "@/components/layout/header";
import { PositionsTable } from "@/components/dashboard/positions-table";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { fetchAPI } from "@/lib/api";
import type { BotStatus, OpenPosition, ClosedPosition } from "@/lib/types";

export default function PositionsPage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [positions, setPositions] = useState<OpenPosition[]>([]);
  const [closed, setClosed] = useState<ClosedPosition[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      const [s, pos, cl] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<OpenPosition[]>("/api/positions"),
        fetchAPI<ClosedPosition[]>("/api/positions/closed?limit=20"),
      ]);
      setStatus(s);
      setPositions(pos);
      setClosed(cl);
    } catch (e) {
      console.error("Failed to fetch:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  return (
    <div className="flex min-h-screen flex-col">
      <Header status={status} onRefresh={refresh} loading={loading} />
      <main className="flex-1 space-y-6 p-6">
        <section>
          <h2 className="mb-3 text-lg font-semibold">Open Positions</h2>
          <PositionsTable positions={positions} />
        </section>

        <section>
          <h2 className="mb-3 text-lg font-semibold">Recently Closed</h2>
          {closed.length === 0 ? (
            <div className="rounded-lg border border-border p-8 text-center text-sm text-muted-foreground">
              No closed trades yet
            </div>
          ) : (
            <ClosedTable data={closed} />
          )}
        </section>
      </main>
    </div>
  );
}

function ClosedTable({ data }: { data: ClosedPosition[] }) {
  return (
    <div className="rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Date</TableHead>
            <TableHead>Ticker</TableHead>
            <TableHead>Setup</TableHead>
            <TableHead>Side</TableHead>
            <TableHead className="text-right">Entry</TableHead>
            <TableHead className="text-right">Exit</TableHead>
            <TableHead className="text-right">P&L</TableHead>
            <TableHead className="text-right">Days</TableHead>
            <TableHead>Reason</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.map((p) => (
            <TableRow key={p.id}>
              <TableCell className="tabular-nums text-muted-foreground">
                {p.date ? new Date(p.date).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "-"}
              </TableCell>
              <TableCell className="font-medium">{p.ticker}</TableCell>
              <TableCell>
                <Badge variant="outline" className="text-xs">{p.setup}</Badge>
              </TableCell>
              <TableCell>
                <Badge
                  variant={p.side === "LONG" ? "default" : "destructive"}
                  className="text-xs"
                >
                  {p.side}
                </Badge>
              </TableCell>
              <TableCell className="text-right tabular-nums">${p.entry.toFixed(2)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {p.exit != null ? `$${p.exit.toFixed(2)}` : "-"}
              </TableCell>
              <TableCell className={`text-right tabular-nums font-medium ${p.pnl >= 0 ? "text-profit" : "text-loss"}`}>
                {p.pnl >= 0 ? "+" : ""}{p.pnl.toFixed(2)}
              </TableCell>
              <TableCell className="text-right tabular-nums">{p.days}</TableCell>
              <TableCell>
                <Badge variant="secondary" className="text-xs capitalize">
                  {p.reason || "-"}
                </Badge>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
