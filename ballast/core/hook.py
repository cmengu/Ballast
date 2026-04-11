"""ballast/core/hook.py — Agent iteration hook with live spec injection.

Public interface:
    run_with_live_spec(agent, task, spec, poller, on_node=None)

Wires Agent.iter + SpecPoller + SpecDelta injection:
    - At every node boundary: poll for spec update
    - On spec change: inject SpecDelta.as_injection() into message_history
    - Stamp every node in the audit log with active spec_hash + node_type
    - Print: "node 00 | spec:a3f2xxxx | NodeTypeName"
    - Call optional async on_node(node_index, node, active_spec, delta) callback

Returns:
    (output, audit_log)
    audit_log: list of {node_index, spec_hash, node_type, delta_injected}

Injection mechanism (confirmed against pydantic-ai source):
    run.ctx.state.message_history.append(
        ModelRequest(parts=[UserPromptPart(content=injection)])
    )
    Path: AgentRun.ctx → GraphRunContext → .state (GraphAgentState) → .message_history
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, UserPromptPart

from ballast.core.spec import SpecDelta, SpecModel
from ballast.core.sync import SpecPoller

logger = logging.getLogger(__name__)


async def run_with_live_spec(
    agent: Agent,
    task: str,
    spec: SpecModel,
    poller: SpecPoller,
    on_node: Optional[Callable] = None,
) -> tuple[Any, list[dict]]:
    """Run agent with live spec polling at every node boundary.

    Polls poller at every node. On spec version change:
      - computes SpecDelta via active_spec.diff(new_spec)
      - injects delta.as_injection() into run.ctx.state.message_history
      - updates active_spec to the new spec

    Args:
        agent:    A pydantic-ai Agent instance.
        task:     The task string to run.
        spec:     A locked SpecModel — used as initial active spec.
        poller:   Initialised SpecPoller (set_initial already called by caller).
        on_node:  Optional async callback: fn(node_index, node, active_spec, delta).
                  delta is None if no spec update occurred at this node.

    Returns:
        (output, audit_log)
        Each audit_log entry: {node_index, spec_hash, node_type, delta_injected}
        delta_injected: "fromhash→tohash" string, or None if no update at that node.
    """
    active_spec = spec
    node_index = 0
    audit_log: list[dict] = []

    async with agent.iter(task) as run:
        async for node in run:
            # Poll for spec update at every node boundary
            delta: Optional[SpecDelta] = None
            new_spec = poller.poll()
            if new_spec:
                delta = active_spec.diff(new_spec)
                active_spec = new_spec
                injection = delta.as_injection()
                run.ctx.state.message_history.append(
                    ModelRequest(parts=[UserPromptPart(content=injection)])
                )

            # Stamp this node in the audit log
            audit_log.append({
                "node_index": node_index,
                "spec_hash": active_spec.version_hash,
                "node_type": type(node).__name__,
                "delta_injected": (
                    f"{delta.from_hash[:8]}→{delta.to_hash[:8]}"
                    if delta else None
                ),
            })

            print(
                f"  node {node_index:02d} | spec:{active_spec.version_hash[:8]}"
                f" | {type(node).__name__}"
            )

            if on_node:
                await on_node(node_index, node, active_spec, delta)

            node_index += 1

    return run.result.output, audit_log
