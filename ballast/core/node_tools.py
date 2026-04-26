"""ballast/core/node_tools.py — Shared duck-typed node extraction.

Used by trajectory.py (scoring), probe.py, and evaluator.py. trajectory imports
this module; probe/evaluator must not import trajectory (circular).
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


def extract_node_info(node: Any) -> tuple[str, str, dict]:
    """Extract (node_type_name, content, tool_info) from a pydantic-ai Agent.iter node.

    Mirrors the full duck-typed paths used for Layer-1 scoring (nested request/response,
    parts/messages). tool_info is {'tool_name': str, 'tool_args': dict} or {}.

    Tool extraction policy — first match wins:
        1. Direct node.tool_name + node.args attributes
        2. First ToolCallPart/ToolCall/FunctionCall found in node.parts or node.messages
        3. Same search inside node.request and node.response wrappers
    Only the first tool call found is returned. Nodes with multiple tool parts
    are uncommon in pydantic-ai; the first-wins policy is documented deliberately.
    """
    node_type = type(node).__name__
    content = ""
    tool_info: dict = {}

    if hasattr(node, "tool_name") and hasattr(node, "args"):
        args_raw = getattr(node, "args", {})
        tool_info = {
            "tool_name": str(node.tool_name),
            "tool_args": normalize_tool_args(args_raw),
        }

    for container_attr in ("parts", "messages"):
        container = getattr(node, container_attr, None) or []
        if not hasattr(container, "__iter__"):
            continue
        for part in container:
            part_type_name = type(part).__name__
            if part_type_name in ("ToolCallPart", "ToolCall", "FunctionCall"):
                t_name = str(
                    getattr(part, "tool_name", getattr(part, "function_name", ""))
                )
                t_args = getattr(part, "args", getattr(part, "arguments", {}))
                if t_name and not tool_info:
                    tool_info = {
                        "tool_name": t_name,
                        "tool_args": normalize_tool_args(t_args),
                    }

    for wrapper_attr in ("request", "response"):
        wrapper = getattr(node, wrapper_attr, None)
        if not wrapper:
            continue
        for container_attr in ("parts", "messages"):
            container = getattr(wrapper, container_attr, None) or []
            if not hasattr(container, "__iter__"):
                continue
            for part in container:
                part_type_name = type(part).__name__
                if part_type_name in ("ToolCallPart", "ToolCall", "FunctionCall"):
                    t_name = str(
                        getattr(part, "tool_name", getattr(part, "function_name", ""))
                    )
                    t_args = getattr(part, "args", getattr(part, "arguments", {}))
                    if t_name and not tool_info:
                        tool_info = {
                            "tool_name": t_name,
                            "tool_args": normalize_tool_args(t_args),
                        }

    for attr in ("text", "content", "output"):
        val = getattr(node, attr, None)
        if val and isinstance(val, str):
            content = val[:1000]
            break

    if not content:
        for container_attr in ("parts", "messages"):
            container = getattr(node, container_attr, None) or []
            if not hasattr(container, "__iter__"):
                continue
            texts = []
            for part in container:
                for pattr in ("text", "content"):
                    val = getattr(part, pattr, None)
                    if val and isinstance(val, str):
                        texts.append(val)
            if texts:
                content = "\n".join(texts)[:1000]
                break

    if not content:
        for wrapper_attr in ("response", "request"):
            wrapper = getattr(node, wrapper_attr, None)
            if not wrapper:
                continue
            for attr in ("text", "content"):
                val = getattr(wrapper, attr, None)
                if val and isinstance(val, str):
                    content = val[:1000]
                    break

    return node_type, content, tool_info


def duck_tool_info(node: Any, *, content_max: int = 600) -> tuple[str, dict, str]:
    """Extract (tool_name, tool_args, content) for probe/evaluator prompts."""
    _, content, ti = extract_node_info(node)
    tool_name = ti.get("tool_name", "")
    tool_args = normalize_tool_args(ti.get("tool_args"))
    return tool_name, tool_args, content[:content_max]
