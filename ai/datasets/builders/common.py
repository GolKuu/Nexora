"""Shared helpers for the task builders.

Every number that appears in a generated answer comes from
:class:`ai.tools.executors.ToolExecutor` - i.e. from the product's own
calculator and scoring engine (§59). No builder writes a figure by hand, and
`grounded_values` on each sample records the engine outputs so
``validate_dataset.py`` can re-derive them and fail the build if the dataset
ever drifts from the code.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date
from typing import Any, Iterable

from ai.datasets.schema import Provenance, SFTSample, Task
from ai.prompts.system import PROMPT_VERSION, assistant_system_prompt
from ai.tools.executors import ToolExecutor, ToolResult

from app.calculations.types import FORMULA_VERSION

#: Fixed so a rebuild of the same dataset version is byte-identical (§60).
SEED = 20260813


def rng(salt: str = "") -> random.Random:
    return random.Random(f"{SEED}:{salt}")


def sample_id(task: str, key: str) -> str:
    digest = hashlib.sha256(f"{task}|{key}".encode("utf-8")).hexdigest()[:12]
    return f"{task}-{digest}"


def make_sample(
    *,
    task: Task,
    key: str,
    messages: list[dict[str, Any]],
    provenance: Provenance,
    tags: Iterable[str] = (),
    grounded_values: dict[str, Any] | None = None,
    synthetic: bool = True,
    language: str = "ru",
) -> SFTSample:
    return SFTSample(
        sample_id=sample_id(task, key),
        task=task,
        messages=messages,
        provenance=provenance,
        synthetic=synthetic,
        language=language,
        tags=list(tags),
        grounded_values=grounded_values or {},
        prompt_version=PROMPT_VERSION,
        formula_version=FORMULA_VERSION,
    )


def kase_provenance(
    *,
    source_url: str | None,
    document_id: str | None = None,
    document_date: str | None = None,
    collected_at: str | None = None,
    derived: bool = False,
) -> Provenance:
    return Provenance(
        source="kase_public_api" if not derived else "kase_bond_ai_engine",
        source_url=source_url or "https://kase.kz/",
        document_id=document_id,
        document_date=document_date,
        collected_at=collected_at,
        license_status="public" if not derived else "derived",
        language="ru",
    )


def engine_provenance(source_url: str | None = None) -> Provenance:
    """For samples whose content is a system calculation, not a KASE fact."""
    return Provenance(
        source="kase_bond_ai_engine",
        source_url=source_url or "https://kase.kz/",
        license_status="derived",
        language="ru",
        document_date=date.today().isoformat(),
    )


# --------------------------------------------------------------------------
# Russian number formatting - matches what the product's UI shows.
# --------------------------------------------------------------------------

def money(value: float | None, currency: str = "KZT") -> str:
    if value is None:
        return "нет данных"
    symbol = {"KZT": "₸", "USD": "$", "EUR": "€"}.get(currency, currency)
    return f"{value:,.0f}".replace(",", " ") + f" {symbol}"


def pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "нет данных"
    return f"{value:.{digits}f}".rstrip("0").rstrip(".") + "%"


def years(value: float | None) -> str:
    if value is None:
        return "нет данных"
    whole = int(value)
    if abs(value - whole) < 0.05:
        return f"{whole} " + plural(whole, "год", "года", "лет")
    return f"{value:.1f} года".replace(".", ",")


def plural(count: int, one: str, few: str, many: str) -> str:
    count = abs(count) % 100
    if 11 <= count <= 14:
        return many
    last = count % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def amount_phrase(amount: float) -> str:
    """How a Kazakhstani retail user actually writes an amount."""
    if amount >= 1_000_000 and amount % 1_000_000 == 0:
        millions = int(amount // 1_000_000)
        return f"{millions} млн тенге"
    if amount >= 1_000 and amount % 1_000 == 0:
        return f"{int(amount // 1000)} тыс. тенге"
    return money(amount)


# --------------------------------------------------------------------------
# Source labelling (§18)
# --------------------------------------------------------------------------

def label(kind: str, text: str) -> str:
    mapping = {
        "FACT": "данные KASE",
        "CALCULATION": "расчет системы",
        "SCENARIO": "сценарий",
        "INTERPRETATION": "AI-интерпретация",
    }
    return f"{text} — {mapping.get(kind, kind)}"


def system_message(*, ui_mode: str = "simple", profile: str = "balanced") -> dict[str, str]:
    return {"role": "system", "content": assistant_system_prompt(ui_mode=ui_mode, profile=profile)}


def tool_turn(result: ToolResult) -> dict[str, str]:
    """The tool-result turn, exactly as the inference server will render it."""
    from ai.prompts.templates import tool_result_block
    import json

    return {
        "role": "user",
        "content": tool_result_block(
            result.tool,
            json.dumps(result.as_dict(), ensure_ascii=False, default=str),
        ),
    }


def liquid_bonds(executor: ToolExecutor, limit: int = 60) -> list[dict]:
    """The issues worth generating training data about.

    A bond with no quote produces samples whose answer is "нет данных" - which
    we do want, but only in the refusal builder and in controlled quantity.
    Here we take issues that actually trade, ranked by how much information we
    hold about them, so the instruction data is grounded in real market state.
    """
    rows: list[tuple[float, dict]] = []
    for bond in executor.store.bonds():
        quote = executor.store.quote(bond["ticker"])
        if not quote or quote.get("ytm") is None:
            continue
        if not bond.get("maturity_date") or bond["maturity_date"] <= executor.today:
            continue
        information = (
            (1 if executor.store.cashflows(bond["ticker"]) else 0)
            + (1 if executor.store.statements(bond.get("issuer_code") or "") else 0)
            + (1 if quote.get("ask") else 0)
            + min(1.0, (quote.get("number_of_trades") or 0) / 10.0)
        )
        rows.append((information, bond))
    rows.sort(key=lambda pair: -pair[0])
    return [bond for _, bond in rows[:limit]]


__all__ = [
    "SEED",
    "amount_phrase",
    "engine_provenance",
    "kase_provenance",
    "label",
    "liquid_bonds",
    "make_sample",
    "money",
    "pct",
    "plural",
    "rng",
    "sample_id",
    "system_message",
    "tool_turn",
    "years",
]
