"use client";

import { useEffect, useState, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { fetchAPI } from "@/lib/api";
import { useAutoRefresh } from "@/lib/hooks";
import { formatRelativeTime, stageLabel, stageTooltip } from "@/lib/utils";
import { VariationBadge } from "@/components/strategies/variation-badge";
import type { BotStatus, WatchlistData, WatchlistCandidate } from "@/lib/types";

export default function WatchlistPage() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [data, setData] = useState<WatchlistData | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, w] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<WatchlistData>("/api/watchlist"),
      ]);
      setStatus(s);
      setData(w);
      setLastUpdated(new Date());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  const { paused, setPaused } = useAutoRefresh(refresh, 60_000, 300_000);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="flex min-h-screen flex-col">
      <Header
        status={status}
        onRefresh={refresh}
        loading={loading}
        lastUpdated={lastUpdated}
        error={error}
        autoRefreshPaused={paused}
        onToggleAutoRefresh={() => setPaused(!paused)}
      />
      <main className="flex-1 p-6">
        <h2 className="mb-4 text-lg font-semibold">Watchlist Pipeline</h2>

        {!data ? (
          <div className="h-48 animate-pulse rounded-lg bg-muted" />
        ) : (
          <Tabs defaultValue="active">
            <TabsList>
              <TabsTrigger value="active" className="gap-2" title={stageTooltip("active")}>
                {stageLabel("active")}
                <Badge variant="secondary" className="text-xs">{data.counts.active}</Badge>
              </TabsTrigger>
              <TabsTrigger value="ready" className="gap-2" title={stageTooltip("ready")}>
                {stageLabel("ready")}
                <Badge variant="secondary" className="text-xs">{data.counts.ready}</Badge>
              </TabsTrigger>
              <TabsTrigger value="watching" className="gap-2" title={stageTooltip("watching")}>
                {stageLabel("watching")}
                <Badge variant="secondary" className="text-xs">{data.counts.watching}</Badge>
              </TabsTrigger>
              <TabsTrigger value="filled" className="gap-2" title={stageTooltip("filled")}>
                {stageLabel("filled")}
                <Badge variant="secondary" className="text-xs">{data.counts.filled}</Badge>
              </TabsTrigger>
              <TabsTrigger value="cancelled" className="gap-2" title={stageTooltip("cancelled")}>
                {stageLabel("cancelled")}
                <Badge variant="secondary" className="text-xs">{data.counts.cancelled}</Badge>
              </TabsTrigger>
              <TabsTrigger value="expired" className="gap-2" title={stageTooltip("expired")}>
                {stageLabel("expired")}
                <Badge variant="secondary" className="text-xs">{data.counts.expired}</Badge>
              </TabsTrigger>
            </TabsList>

            <TabsContent value="active" className="mt-4">
              <CandidateTable candidates={data.active} />
            </TabsContent>
            <TabsContent value="ready" className="mt-4">
              <CandidateTable candidates={data.ready} />
            </TabsContent>
            <TabsContent value="watching" className="mt-4">
              <CandidateTable candidates={data.watching} />
            </TabsContent>
            <TabsContent value="filled" className="mt-4">
              <CandidateTable candidates={data.filled} />
            </TabsContent>
            <TabsContent value="cancelled" className="mt-4">
              <CandidateTable candidates={data.cancelled} />
            </TabsContent>
            <TabsContent value="expired" className="mt-4">
              <CandidateTable candidates={data.expired} />
            </TabsContent>
          </Tabs>
        )}
      </main>
    </div>
  );
}

function CandidateTable({ candidates }: { candidates: WatchlistCandidate[] }) {
  if (candidates.length === 0) {
    return (
      <div className="rounded-lg border border-border p-8 text-center text-sm text-muted-foreground">
        No tickers in this stage
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
            <TableHead>Stage</TableHead>
            <TableHead>Added</TableHead>
            <TableHead>Stage Changed</TableHead>
            <TableHead className="text-right">Gap %</TableHead>
            <TableHead className="text-right">RVOL</TableHead>
            <TableHead className="text-right">Consol Days</TableHead>
            <TableHead className="text-right">ATR Ratio</TableHead>
            <TableHead className="text-right">RS Score</TableHead>
            <TableHead>Quality</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {candidates.map((c) => (
            <TableRow key={c.id}>
              <TableCell className="font-medium">{c.ticker}</TableCell>
              <TableCell>
                <div className="flex items-center gap-1.5">
                  <Badge variant="outline" className="text-xs">{c.setup}</Badge>
                  <VariationBadge value={c.variation} />
                </div>
              </TableCell>
              <TableCell>
                <Badge
                  title={stageTooltip(c.stage)}
                  className={`text-xs ${
                    c.stage === "ACTIVE"
                      ? "bg-profit/20 text-profit"
                      : c.stage === "READY"
                      ? "bg-blue-500/20 text-blue-400"
                      : "bg-yellow-500/20 text-yellow-400"
                  }`}
                >
                  {stageLabel(c.stage)}
                </Badge>
              </TableCell>
              <TimestampCell iso={c.added_at} />
              <TimestampCell iso={c.stage_changed_at} />
              <TableCell className="text-right tabular-nums">
                {c.gap_pct != null ? `${c.gap_pct}%` : "-"}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {c.pre_mkt_rvol != null ? `${c.pre_mkt_rvol}x` : "-"}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {c.consolidation_days ?? "-"}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {c.atr_ratio ?? "-"}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {c.rs_score ?? "-"}
              </TableCell>
              <TableCell>
                {c.quality_flags.length > 0 ? (
                  <div className="flex flex-wrap gap-1">
                    {c.quality_flags.map((f) => (
                      <Badge key={f} variant="secondary" className="text-[10px]">
                        {f}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">-</span>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function TimestampCell({ iso }: { iso: string | null }) {
  if (!iso) {
    return <TableCell className="text-xs text-muted-foreground">-</TableCell>;
  }
  const relative = formatRelativeTime(iso);
  const absolute = new Date(iso).toLocaleString();
  return (
    <TableCell className="whitespace-nowrap text-xs text-muted-foreground" title={absolute}>
      {relative}
    </TableCell>
  );
}
