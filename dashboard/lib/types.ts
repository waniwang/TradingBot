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
}

export interface WatchlistCandidate {
  id: number;
  ticker: string;
  setup: string;
  setup_raw: string;
  stage: string;
  scan_date: string;
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
  category: "scan" | "trade" | "monitor" | "system";
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
}

export interface PipelineData {
  trade_date: string;
  schedule: PipelineJob[];
  executions: PipelineExecution[];
  current_phase: string;
  next_job: {
    job_id: string;
    label: string;
    time: string;
    countdown_seconds: number | null;
  } | null;
}

export interface PipelineHistoryDay {
  date: string;
  executions: PipelineExecution[];
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
