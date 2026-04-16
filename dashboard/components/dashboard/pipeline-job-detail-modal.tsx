"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import type {
  SelectedPipelineJob,
  JobDetailResponse,
  JobDetailTicker,
  JobDetailSignal,
  JobDetailPositionClosed,
} from "@/lib/types";
import { formatDuration } from "./pipeline-timeline";
import { fetchAPI } from "@/lib/api";
import Link from "next/link";
import {
  PHASE_LABELS,
  CATEGORY_COLORS,
  STRATEGY_LABELS,
  getStatusTextClass,
  getStatusLabel,
} from "@/lib/pipeline-constants";
import { VariationBadge } from "@/components/strategies/variation-badge";

function formatTimestamp(iso: string | null) {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1">
      <span className="text-xs text-muted-foreground shrink-0">{label}</span>
      <span className="text-xs text-right">{children}</span>
    </div>
  );
}

function formatMoney(v: number | null | undefined): string {
  if (v == null) return "-";
  const sign = v < 0 ? "-" : "";
  const abs = Math.abs(v);
  return `${sign}$${abs.toFixed(2)}`;
}

function formatPct(v: number | null | undefined): string {
  if (v == null) return "-";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function TickerGrid({ tickers }: { tickers: JobDetailTicker[] }) {
  if (!tickers.length) return null;

  // Group by setup_type when multiple strategies present
  const groups = new Map<string, JobDetailTicker[]>();
  for (const t of tickers) {
    const key = t.setup_type;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(t);
  }

  return (
    <div className="space-y-3">
      {Array.from(groups.entries()).map(([setup, items]) => (
        <div key={setup}>
          <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
            {STRATEGY_LABELS[setup] ?? setup} · {items.length}
          </div>
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
            {items.map((t) => (
              <div
                key={`${setup}-${t.ticker}`}
                className="rounded-md border border-border bg-muted/20 px-2 py-1.5"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="font-mono text-sm font-semibold">{t.ticker}</span>
                    <VariationBadge value={t.variation} />
                  </div>
                  {t.gap_pct != null && (
                    <span
                      className={`text-[10px] tabular-nums ${
                        t.gap_pct >= 0 ? "text-profit" : "text-loss"
                      }`}
                    >
                      {formatPct(t.gap_pct)}
                    </span>
                  )}
                </div>
                <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                  {t.entry_price != null && (
                    <span className="tabular-nums">{formatMoney(t.entry_price)}</span>
                  )}
                  {t.rvol != null && (
                    <span className="tabular-nums">RVOL {t.rvol.toFixed(1)}x</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function SignalsList({ signals }: { signals: JobDetailSignal[] }) {
  if (!signals.length) return null;
  return (
    <div className="space-y-1.5">
      {signals.map((s, i) => {
        const status = s.order?.status ?? (s.acted_on ? "submitted" : "not entered");
        const filled = s.order?.filled_qty ?? 0;
        const qty = s.order?.qty ?? 0;
        const price =
          s.order?.filled_avg_price ?? s.order?.price ?? s.entry_price;
        return (
          <div
            key={`${s.ticker}-${i}`}
            className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/20 px-2 py-1.5 text-xs"
          >
            <div className="flex items-center gap-2">
              <span className="font-mono font-semibold">{s.ticker}</span>
              <VariationBadge value={s.variation} />
              <span className="text-muted-foreground">
                {s.order?.side ?? "buy"} {qty || ""}
              </span>
              <span className="tabular-nums text-muted-foreground">
                @ {formatMoney(price)}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {filled > 0 && qty > 0 && (
                <span className="text-[10px] tabular-nums text-muted-foreground">
                  {filled}/{qty}
                </span>
              )}
              <span
                className={
                  status === "filled"
                    ? "text-profit"
                    : status === "rejected" || status === "cancelled"
                      ? "text-loss"
                      : "text-muted-foreground"
                }
              >
                {status}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PositionsClosedList({
  positions,
}: {
  positions: JobDetailPositionClosed[];
}) {
  if (!positions.length) return null;
  return (
    <div className="space-y-1.5">
      {positions.map((p, i) => (
        <div
          key={`${p.ticker}-${i}`}
          className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/20 px-2 py-1.5 text-xs"
        >
          <div className="flex items-center gap-2">
            <span className="font-mono font-semibold">{p.ticker}</span>
            <VariationBadge value={p.variation} />
            <span className="text-muted-foreground">{p.exit_reason ?? "—"}</span>
          </div>
          <div className="flex items-center gap-2 tabular-nums">
            <span className="text-muted-foreground">
              {formatMoney(p.entry_price)} → {formatMoney(p.exit_price)}
            </span>
            {p.realized_pnl != null && (
              <span
                className={p.realized_pnl >= 0 ? "text-profit" : "text-loss"}
              >
                {p.realized_pnl >= 0 ? "+" : ""}
                {formatMoney(p.realized_pnl)}
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-border bg-muted/10 p-3">
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
        {label}
      </div>
      {children}
    </div>
  );
}

export function PipelineJobDetailModal({
  job,
  onClose,
}: {
  job: SelectedPipelineJob | null;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<JobDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    if (!job?.job_id || !job.date) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetail(null);
    fetchAPI<JobDetailResponse>(
      `/api/pipeline/job-detail?job_id=${encodeURIComponent(job.job_id)}&trade_date=${encodeURIComponent(job.date)}`
    )
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch(() => {
        if (!cancelled) setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [job?.job_id, job?.date]);

  if (!job) return null;

  const isTerminal = ["success", "failed"].includes(job.status);
  const hasExecution = job.started_at != null;
  const tickers = detail?.tickers ?? [];
  const signals = detail?.signals ?? [];
  const positions = detail?.positions_closed ?? [];
  const dailyPnl = detail?.daily_pnl ?? null;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-auto">
        <DialogHeader>
          <div className="flex items-center gap-2 flex-wrap">
            <DialogTitle>{job.label}</DialogTitle>
            {!["missed", "upcoming"].includes(job.status) && (
              <span className={`text-xs font-medium ${getStatusTextClass(job.status)}`}>
                {getStatusLabel(job.status, job.failure_reason)}
              </span>
            )}
            {job.category && job.category !== "system" && (
              <Badge
                className={`text-[10px] px-1.5 py-0 ${CATEGORY_COLORS[job.category] || CATEGORY_COLORS.system}`}
              >
                {job.category}
              </Badge>
            )}
          </div>
          {job.description && (
            <DialogDescription>{job.description}</DialogDescription>
          )}
        </DialogHeader>

        {/* Details grid */}
        <div className="divide-y divide-border">
          {job.strategy && (
            <DetailRow label="Strategy">
              <Link
                href={`/strategies/${job.strategy}`}
                className="text-blue-400 hover:underline"
              >
                {STRATEGY_LABELS[job.strategy] ?? job.strategy}
              </Link>
            </DetailRow>
          )}
          {job.phase && (
            <DetailRow label="Phase">
              {PHASE_LABELS[job.phase] || job.phase}
            </DetailRow>
          )}
          {job.date && (
            <DetailRow label="Date">{job.date}</DetailRow>
          )}
          {job.scheduled_time && (
            <DetailRow label="Scheduled">{job.scheduled_time}</DetailRow>
          )}
          {hasExecution && (
            <>
              <DetailRow label="Started">{formatTimestamp(job.started_at)}</DetailRow>
              <DetailRow label="Finished">{formatTimestamp(job.finished_at)}</DetailRow>
              <DetailRow label="Duration">
                {job.duration_seconds != null
                  ? formatDuration(job.duration_seconds)
                  : "-"}
              </DetailRow>
            </>
          )}
        </div>

        {/* Result summary */}
        {job.result_summary && (
          <div className="rounded-md border border-border bg-muted/30 p-3">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
              Result
            </span>
            <p className="mt-1 text-sm">{job.result_summary}</p>
          </div>
        )}

        {/* Error / timeout explanation */}
        {job.error && (
          <div className="rounded-md border border-loss/20 bg-loss/5 p-3">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-loss">
              {job.failure_reason === "timeout" ? "Timeout" : "Error"}
            </span>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-loss/90">
              {job.error}
            </pre>
          </div>
        )}

        {/* Status messages for non-executed jobs */}
        {!hasExecution && !isTerminal && (
          <p className="text-sm text-muted-foreground">
            {job.status === "upcoming"
              ? "This job has not run yet."
              : job.status === "missed"
                ? "This job has not run yet."
                : job.status === "skipped"
                  ? "This job was skipped."
                  : null}
          </p>
        )}

        {/* Enriched detail sections */}
        {detailLoading && hasExecution && (
          <p className="text-xs text-muted-foreground">Loading details…</p>
        )}

        {tickers.length > 0 && (
          <Section label={`Tickers · ${tickers.length}`}>
            <TickerGrid tickers={tickers} />
          </Section>
        )}

        {signals.length > 0 && (
          <Section
            label={`Signals · ${detail?.entered_count ?? 0}/${detail?.signal_count ?? signals.length} entered`}
          >
            <SignalsList signals={signals} />
          </Section>
        )}

        {positions.length > 0 && (
          <Section label={`Positions Closed · ${positions.length}`}>
            <PositionsClosedList positions={positions} />
          </Section>
        )}

        {dailyPnl && (
          <Section label="Daily P&L">
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div>
                <div className="text-[10px] text-muted-foreground">Realized</div>
                <div
                  className={`tabular-nums ${dailyPnl.realized_pnl >= 0 ? "text-profit" : "text-loss"}`}
                >
                  {formatMoney(dailyPnl.realized_pnl)}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-muted-foreground">Trades</div>
                <div className="tabular-nums">{dailyPnl.num_trades}</div>
              </div>
              <div>
                <div className="text-[10px] text-muted-foreground">W/L</div>
                <div className="tabular-nums">
                  {dailyPnl.num_winners}/{dailyPnl.num_losers}
                </div>
              </div>
            </div>
          </Section>
        )}
      </DialogContent>
    </Dialog>
  );
}
