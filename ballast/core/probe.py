"""ballast/core/probe.py — Post-execution environment probe.

Public interface:
    verify_node_claim(node, label, spec) -> tuple[bool, str]
        — Makes one LLM call to check whether a PROGRESSING node's tool args
          violate any spec constraint. Returns (True, "") on pass, (False, note)
          on breach, (True, "probe_error: ...") on any exception.
    ProbePacket
        — Typed input envelope passed to _call_probe_agent().

Fail-safe: _call_probe_agent() never raises. Any exception → (True, "probe_error: ...").
The probe is supplemental to score_drift(). Fail-open is intentional.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent

from ballast.core.constants import HAIKU_MODEL
from ballast.core.spec import SpecModel

logger = logging.getLogger(__name__)

_PROBE_SYSTEM = (
    "You are a constraint auditor for Ballast, an AI agent guardrail system. "
    "An agent has just executed a tool call. "
    "Your job: determine whether the tool name and arguments violate any of the "
    "listed hard constraints. "
    "Respond ONLY with valid JSON: "
    '{"verified": true} if no constraint is violated, or '
    '{"verified": false, "note": "<which constraint was breached and why>"} '
    "if a constraint is clearly violated. "
    "Be strict: if the tool args unambiguously match a constraint violation, flag it. "
    "If unsure, return verified: true."
)

# Lazy singleton — NOT constructed at module level.
# Constructing Agent(model=...) at import time raises UserError if ANTHROPIC_API_KEY
# is absent, which breaks pytest -m 'not integration' collection.
_probe_agent: "Agent | None" = None


def _get_probe_agent() -> Agent:
    global _probe_agent
    if _probe_agent is None:
        _probe_agent = Agent(model=HAIKU_MODEL, system_prompt=_PROBE_SYSTEM)
    return _probe_agent


# ---------------------------------------------------------------------------
# ProbePacket — typed input envelope
# ---------------------------------------------------------------------------

@dataclass
class ProbePacket:
    """Structured input passed to _call_probe_agent().

    tool_args is JSON-serialised str for prompt safety (avoids nested dict formatting).
    tool_result is the node content excerpt (max 500 chars).
    """

    tool_name: str
    tool_args: str                            # JSON-serialised
    tool_result: str
    spec_constraints: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# _get_tool_info — minimal duck-typed extraction (no trajectory.py import)
# ---------------------------------------------------------------------------

def _get_tool_info(node: Any) -> tuple[str, dict, str]:
    """Extract (tool_name, tool_args, content) from a pydantic-ai node.

    Minimal version — covers direct-attr and parts-scan paths only.
    Does not import from trajectory.py (circular: trajectory imports probe).
    Returns ("", {}, "") if no tool call is found.
    """
    tool_name = ""
    tool_args: dict = {}
    content = ""

    # Direct attributes (some pydantic-ai versions)
    if hasattr(node, "tool_name") and hasattr(node, "args"):
        tool_name = str(node.tool_name)
        args_raw = getattr(node, "args", {})
        tool_args = args_raw if isinstance(args_raw, dict) else {}

    # Scan parts (ModelResponse with ToolCallPart)
    if not tool_name:
        for part in getattr(node, "parts", []) or []:
            if type(part).__name__ in ("ToolCallPart", "ToolCall", "FunctionCall"):
                t_name = str(getattr(part, "tool_name", getattr(part, "function_name", "")))
                t_args = getattr(part, "args", getattr(part, "arguments", {}))
                if t_name:
                    tool_name = t_name
                    tool_args = t_args if isinstance(t_args, dict) else {}
                    break

    # Content extraction
    for attr in ("text", "content", "output"):
        val = getattr(node, attr, None)
        if val and isinstance(val, str):
            content = val[:500]
            break

    return tool_name, tool_args, content


# ---------------------------------------------------------------------------
# _call_probe_agent — async, never raises
# ---------------------------------------------------------------------------

async def _call_probe_agent(agent: Agent, packet: ProbePacket) -> dict:
    """Call the probe agent. Returns parsed dict or fail-open dict on any exception.

    Async because agent.run() is async-native. run_sync() must NOT be used here:
    _call_probe_agent is always called from verify_node_claim() which is always
    called from the async run_with_spec() loop.

    Never raises. Any exception → {"verified": True, "note": "probe_error: <exc>"}.
    """
    constraints_block = (
        "\n".join(f"  - {c}" for c in packet.spec_constraints)
        if packet.spec_constraints
        else "  (none)"
    )
    prompt = (
        f"TOOL CALL\n"
        f"  name: {packet.tool_name!r}\n"
        f"  args: {packet.tool_args}\n\n"
        f"TOOL RESULT (excerpt)\n"
        f"  {packet.tool_result[:400]}\n\n"
        f"SPEC CONSTRAINTS\n"
        f"{constraints_block}"
    )
    try:
        result = await agent.run(prompt)
        raw = result.output if hasattr(result, "output") else str(result)
        parsed = json.loads(raw)
        # Normalise: ensure both keys exist
        return {
            "verified": bool(parsed.get("verified", True)),
            "note": str(parsed.get("note", "")),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "probe_agent_failed tool=%r exc=%s — failing open",
            packet.tool_name,
            exc,
        )
        return {"verified": True, "note": f"probe_error: {exc}"}


# ---------------------------------------------------------------------------
# verify_node_claim — public async entry point
# ---------------------------------------------------------------------------

async def verify_node_claim(
    node: Any,
    label: str,
    spec: SpecModel,
) -> tuple[bool, str]:
    """Probe whether a PROGRESSING node's tool args violate spec constraints.

    Args:
        node:   Raw pydantic-ai Agent.iter node.
        label:  DriftLabel assigned by score_drift() — caller should only call
                this for PROGRESSING nodes, but the function is safe for any label.
        spec:   Active locked SpecModel.

    Returns:
        (True, "")                    — probe passed, no constraint violation.
        (True, "no tool call")        — node has no tool call; nothing to verify.
        (True, "probe_error: <exc>")  — probe agent failed; fail-open.
        (False, "<note>")             — constraint violation detected.
    """
    tool_name, tool_args, content = _get_tool_info(node)

    if not tool_name:
        return True, "no tool call"

    packet = ProbePacket(
        tool_name=tool_name,
        tool_args=json.dumps(tool_args, default=str)[:400],
        tool_result=content,
        spec_constraints=list(spec.constraints),
    )

    result = await _call_probe_agent(_get_probe_agent(), packet)
    return result["verified"], result["note"]
