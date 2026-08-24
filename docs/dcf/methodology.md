# AI DCF Valuation methodology

The retail result is an estimate of fair value, not an investment recommendation. The numerical model is `corporate-fcff-1.0.0`; AI is optional and cannot change its inputs or outputs.

The result also carries an explicitly bounded two-year factual comparison: exactly the two latest available FY statements, their reporting dates and changes in revenue, EBIT, EBITDA, operating/free cash flow, capex, net debt and EBIT margin. Missing report lines remain null. This historical comparison is persisted in the financial input snapshot and is not a forecast.

## Routing and readiness

The MVP routes ordinary operating companies to the corporate FCFF model. Banks, insurers, brokers and other financial institutions fail safely as unsupported. A run is blocked if the latest FY statement lacks revenue, EBIT, cash, debt, capex or share count, or if market price and governed macro inputs are unavailable. Missing values remain missing and are never converted to zero.

Inputs are point-in-time records. Every run persists the statement, market and macro snapshots, their source metadata and hashes. A later report produces a different snapshot hash and therefore a new run; old runs remain immutable and auditable.

## Calculation

For each of five explicit forecast years:

`Revenue → EBIT → NOPAT + D&A − Capex − ΔNWC = FCFF`

Each FCFF is discounted at WACC. Terminal value uses the perpetual-growth formula, with the hard guardrail `WACC > terminal growth`. Enterprise value is discounted FCFF plus discounted terminal value; equity value is enterprise value less net debt; fair value per share divides by diluted shares.

WACC is deterministic: risk-free rate plus beta times equity risk premium for cost of equity, blended with after-tax debt cost by market-value capital structure. Beta and tax fallbacks are configured policy values, explicitly marked as fallbacks and persisted with lower confidence. Terminal growth is sourced from the long-term macro record and capped by policy.

Bear, base and bull scenarios contain independent growth, margin, capex, WACC and terminal-growth assumptions. The engine recalculates each scenario and rejects results unless `bear <= base <= bull`.

## Versions and reproducibility

The run stores engine, assumption, prompt, disclaimer and optional AI-model versions. `input_snapshot_hash` is canonical JSON over inputs, assumptions, lineage and versions. Equal snapshot plus equal engine version yields equal numeric output. A separate valuation-cache hash covers the report/version, macro records, shares and model versions, so a fresh quote updates retail upside/downside without spending quota or rerunning fundamentals. Full yearly cash-flow calculations remain on the protected audit endpoint; retail receives rounded scenario values only.
