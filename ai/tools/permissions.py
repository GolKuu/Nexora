"""What the model is allowed to do (§46).

The permission model is deliberately boring: an allow-list of read-only tools,
checked before dispatch, plus a hard denial of every category of action that
could move money or change a stored value. There is no "admin mode" that
loosens it at runtime, because a prompt-injected document must not be able to
ask for one (§45).
"""

from __future__ import annotations

from dataclasses import dataclass

from ai.tools.registry import TOOL_NAMES, TOOLS_BY_NAME

#: Everything the assistant may call in the MVP. All read-only.
READ_ONLY_TOOLS: frozenset[str] = frozenset(TOOL_NAMES)

#: Capabilities that do not exist and must not be added by accident. If a tool
#: with any of these effects is ever introduced, it needs a human-in-the-loop
#: confirmation path in the product, not a model decision.
FORBIDDEN_CAPABILITIES: frozenset[str] = frozenset(
    {
        "place_order",
        "buy_bond",
        "sell_bond",
        "transfer_funds",
        "update_quote",
        "update_bond",
        "override_score",
        "write_financials",
        "delete_data",
        "run_sql",
        "execute_code",
        "http_request",
    }
)


class PermissionDenied(PermissionError):
    """The system refuses to run what the model asked for."""


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    allowed: frozenset[str] = READ_ONLY_TOOLS
    allow_write: bool = False
    max_calls_per_turn: int = 4

    def check(self, name: str, *, calls_so_far: int = 0) -> None:
        if name in FORBIDDEN_CAPABILITIES:
            raise PermissionDenied(
                f"{name!r} is a forbidden capability: the AI never trades, "
                f"never moves money and never edits source data."
            )
        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            raise PermissionDenied(f"{name!r} is not a registered tool")
        if name not in self.allowed:
            raise PermissionDenied(f"{name!r} is not permitted for this caller")
        if spec.mutates and not self.allow_write:
            raise PermissionDenied(f"{name!r} would mutate state; writes are disabled")
        if calls_so_far >= self.max_calls_per_turn:
            raise PermissionDenied(
                f"tool-call budget exhausted ({self.max_calls_per_turn} per turn)"
            )


DEFAULT_POLICY = ToolPolicy()

__all__ = [
    "DEFAULT_POLICY",
    "FORBIDDEN_CAPABILITIES",
    "PermissionDenied",
    "READ_ONLY_TOOLS",
    "ToolPolicy",
]
