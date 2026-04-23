"""ballast/core/node_tools.py — Shared duck-typed tool extraction for nodes.

Used by probe.py and evaluator.py without importing trajectory.py (circular).
"""
from __future__ import annotations

import json
from typing import Any


def normalize_tool_args(raw: Any) -> dict:
    """Coerce provider tool args to a dict (JSON object strings parsed)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def duck_tool_info(node: Any, *, content_max: int = 600) -> tuple[str, dict, str]:
    """Extract (tool_name, tool_args, content) from a pydantic-ai node."""
    tool_name = ""
    tool_args: dict = {}
    content = ""

    if hasattr(node, "tool_name") and hasattr(node, "args"):
        tool_name = str(node.tool_name)
        tool_args = normalize_tool_args(getattr(node, "args", {}))

    if not tool_name:
        for part in getattr(node, "parts", []) or []:
            if type(part).__name__ in ("ToolCallPart", "ToolCall", "FunctionCall"):
                t_name = str(
                    getattr(part, "tool_name", getattr(part, "function_name", ""))
                )
                t_args = getattr(part, "args", getattr(part, "arguments", {}))
                if t_name:
                    tool_name = t_name
                    tool_args = normalize_tool_args(t_args)
                    break

    for attr in ("text", "content", "output"):
        val = getattr(node, attr, None)
        if val and isinstance(val, str):
            content = val[:content_max]
            break

    return tool_name, tool_args, content
