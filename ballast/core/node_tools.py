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

    Tool extraction policy — worst-case (fail-closed) for multi-tool nodes:
        1. Collect all ToolCallPart/ToolCall/FunctionCall parts from every container.
        2. Every invocation is preserved — same-name calls with different args are
           NOT deduplicated, so a violating second invocation is never hidden.
        3. If more than one invocation is found, flag with 'multi_tool': True so
           callers (allowed_tools checks, compliance scorers, probe) apply
           worst-case logic.
        4. Direct node.tool_name + node.args attributes are always included.

    Callers that care about multi-tool compliance should check tool_info.get('all_tools').
    """
    node_type = type(node).__name__
    content = ""
    all_tools: list[dict] = []

    if hasattr(node, "tool_name") and hasattr(node, "args"):
        args_raw = getattr(node, "args", {})
        t_name = str(node.tool_name)
        if t_name:
            all_tools.append({
                "tool_name": t_name,
                "tool_args": normalize_tool_args(args_raw),
            })

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
                if t_name:
                    all_tools.append({
                        "tool_name": t_name,
                        "tool_args": normalize_tool_args(t_args),
                    })

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
                    if t_name:
                        all_tools.append({
                            "tool_name": t_name,
                            "tool_args": normalize_tool_args(t_args),
                        })

    # Keep every invocation; do NOT deduplicate by name — two calls to the same
    # tool with different args are distinct and the later one can be violating.
    tool_info: dict = {}
    if all_tools:
        tool_info = dict(all_tools[0])  # first invocation as primary
        tool_info["all_tools"] = all_tools
        if len(all_tools) > 1:
            tool_info["multi_tool"] = True

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
