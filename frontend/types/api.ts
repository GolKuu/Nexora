/** Shapes returned by the backend. Kept in one place so a contract change
 *  breaks the build rather than a page at runtime. */

export type DataMode = "live" | "delayed" | "end_of_day" | "cached" | "mock";
export type UiMode = "simple" | "pro";
export type RiskProfile = "conservative" | "balanced" | "aggressive";
export type InflationSource = "automatic" | "official" | "forecast" | "manual";

export interface Freshness {
  as_of: string | null;
  age_hours: number | null;
  data_mode: DataMode | null;
  is_mock: boolean;
  label: string | null;
}

export interface BondListItem {
  id: number;
  ticker: string;
  isin: string | null;
  name: string;
  issuer_name: string | null;
  currency: string;
  bond_type: string | null;
  maturity_date: string | null;
  years_to_maturity: number | null;
  coupon_rate_pct: number | null;
  yield_pct: number | null;
  real_yield_pct: number | null;
  clean_price: number | null;
  investment_score: number | null;
  credit_score?: number | null;
  liquidity_score?: number | null;
  growth_score?: number | null;
  hold_score?: number | null;
  trade_score?: number | null;
  data_quality_score?: number | null;
  data_mode: DataMode | null;
  note?: string | null;
}

export interface BondListResponse {
  items: BondListItem[];
  total: number;
  limit: number;
  offset: number;
  data_mode: DataMode | null;
  warning: string | null;
}

export interface ScoreWord {
  score: number | null;
  word?: string | null;
  note?: string | null;
}

export interface SimpleView {
  yield_pct: number | null;
  real_yield_pct: number | null;
  inflation_pct: number | null;
  years_to_maturity: number | null;
  maturity_date: string | null;
  reliability: ScoreWord;
  liquidity: ScoreWord;
  growth_potential: ScoreWord;
  overall: {
    score: number | null;
    verdict: string;
    summary: string;
    confidence: number | null;
  };
}

export interface ProView {
  available: boolean;
  ytm?: number | null;
  ytm_source?: string | null;
  current_yield?: number | null;
  clean_price?: number | null;
  dirty_price?: number | null;
  accrued_interest?: number | null;
  macaulay_duration?: number | null;
  modified_duration?: number | null;
  convexity?: number | null;
  credit_spread?: number | null;
  risk_free_rate?: number | null;
  pull_to_par?: number | null;
  bid?: number | null;
  ask?: number | null;
  bid_ask_spread?: number | null;
  bid_ask_spread_pct?: number | null;
  volume?: number | null;
  turnover?: number | null;
  number_of_trades?: number | null;
  avg_daily_turnover_30d?: number | null;
  trading_days_30d?: number | null;
  price_volatility_90d?: number | null;
  formula_version?: string | null;
  calculated_at?: string | null;
}

export interface IssuerRef {
  id: number;
  code: string;
  name: string;
  short_name: string | null;
  sector: string | null;
  industry: string | null;
  is_financial_institution: boolean;
  is_state_owned: boolean;
  kase_url: string | null;
}

export interface BondReference {
  id: number;
  ticker: string;
  isin: string | null;
  name: string;
  currency: string;
  nominal: number | null;
  coupon_rate: number | null;
  coupon_rate_pct: number | null;
  coupon_type: string | null;
  coupon_frequency: number | null;
  next_coupon_date: string | null;
  issue_date: string | null;
  maturity_date: string | null;
  issue_size: number | null;
  outstanding_amount: number | null;
  market_segment: string | null;
  bond_type: string | null;
  secured: boolean | null;
  subordinated: boolean | null;
  callable: boolean | null;
  putable: boolean | null;
  guarantee: string | null;
  kase_url: string | null;
  is_active: boolean;
  issuer: IssuerRef | null;
  provenance: {
    source: string | null;
    source_url: string | null;
    source_identifier: string | null;
    fetched_at: string | null;
  };
}

export interface ScoreSummary {
  value: number | null;
  confidence: number | null;
  version: string;
  calculated_at: string;
  notes: string | null;
}

export interface BondCard {
  bond: BondReference;
  simple: SimpleView;
  pro: ProView;
  scores: Record<string, ScoreSummary>;
  freshness: Freshness;
  warning: string | null;
}

export interface ScoreComponent {
  code: string;
  label: string;
  value: number | null;
  weight: number;
  contribution: number | null;
  raw_value: number | null;
  raw_unit: string | null;
  available: boolean;
  explanation: string | null;
}

export interface ScoreExplanation {
  kind: string;
  value: number | null;
  verdict: string;
  summary: string;
  confidence: number | null;
  version: string;
  notes: string | null;
  strengths: ScoreComponent[];
  weaknesses: ScoreComponent[];
  missing_data: { code: string; label: string; weight: number }[];
  components: ScoreComponent[];
  related?: Record<string, number | null>;
}

export interface ExplanationResponse {
  ticker: string;
  text: string;
  generated_by: "llm" | "engine";
  cached?: boolean;
  model?: string | null;
  deterministic_text?: string;
  explanation: ScoreExplanation;
}

export interface CashFlow {
  payment_date: string;
  period_start: string | null;
  coupon_amount: number | null;
  principal_amount: number | null;
  total_amount: number | null;
  is_estimated: boolean;
  is_final: boolean;
}

export interface HistoryPoint {
  timestamp: string;
  clean_price: number | null;
  ytm: number | null;
  volume: number | null;
  turnover: number | null;
  data_mode: DataMode | null;
}

export interface CalculatorResult {
  available: boolean;
  reason?: string;
  ticker?: string;
  currency?: string;
  requested_amount?: number;
  quantity?: number;
  price_per_bond?: number;
  min_amount?: number;
  accrued_interest_per_bond?: number;
  invested?: number;
  uninvested_remainder?: number;
  coupons_total?: number;
  principal_total?: number;
  proceeds?: number;
  profit?: number;
  profit_real?: number | null;
  years?: number;
  total_return_pct?: number | null;
  annualized_return_pct?: number | null;
  real_total_return_pct?: number | null;
  real_annualized_return_pct?: number | null;
  inflation_pct?: number | null;
  inflation_source?: string | null;
  assumptions?: string[];
  schedule?: {
    date: string;
    coupon: number;
    principal: number;
    total: number;
    is_estimated: boolean;
  }[];
}

export interface CompareResponse {
  mode: UiMode;
  amount?: number | null;
  rows: { key: string; label: string; unit: string }[];
  columns: {
    id: number;
    ticker: string;
    name: string;
    issuer: string | null;
    currency: string;
    data_mode: DataMode | null;
    values: Record<string, number | null>;
  }[];
  best: Record<string, number | null>;
  winner: { id: number; ticker: string; investment_score: number; reason: string } | null;
}

export interface UserSettings {
  persisted: boolean;
  inflation_enabled: boolean;
  inflation_source: InflationSource;
  manual_inflation_rate: number | null;
  show_real_return: boolean;
  base_currency: string;
  ui_mode: UiMode;
  risk_profile: RiskProfile;
  theme: "light" | "dark" | "system";
  remember_calculator_amount: boolean;
  calculator_amount: number | null;
  language: string;
  conservative_missing_data_mode: boolean;
  news_enabled: boolean;
  kase_news_enabled: boolean;
  external_news_enabled: boolean;
  chart_news_markers_enabled: boolean;
  forecast_enabled: boolean;
  uncertainty_intervals_enabled: boolean;
  default_chart_range: "1d" | "5d" | "1m" | "3m" | "6m" | "1y" | "2y" | "3y" | "5y" | "max";
}

export interface PortfolioSummary {
  position_count: number;
  market_value: number | null;
  cost: number | null;
  unrealized_pnl: number | null;
  portfolio_ytm: number | null;
  portfolio_ytm_pct: number | null;
  portfolio_real_ytm_pct: number | null;
  portfolio_duration: number | null;
  average_investment_score: number | null;
  inflation_pct: number | null;
  dividends: number | null;
  coupons: number | null;
  asset_allocation: { stocks: number; bonds: number };
  currency_allocation: Record<string, number>;
  issuer_concentration: Array<{
    issuer_id: number;
    issuer_name: string;
    market_value: number;
    percent: number | null;
  }>;
}

export interface PortfolioPosition {
  id: number;
  instrument_type: "bond" | "stock";
  bond_id: number | null;
  stock_id?: number | null;
  ticker: string;
  name: string;
  issuer_id: number;
  issuer_name: string;
  currency: string;
  quantity: number;
  purchase_clean_price: number | null;
  purchase_price?: number | null;
  purchase_date: string | null;
  clean_price: number | null;
  current_price?: number | null;
  dividend_income_trailing?: number | null;
  market_value: number | null;
  cost: number | null;
  unrealized_pnl: number | null;
  ytm: number | null;
  real_ytm: number | null;
  modified_duration: number | null;
  years_to_maturity: number | null;
  investment_score: number | null;
}

export interface PortfolioDetail {
  id: number;
  name: string;
  base_currency: string;
  positions: PortfolioPosition[];
  summary: PortfolioSummary;
}

export interface KaseHealth {
  configured_mode: string;
  provider: string;
  connected: boolean;
  reachable?: boolean;
  is_mock: boolean;
  data_mode?: string;
  latency_ms?: number | null;
  checked_at: string;
  detail: string | null;
  warning: string | null;
}

export interface PeersResponse {
  ticker: string;
  peer_group: string | null;
  stats: {
    peer_count: number;
    peer_median_ytm?: number | null;
    peer_median_spread?: number | null;
    peer_median_duration?: number | null;
  };
  peers: {
    id: number;
    ticker: string;
    name: string;
    ytm: number | null;
    real_ytm: number | null;
    modified_duration: number | null;
    years_to_maturity: number | null;
  }[];
}

export interface StockScoreValue { value: number | null; confidence: number; version: string }
export interface StockListItem {
  id: number; ticker: string; isin: string; company_name: string; issuer: string;
  instrument_type: "stock" | "preferred_stock"; type_label: string; currency: string;
  price: number | null; bid: number | null; ask: number | null; change_percent?: number | null; market_cap: number | null;
  sector: string | null; metrics: Record<string, number | null>;
  scores: Record<string, StockScoreValue>; data_timestamp: string | null;
  data_mode: DataMode | null; source: string | null; kase_url: string | null;
}
export interface StockListResponse {
  items: StockListItem[]; total: number; limit: number; offset?: number;
  category?: string; ranking_score?: string; source?: string;
  data_mode?: DataMode; latest_market_timestamp?: string | null;
  generated_at?: string;
}
export interface StockCard extends StockListItem {
  simple: { price: number | null; company_earning_trend: string; valuation: string; dividends: number | null; risk: StockScoreValue; liquidity: StockScoreValue; important: string };
  pro: Record<string, number | null>; score_explanation: Array<{kind: string} & StockScoreValue>;
  dividends: Array<{ex_date: string | null; record_date: string | null; payment_date: string | null; dividend_per_share: number; currency: string; status: string; source_url?: string | null}>;
  corporate_actions: Array<{action_type: string; status: string; event_date: string | null; title: string; source_url: string | null}>;
}
export interface StockCalculation {
  stock_identifier: string; input_amount: number; quantity: number; unit_price: number | null;
  calculation_price_type: string | null; principal_cost: number; commission: number;
  total_purchase_cost: number; cash_remaining: number; current_market_value: number | null;
  dividend_income_trailing: number | null; scenario_price: number | null;
  scenario_profit: number | null; total_return_percent: number | null;
  liquidity_warning: string | null; warnings: string[]; data_timestamp: string | null; source: string | null;
  input_mode?: "amount" | "quantity"; requested_quantity?: number | null;
}
export interface BondInvestmentCalculation {
  bond_identifier: string; currency: string; input_amount: number;
  input_mode: "amount" | "quantity"; requested_quantity: number | null;
  quantity: number; unit_clean_price: number | null; unit_dirty_price: number | null;
  accrued_interest_per_bond: number | null; principal_cost: number;
  accrued_interest_total: number; commission: number; total_purchase_cost: number;
  cash_remaining: number; minimum_required_amount: number | null;
  coupon_income: number; principal_repayment: number; estimated_price_return: number | null;
  total_profit: number | null; total_cash_received: number | null;
  total_return_percent: number | null; annualized_return_percent: number | null;
  real_profit: number | null; real_return_percent: number | null;
  real_annualized_return_percent: number | null; inflation_rate_percent: number | null;
  inflation_source: string | null; holding_period_years: number | null;
  price_basis: string | null; scenario: string; exit_mode: string; exit_date: string | null;
  cashflows: Array<{ date: string; type: string; coupon_amount: number | null; principal_amount: number | null; total_amount: number | null; is_estimated: boolean }>;
  liquidity_warning: string | null; warnings: string[];
}
export interface EventReaction {
  price_before: number | null; return_5m: number | null; return_30m: number | null; return_1h: number | null;
  return_same_day: number | null; return_1d: number | null; return_5d: number | null; return_20d: number | null;
  volume_ratio: number | null; volatility_change: number | null; market_return: number | null;
  sector_return: number | null; abnormal_return_1d: number | null; abnormal_return_5d: number | null;
  benchmark_id: number | null; formula_version: string;
}
export interface HistoricalAnalogs {
  count: number; minimum_sample_size: number; sufficient_sample: boolean; message: string | null;
  positive_reaction_rate: number | null; negative_reaction_rate: number | null;
  median_return_1d: number | null; median_return_5d: number | null; median_abnormal_return: number | null;
}
export interface MarketEventItem {
  id: number; news_id: number; title: string; source: string; source_url: string; event_type: string;
  event_timestamp: string; importance: number; sentiment: number | null; surprise: number | null;
  source_confidence: number; analysis_confidence: number; impact_score: number | null; marker: "N"|"E"|"D"|"R"|"C"|"S";
  reaction: EventReaction | null; historical_analogs: HistoricalAnalogs; explanation: string;
}
export interface StockEventsResponse { ticker: string; items: MarketEventItem[]; total: number }
export interface NewsFeedItem {
  id: number; news_id: number; title: string; summary: string | null;
  source: string; source_url: string; published_at: string; event_type: string;
  importance: number; sentiment: number | null; source_confidence: number;
  analysis_confidence: number; impact_score: number | null; marker: string;
  ticker: string | null; instrument_type: string | null;
  reaction: EventReaction | null; explanation: string;
}
export interface NewsFeedResponse {
  items: NewsFeedItem[]; total: number;
  filters: { event_type: string | null; source: string | null; min_importance: number };
}
export interface StockHistoryResponse { ticker: string; quotes: Array<{timestamp:string; last:number|null; close:number|null; volume:number|null}> }

export interface ForecastHorizon {
  forecast_available: boolean; reason?: string; minimum_observations?: number; observations?: number;
  expected_return?: number; median_return?: number; probability_up?: number; probability_down?: number;
  q05?: number; q10?: number; q25?: number; q50?: number; q75?: number; q90?: number; q95?: number;
  expected_volatility?: number; confidence?: number; confidence_components?: Record<string, number>;
  selected_model?: string;
  factors?: Array<{feature: string; association: "positive" | "negative"; contribution: number}>;
}
export interface StockForecastResponse {
  instrument: string; as_of?: string; source_timestamp?: string; current_price?: number;
  data_mode?: DataMode | string; model_version?: string; forecast_available: boolean; reason?: string;
  selected_horizon?: string; horizons: Record<string, ForecastHorizon>;
  history: Array<{date: string; price: number; volume: number | null}>;
  path: Array<{date: string; median: number; q10: number; q25: number; q75: number; q90: number}>;
  confidence?: number; warnings: string[]; label?: string; disclaimer?: string;
  explanation?: Array<{feature: string; association: "positive" | "negative"; contribution: number}>;
  validation?: Record<string, Record<string, unknown>>;
  event_comparison?: {
    event_id: number; event_type: string; label: string;
    before: {generated_at: string; probability_up: number; median_return: number};
    after: {generated_at: string; probability_up: number; median_return: number};
  } | null;
  forecast_change?: {
    probability_change: number; expected_return_change: number;
    interval_width_change: number; confidence_change: number; reason: string;
  } | null;
}
export interface ForecastCalibrationBin {
  lower: number; upper: number; count: number; mean_probability: number | null; observed_frequency: number | null;
}
export interface ForecastPerformanceHorizon {
  evaluated_forecasts: number; mae_return: number | null; rmse: number | null;
  direction_accuracy: number | null; balanced_accuracy: number | null; brier_score: number | null;
  log_loss: number | null; calibration_error: number | null; calibration_bins: ForecastCalibrationBin[];
  interval_50_coverage: number | null; interval_80_coverage: number | null; quantile_loss: number | null;
  rank_correlation: number | null; information_coefficient: number | null;
}
export interface StockForecastPerformanceResponse {
  instrument: string; metrics_are_out_of_sample: true;
  horizons: Record<string, ForecastPerformanceHorizon>;
  walk_forward_validation: Record<string, Record<string, unknown>>;
  warning: string;
}
export interface InstrumentSearchItem { id: number; ticker: string; isin: string | null; name: string; instrument_type: "stock" | "bond"; type_label: string; href: string }
export interface InstrumentSearchResponse { items: InstrumentSearchItem[]; total: number; query: string }
export interface CrossAssetItem {
  instrument_type: "stock" | "bond"; ticker: string; name: string;
  risk: { value: number | null }; liquidity: { value: number | null };
  potential_income: { ytm?: number | null; dividend_yield_trailing?: number | null; price_change?: string };
  payment_income: string; horizon: string | null; volatility: number | null; cashflow_predictability: string;
}
export interface CrossAssetCompareResponse { items: CrossAssetItem[]; comparison_type: "cross_asset"; explanation: string; warning: string }

/** Browser agent (§31, §49): every value carries how it was read and how much
 *  that method is worth. `method: "visual"` is qualitative by construction. */
export interface KaseExtractedField {
  field: string;
  label: string | null;
  raw_value: string | null;
  normalized_value: unknown;
  unit: string | null;
  method: "dom" | "table" | "tooltip" | "document" | "visual";
  confidence: number;
  warnings: string[];
  source: {
    page_url: string;
    page_title: string | null;
    section: string | null;
    fetched_at: string;
    source_timestamp: string | null;
    browser_session_id: string | null;
    extractor_version: string;
  } | null;
}

export interface KaseDocumentLink {
  document_url: string;
  document_name: string;
  document_type: string;
  publication_date: string | null;
  source_page: string;
  section: string | null;
}

export interface KaseVerifyResponse {
  ticker: string;
  source: string;
  source_url: string | null;
  checked_at: string;
  checked_at_label: string;
  status: string;
  ok: boolean;
  notice: string | null;
  browser_blocked_by_captcha: boolean;
  requires_authentication: boolean;
  data_mode: string;
  identity_confirmed: boolean;
  tabs_available: string[];
  tabs_read: {
    tab_name: string;
    changed_content: boolean;
    tables: number;
    documents: number;
    status: string;
  }[];
  fields: Record<string, KaseExtractedField>;
  documents: KaseDocumentLink[];
  warnings: string[];
  chart: Record<string, unknown>;
  visual: Record<string, unknown> | null;
}

export interface KaseAnalysisFinding {
  kind: "observation" | "mismatch" | "warning" | "limitation";
  code: string;
  message: string;
  detail: Record<string, unknown>;
}

export interface KaseAnalysisResponse {
  ticker: string;
  url: string | null;
  status: string;
  summary: string;
  deterministic_summary: string;
  generated_by: "llm" | "engine";
  model: string | null;
  ai_unavailable_reason: string | null;
  analysis: {
    identity_confirmed: boolean;
    tabs_read: string[];
    views_read: string[];
    fields_extracted: number;
    facts: Record<
      string,
      {
        value: unknown;
        label: string | null;
        confidence: number | null;
        method: string | null;
      }
    >;
    mismatches: {
      field: string;
      on_page: string;
      in_database: string;
      page_confidence: number | null;
    }[];
    findings: KaseAnalysisFinding[];
  };
  browser: {
    identity_confirmed: boolean;
    blocked_by_captcha: boolean;
    requires_authentication: boolean;
    navigation_steps: number;
  };
}

export interface KaseTabResponse extends KaseVerifyResponse {
  section: string;
  tab: {
    tab_name: string;
    url: string;
    text: string;
    changed_content: boolean;
    status: string;
  } | null;
}

export interface KaseLinkResponse {
  ticker: string;
  url: string | null;
  verified_at: string | null;
  source: string | null;
}

/* ---- Daily series and change history (public data only) ------------------ */

export type InstrumentKind = "stock" | "bond";

/** One trading session. ``bar_basis`` says whether the exchange published the
 *  bar or whether we folded it out of our own snapshots of the public feed. */
export interface SeriesSession {
  date: string;
  timestamp: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  bid: number | null;
  ask: number | null;
  spread_pct: number | null;
  ytm: number | null;
  ytm_high: number | null;
  ytm_low: number | null;
  volume: number | null;
  turnover: number | null;
  trades: number | null;
  change_pct: number | null;
  observations: number;
  bar_basis: "native" | "sampled";
  sources: string[];
  data_mode: DataMode | string | null;
  change_events: number;
}

export interface SeriesMarker {
  date: string;
  count: number;
  max_importance: number;
  sections: string[];
  top: Array<{
    section: string;
    field: string;
    old_value: unknown;
    new_value: unknown;
    change_type: string;
    importance: number;
    source_url: string;
  }>;
}

export interface SeriesCoverage {
  requested_days: number;
  sessions: number;
  observations: number;
  first_session: string | null;
  last_session: string | null;
  expected_sessions: number;
  sessions_outside_calendar: number;
  coverage_ratio: number | null;
  longest_gap_sessions: number;
  native_bars: number;
  sampled_bars: number;
  sources: Record<string, number>;
  data_modes: Record<string, number>;
  includes_licensed: boolean;
  licensed_rows_excluded: number;
  licensed_free: boolean;
  mock: boolean;
  chartable: boolean;
}

export interface SeriesResponse {
  ticker: string;
  instrument_type: InstrumentKind;
  basis: string;
  price_unit: string;
  isin: string | null;
  name: string | null;
  currency: string;
  kase_url: string | null;
  maturity_date?: string | null;
  sessions: SeriesSession[];
  markers: SeriesMarker[];
  coverage: SeriesCoverage;
  warning: string | null;
}

export interface ChangeRecord {
  id: number;
  detected_at: string;
  ticker: string | null;
  isin: string | null;
  section: string;
  field: string;
  old_value: unknown;
  new_value: unknown;
  change_type: string;
  importance: number;
  material: boolean;
  source_url: string;
  source_timestamp: string | null;
  parser_version: string;
}

export interface ChangeSummary {
  ticker?: string;
  instrument_type?: InstrumentKind;
  changed: boolean;
  since: string | null;
  material_changes: number;
  summary: {
    price_changed: boolean;
    yield_changed: boolean;
    credit_changed: boolean;
    new_documents: number;
    sections: string[];
  };
  freshness?: {
    last_checked_at: string | null;
    last_changed_at: string | null;
    source_timestamp: string | null;
  };
}

export interface ScoreHistorySnapshot {
  id: number; kind: string; model_version: string; as_of: string | null;
  calculated_at: string; final_score: number; base_score: number;
  data_quality: number; confidence: number; band: string;
}
export interface ScoreHistoryTransition {
  from_snapshot_id: number; to_snapshot_id: number; from: number; to: number;
  delta: number | null; direction: "up" | "down" | "unchanged";
  components_changed: Array<{ code: string; label: string | null; from: number | null; to: number | null; delta: number | null; reason: string | null }>;
  red_flags_raised: Array<{ code: string; label?: string }>;
  red_flags_cleared: Array<{ code: string; label?: string }>;
  caps_applied: Array<{ code: string; label?: string }>;
  caps_lifted: Array<{ code: string; label?: string }>;
  model_version_changed: boolean;
}
export interface ScoreHistoryResponse {
  instrument_type: InstrumentKind; ticker: string; isin: string | null; name: string | null;
  count: number; snapshots: ScoreHistorySnapshot[]; transitions: ScoreHistoryTransition[]; note: string;
}
