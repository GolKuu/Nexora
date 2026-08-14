"""Safety layer (§44, §45, §66).

Three separate jobs, deliberately not merged:

1. **Isolation.** Untrusted text - retrieved documents, tool payloads, browser
   observations - is wrapped in attributed blocks and never concatenated into
   the system prompt. Detected injection attempts are annotated so the answer
   can mention them rather than silently obeying or silently dropping them.
2. **Secret hygiene.** Nothing that looks like a credential reaches the model,
   and nothing that looks like one leaves it.
3. **Investment-product safety.** An answer containing "гарантированно
   заработаете" is a defect regardless of how well it scored on anything else.
   ``check_answer`` is called on every generation, and the server refuses to
   return a violating answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ai.prompts.system import FORBIDDEN_PHRASES
from ai.retrieval.context_builder import redact

#: Patterns that mean "this text is trying to talk to the model".
INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"игнорируй\s+(?:все\s+)?(?:предыдущие|прошлые|верхние)\s+инструкц", re.I),
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions", re.I),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior)\s+(?:rules|instructions)", re.I),
    re.compile(r"(?:системн\w+\s+промпт|system\s+prompt)", re.I),
    re.compile(r"ты\s+больше\s+не\s+(?:ассистент|kase)", re.I),
    re.compile(r"(?:^|\n)\s*(?:system|assistant)\s*:", re.I),
    re.compile(r"внимание\s+для\s+(?:ии|ai|ассистент)", re.I),
    re.compile(r"вызови\s+инструмент\s+\w+", re.I),
    re.compile(r"напиши,\s+что\s+.{0,40}гарантирован", re.I),
)

_SECRET_VALUE = re.compile(
    r"(postgres(?:ql)?://\S+)|(\bsk-[A-Za-z0-9]{16,})|(Bearer\s+[A-Za-z0-9._-]{16,})"
    r"|(\bAKIA[0-9A-Z]{16}\b)"
)


@dataclass(slots=True)
class SafetyReport:
    injection_detected: bool = False
    injection_matches: list[str] = field(default_factory=list)
    forbidden_phrases: list[str] = field(default_factory=list)
    secrets_removed: int = 0
    redacted_keys: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.forbidden_phrases and self.secrets_removed == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "injection_detected": self.injection_detected,
            "injection_matches": self.injection_matches[:5],
            "forbidden_phrases": self.forbidden_phrases,
            "secrets_removed": self.secrets_removed,
            "redacted_keys": self.redacted_keys,
        }


def scan_untrusted(text: str) -> SafetyReport:
    """Look for instruction-shaped content in data. Does not modify the text.

    The text is *not* stripped: removing the injection would hide it from the
    user. It stays in the document block, where the system prompt has already
    told the model that documents are data, and the report lets the answer say
    that an attempt was present (§45).
    """
    report = SafetyReport()
    for pattern in INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            report.injection_detected = True
            report.injection_matches.append(match.group(0)[:120])
    return report


def sanitise_context(payload: Any) -> tuple[Any, list[str]]:
    """Strip credentials from anything on its way into the model (§44)."""
    found: list[str] = []
    cleaned = redact(payload, found=found)
    return cleaned, sorted(set(found))


def check_answer(text: str) -> SafetyReport:
    """Validate a generated answer before it reaches a user (§66)."""
    report = SafetyReport()
    lowered = text.lower()
    report.forbidden_phrases = [
        phrase for phrase in FORBIDDEN_PHRASES if phrase in lowered
    ]
    report.secrets_removed = len(_SECRET_VALUE.findall(text))
    return report


def scrub_answer(text: str) -> tuple[str, SafetyReport]:
    """Remove leaked secrets from an answer; report forbidden phrasing.

    Secrets are removed because printing one causes harm on its own. A
    forbidden *phrase* is not silently rewritten - rewriting it would hide a
    behavioural regression that the release gate is supposed to catch (§65).
    The caller decides what to do; the server replaces the answer.
    """
    report = check_answer(text)
    if report.secrets_removed:
        text = _SECRET_VALUE.sub("[REDACTED]", text)
    return text, report


SAFE_FALLBACK_ANSWER = (
    "## Коротко\n"
    "Я не могу дать этот ответ в такой формулировке.\n\n"
    "## Почему\n"
    "Сформированный ответ содержал утверждение о гарантированной доходности или отсутствии "
    "риска. Такие утверждения неверны для любой облигации, поэтому ответ не выдаётся.\n\n"
    "## Что проверить\n"
    "Переформулируйте вопрос — я покажу доходность, реальную доходность после инфляции и риски "
    "по данным KASE."
)


__all__ = [
    "INJECTION_PATTERNS",
    "SAFE_FALLBACK_ANSWER",
    "SafetyReport",
    "check_answer",
    "sanitise_context",
    "scan_untrusted",
    "scrub_answer",
]
