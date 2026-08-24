# AI DCF governance and controls

## System boundary

AI may summarize qualitative context or explain a completed valuation. It is not the numerical engine and cannot silently create assumptions. `DCFValuationEngine` is a pure deterministic module with no network, database or model dependency. An AI outage therefore does not affect fair values.

## Primary risks and mitigations

- Hallucinated facts: only normalized stored financial, market and macro records enter the engine; readiness fails closed.
- Look-ahead bias: source availability timestamps and report versions are captured in the run snapshot.
- Unsuitable methodology: financial institutions are routed to an unsupported result for the MVP.
- Unreasonable extrapolation: configured bounds cover growth, margins, tax, WACC, terminal growth, horizon and shares.
- False precision: the retail UI rounds values and separates analysis confidence from fair value.
- Inverted scenarios: a critical ordering validation rejects the run.
- Untraceable fallback: each persisted assumption records source, reason, confidence and `fallback_used`.
- Unauthorized disclosure: retail result routes never return calculations or internal assumptions; the audit route requires the backend admin guard.
- Cost escalation: cached identical runs do not consume quota; cost events record compute time, cache state, tokens and AI cost. The current deterministic path records zero AI tokens and cost.

## Audit evidence

Each run retains user/session ownership, timestamps, status, statement ID, snapshot hashes, source URLs, scenario inputs and yearly calculations, validations, warnings, versions, disclaimer version, shown timestamp, latency, usage and cost events. Historical runs are append-only. Internal reviewers can reproduce a result from the protected `/api/v1/admin/dcf/{run_id}` payload.

Model status begins as `pilot`. Promotion to `approved` requires named human approval, golden-set review, numeric regression results, data-quality review, sensitivity/stability thresholds and Compliance approval of the active disclaimer. Model releases must use a new engine or assumption version; previously completed runs must not be rewritten.

## Human oversight and incident response

Analysts can inspect sources and assumptions through the audit API. Suspected source conflicts, corporate actions, restatements, extreme values or scenario instability should mark the run stale or failed and trigger manual review. Trading is outside the DCF boundary and no output can execute an order.
