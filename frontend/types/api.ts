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
}

export interface PortfolioPosition {
  id: number;
  bond_id: number;
  ticker: string;
  name: string;
  currency: string;
  quantity: number;
  purchase_clean_price: number | null;
  purchase_date: string | null;
  clean_price: number | null;
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
