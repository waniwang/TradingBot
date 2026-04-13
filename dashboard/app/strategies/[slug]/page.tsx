"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { StrategyTradesTable } from "@/components/strategies/strategy-trades-table";
import { StrategyJobsTable } from "@/components/strategies/strategy-jobs-table";
import { PipelineJobDetailModal } from "@/components/dashboard/pipeline-job-detail-modal";
import { fetchAPI } from "@/lib/api";
import { useAutoRefresh } from "@/lib/hooks";
import type {
  BotStatus,
  StrategyListResponse,
  StrategyInfo,
  ClosedPosition,
  PipelineHistoryResponse,
  MergedPipelineJob,
  SelectedPipelineJob,
} from "@/lib/types";

export default function StrategyDetailPage() {
  const params = useParams<{ slug: string }>();
  const slug = params.slug;

  const [status, setStatus] = useState<BotStatus | null>(null);
  const [strategy, setStrategy] = useState<StrategyInfo | null>(null);
  const [trades, setTrades] = useState<ClosedPosition[]>([]);
  const [history, setHistory] = useState<PipelineHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<SelectedPipelineJob | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, strats, closed, hist] = await Promise.all([
        fetchAPI<BotStatus>("/api/status"),
        fetchAPI<StrategyListResponse>("/api/strategies"),
        fetchAPI<ClosedPosition[]>(`/api/positions/closed?strategy=${slug}&limit=100`),
        fetchAPI<PipelineHistoryResponse>("/api/pipeline/history?days=30"),
      ]);
      setStatus(s);
      setStrategy(strats.strategies.find((st) => st.slug === slug) ?? null);
      setTrades(closed);
      setHistory(hist);
      setLastUpdated(new Date());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [slug]);

  const { paused, setPaused } = useAutoRefresh(refresh, 60_000, 300_000);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Derive strategy pipeline jobs from history
  const strategyJobs = useMemo(() => {
    if (!history) return [];
    const result: { date: string; job: MergedPipelineJob }[] = [];
    for (const day of history.days) {
      for (const job of day.jobs) {
        if (job.strategy === slug) {
          result.push({ date: day.date, job });
        }
      }
    }
    return result;
  }, [history, slug]);

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
      <main className="flex-1 space-y-6 p-6">
        <div className="flex items-center gap-3">
          <Link
            href="/strategies"
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <h2 className="text-lg font-semibold">
            {strategy?.display_name ?? slug}
          </h2>
          {strategy && (
            <Badge
              className={
                strategy.enabled
                  ? "bg-profit/15 text-profit text-[10px]"
                  : "bg-muted text-muted-foreground text-[10px]"
              }
            >
              {strategy.enabled ? "active" : "disabled"}
            </Badge>
          )}
        </div>

        {!strategy && !loading && (
          <p className="text-sm text-muted-foreground">Strategy not found.</p>
        )}

        {strategy && (
          <Tabs defaultValue="overview">
            <TabsList variant="line">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="trades">
                Trades
                {trades.length > 0 && (
                  <span className="ml-1 text-[10px] tabular-nums text-muted-foreground">
                    {trades.length}
                  </span>
                )}
              </TabsTrigger>
              <TabsTrigger value="pipeline">
                Pipeline
                {strategyJobs.length > 0 && (
                  <span className="ml-1 text-[10px] tabular-nums text-muted-foreground">
                    {strategyJobs.length}
                  </span>
                )}
              </TabsTrigger>
            </TabsList>

            {/* Overview Tab */}
            <TabsContent value="overview">
              <div className="space-y-4 pt-4">
                {/* Description */}
                <Card>
                  <CardContent className="p-4">
                    <p className="text-sm text-muted-foreground">
                      {strategy.description}
                    </p>
                    {strategy.job_ids.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        {strategy.job_ids.map((id) => (
                          <Badge
                            key={id}
                            className="bg-muted text-muted-foreground text-[10px]"
                          >
                            {id}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>

                {/* Stats */}
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <StatCard
                    title="Win Rate"
                    value={`${strategy.stats.win_rate}%`}
                  />
                  <StatCard
                    title="Total Trades"
                    value={`${strategy.stats.total_closed}`}
                  />
                  <StatCard
                    title="Total P&L"
                    value={`$${strategy.stats.total_pnl.toFixed(2)}`}
                    color={strategy.stats.total_pnl >= 0 ? "text-profit" : "text-loss"}
                  />
                  <StatCard
                    title="Open Positions"
                    value={`${strategy.stats.open_positions}`}
                  />
                </div>

                {/* Config parameters */}
                {Object.keys(strategy.config_params).length > 0 && (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm font-medium">
                        Parameters
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-3">
                        {Object.entries(strategy.config_params).map(
                          ([key, value]) => (
                            <div key={key} className="flex justify-between gap-2">
                              <dt className="text-xs text-muted-foreground truncate">
                                {key}
                              </dt>
                              <dd className="text-xs font-medium tabular-nums shrink-0">
                                {String(value)}
                              </dd>
                            </div>
                          )
                        )}
                      </dl>
                    </CardContent>
                  </Card>
                )}
              </div>
            </TabsContent>

            {/* Trades Tab */}
            <TabsContent value="trades">
              <div className="pt-4">
                <StrategyTradesTable trades={trades} />
              </div>
            </TabsContent>

            {/* Pipeline Tab */}
            <TabsContent value="pipeline">
              <div className="pt-4">
                <StrategyJobsTable
                  jobs={strategyJobs}
                  onSelectJob={setSelectedJob}
                />
              </div>
            </TabsContent>
          </Tabs>
        )}

        <PipelineJobDetailModal
          job={selectedJob}
          onClose={() => setSelectedJob(null)}
        />
      </main>
    </div>
  );
}

function StatCard({
  title,
  value,
  color = "",
}: {
  title: string;
  value: string;
  color?: string;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-medium text-muted-foreground">
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`text-xl font-bold tabular-nums ${color}`}>{value}</div>
      </CardContent>
    </Card>
  );
}
