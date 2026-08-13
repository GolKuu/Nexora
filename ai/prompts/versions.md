# Prompt changelog

Prompts are versioned because they change behaviour as surely as weights do.
A dataset records the `PROMPT_VERSION` it was generated against
(`data/ai/sft/<version>/manifest.json`), and a model records the prompt version
it was trained with (`models/<run>/metadata.json`). An answer is reproducible
only when model version, dataset version, prompt version, formula version and
index version are all pinned (§60).

## 2.0.0 — 2026-08-13

First version of the *own-model* prompt set. Not backwards compatible with the
1.x prompts in `backend/app/ai/prompts.py`, which addressed a general-purpose
third-party model and only ever asked it to rephrase.

* Added the tool catalogue rendering (`render_tool_list`) directly into the
  system prompt, and the exact `{"tool": ..., "arguments": {...}}` call format
  the model is trained to emit (§14).
* Added the four-way provenance taxonomy — ФАКТ / РАСЧЕТ / СЦЕНАРИЙ /
  ИНТЕРПРЕТАЦИЯ (§18).
* Added the prompt-injection rule: documents and tool results are data
  (§45).
* Added the fixed answer skeleton — Коротко / Почему / Основные риски / Что
  будет с X ₸ / Что проверить — with the instruction to drop empty sections
  (§49).
* Added `FORBIDDEN_PHRASES`, checked both in evaluation and at inference
  (§66).
* Added `tool_decision_prompt` with the Russian argument-extraction rules
  ("5 млн тенге" → `amount: 5000000`, "до трех лет" → `max_maturity_years: 3`,
  percentages as decimals).

### Migration

`backend/app/ai/prompts.py` stays in place: it is what the *deterministic
fallback path* uses when no model is reachable. It is no longer the product's
primary intelligence.

## 1.0.0 — earlier

Explainer / search-intent / document-summary prompts for an OpenAI-compatible
endpoint. Kept for the fallback path only.
