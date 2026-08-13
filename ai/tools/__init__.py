"""The deterministic functions the model may call.

The model chooses *which* tool and *with what arguments*. It never produces the
numbers itself (§12).
"""

from ai.tools.registry import (
    TOOLS,
    TOOLS_BY_NAME,
    TOOL_NAMES,
    TOOLS_VERSION,
    ToolCallError,
    ToolSpec,
    parse_tool_call,
    render_tool_list,
    tool_schemas,
    validate_call,
)

__all__ = [
    "TOOLS",
    "TOOLS_BY_NAME",
    "TOOL_NAMES",
    "TOOLS_VERSION",
    "ToolCallError",
    "ToolSpec",
    "parse_tool_call",
    "render_tool_list",
    "tool_schemas",
    "validate_call",
]
