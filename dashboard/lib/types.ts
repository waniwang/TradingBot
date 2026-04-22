export interface BotStatus {
  running: boolean;
  phase: string;
  phase_label: string;
  phase_description: string;
  environment: "paper" | "live";
  next_job: string | null;
  next_job_label: string | null;
  next_job_time: string | null;
  countdown_seconds: number | null;
  progress: { task: string; detail?: string } | null;
}

export interface Portfolio {
  portfolio_value: number;
  cash: number;
  daily_pnl: number;
  daily_pnl_pct: number;
  daily_realized: number;
  daily_unrealized: number;
  open_positions: number;
  max_positions: number;
  trades_today: number;
}

export interface OpenPosition {
  id: number;
  ticker: string;
  setup: string;
  side: string;
  shares: number;
  entry: number;
  stop: number;
  current: number;
  gain_pct: number;
  unrealized_pnl: number;
  days: number;
  partial: boolean;
  opened_at: string | null;
  /** A / B / A+B / C — only set for EP strategies; null otherwise. */
  variation: string | null;
}

export interface ClosedPosition {
  id: number;
  date: string | null;
  ticker: string;
  setup: string;
  side: string;
  entry: number;
  exit: number | null;
  pnl: number;
  days: number;
  reason: string;
  /** A / B / A+B / C — only set for EP strategies; null otherwise. */
  variation: string | null;
}

export interface WatchlistCandidate {
  id: number;
  ticker: string;
  setup: string;
  setup_raw: string;
  stage: string;
  /** A / B / A+B / C — only set for EP strategies; null otherwise. */
  variation: string | null;
  scan_date: string;
  /** ISO-8601 UTC — when this row first appeared on the watchlist. */
  added_at: string | null;
  /** ISO-8601 UTC — when the row entered its current stage. */
  stage_changed_at: string | null;
  /** ISO-8601 UTC — last time any field on the row changed. */
  updated_at: string | null;
  gap_pct: number | null;
  pre_mkt_rvol: number | null;
  consolidation_days: number | null;
  atr_ratio: number | null;
  rs_score: number | null;
  quality_flags: string[];
}

export interface WatchlistData {
  counts: { active: number; ready: number; watching: number };
  active: WatchlistCandidate[];
  ready: WatchlistCandidate[];
  watching: WatchlistCandidate[];
}

export interface SignalToday {
  id: number;
  time: string;
  fired_at: string;
  ticker: string;
  setup: string;
  entry: number;
  stop: number;
  gap_pct: number | null;
  acted: boolean;
  /** A / B / A+B / C — only set for EP strategies; null otherwise. */
  variation: string | null;
  /** Latest Order row status — pending/submitted/filled/partially_filled/cancelled/rejected.
   * null if no order was placed (acted=false). "acted=true + cancelled" is the "order
   * submitted but never filled" case (e.g. limit didn't print). */
  order_status: string | null;
  filled_qty: number | null;
  filled_avg_price: number | null;
  order_qty: number | null;
}

export interface DailyPnl {
  date: string;
  daily_pnl: number;
  realized: number;
  unrealized: number;
  cumulative: number;
  portfolio_value: number;
  trades: number;
  winners: number;
  losers: number;
}

export interface PipelineJob {
  job_id: string;
  label: string;
  time: string;
  /** For window jobs (intraday monitor, retry loops); null for point-in-time jobs. */
  end_time: string | null;
  category: "scan" | "trade" | "monitor" | "system";
  phase: string;
  description: string;
  display_day_offset: number;
  strategy: string | null;
}

export interface PipelineExecution {
  id: number;
  job_id: string;
  label: string;
  status: "running" | "success" | "failed" | "skipped";
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  result_summary: string | null;
  error: string | null;
  failure_reason: string | null;
}

export interface PipelineData {
  trade_date: string;
  is_trading_day: boolean;
  last_trading_date: string | null;
  schedule: PipelineJob[];
  executions: PipelineExecution[];
  current_phase: string;
  next_job: {
    job_id: string;
    label: string;
    time: string;
    countdown_seconds: number | null;
  } | null;
  phases: Record<string, { label: string; time_range: string }>;
  phase_order: string[];
}

export interface MergedPipelineJob {
  job_id: string;
  label: string;
  phase: string;
  description: string;
  scheduled_time: string;
  /** For window jobs (intraday monitor, retry loops); null for point-in-time jobs. */
  end_time: string | null;
  category: string;
  display_day_offset: number;
  strategy: string | null;
  status: "success" | "running" | "failed" | "skipped" | "upcoming" | "missed";
  failure_reason: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  result_summary: string | null;
  error: string | null;
}

export interface PipelineDayHistory {
  date: string;
  is_trading_day: boolean;
  summary: "all_passed" | "some_issues" | "failures" | "in_progress" | "no_data";
  jobs: MergedPipelineJob[];
}

export interface FlatExecution extends PipelineExecution {
  date: string;
}

export interface PipelineHistoryResponse {
  days: PipelineDayHistory[];
  recent_executions: FlatExecution[];
}

export type SelectedPipelineJob = {
  job_id: string;
  label: string;
  status: string;
  failure_reason: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  result_summary: string | null;
  error: string | null;
  category?: string;
  phase?: string;
  description?: string;
  date?: string;
  scheduled_time?: string;
  end_time?: string | null;
  strategy?: string | null;
};

export interface JobDetailTicker {
  ticker: string;
  setup_type: string;
  stage?: string;
  entry_price?: number | null;
  gap_pct?: number | null;
  rvol?: number | null;
  market_cap?: number | null;
  notes?: string | null;
  /** A / B / C — only set for EP strategies; null otherwise. */
  variation?: string | null;
}

export interface JobDetailSignal {
  ticker: string;
  setup_type: string;
  entry_price: number;
  stop_price: number;
  gap_pct: number | null;
  acted_on: boolean;
  fired_at: string | null;
  /** A / B / A+B / C — only set for EP strategies; null otherwise. */
  variation: string | null;
  order: {
    id: number;
    side: string;
    qty: number;
    price: number | null;
    status: string;
    filled_qty: number;
    filled_avg_price: number | null;
  } | null;
}

export interface JobDetailPositionClosed {
  ticker: string;
  setup_type: string;
  side: string;
  shares: number;
  entry_price: number;
  exit_price: number | null;
  exit_reason: string | null;
  realized_pnl: number | null;
  opened_at: string | null;
  closed_at: string | null;
  /** A / B / A+B / C — only set for EP strategies; null otherwise. */
  variation: string | null;
}

export interface JobDetailResponse {
  job_id: string;
  label: string;
  phase: string;
  category: string;
  description: string;
  scheduled_time: string;
  strategy: string | null;
  trade_date: string;
  execution: {
    id: number;
    job_id: string;
    label: string;
    status: string;
    started_at: string | null;
    finished_at: string | null;
    duration_seconds: number | null;
    result_summary: string | null;
    error: string | null;
    failure_reason: string | null;
  } | null;
  tickers?: JobDetailTicker[];
  strategy_breakdown?: Record<string, number>;
  signals?: JobDetailSignal[];
  entered_count?: number;
  signal_count?: number;
  positions_closed?: JobDetailPositionClosed[];
  daily_pnl?: {
    realized_pnl: number;
    unrealized_pnl: number;
    total_pnl: number;
    portfolio_value: number;
    num_trades: number;
    num_winners: number;
    num_losers: number;
  } | null;
}

export interface MarketIndex {
  ticker: string;
  price: number;
  change_pct: number;
}

export interface MarketData {
  indices: MarketIndex[];
  error?: string;
}

export interface RiskData {
  daily_pnl: number;
  daily_limit_pct: number;
  weekly_pnl: number;
  weekly_limit_pct: number;
  open_positions: number;
  max_positions: number;
}

export interface PerformanceSummary {
  total_pnl: number;
  win_rate: number;
  total_trades: number;
  best_day: number;
  worst_day: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  strategy_breakdown: Record<string, { trades: number; pnl: number; winners: number }>;
}

export interface StrategyLastRun {
  job_id: string;
  label: string;
  status: string;
  ran_at: string | null;
  result_summary: string | null;
}

export interface StrategyStats {
  open_positions: number;
  total_closed: number;
  win_rate: number;
  total_pnl: number;
}

export type ParamVariation = "base" | "A" | "B" | "C";
export type ParamPhase = "scan" | "execute" | "day2_confirm";

export interface ConfigParamRow {
  key: string;
  value: unknown;
  description: string;
  variation: ParamVariation;
  phase: ParamPhase;
}

export interface PhaseLabel {
  short: string;
  long: string;
  description: string;
}

export interface StrategyInfo {
  slug: string;
  display_name: string;
  enabled: boolean;
  description: string;
  job_ids: string[];
  config_params: ConfigParamRow[];
  stats: StrategyStats;
  last_run: StrategyLastRun | null;
}

export interface StrategyListResponse {
  strategies: StrategyInfo[];
  phase_labels: Record<ParamPhase, PhaseLabel>;
}
