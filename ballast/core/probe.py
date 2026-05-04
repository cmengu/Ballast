"""ballast/core/probe.py — Post-execution environment probe.

Public interface:
    verify_node_claim(node, label, spec) -> tuple[bool, str]
        — Makes one LLM call to check whether a PROGRESSING node's tool args
          violate any spec constraint. Returns (True, "") on pass, (False, note)
          on breach.
    ProbePacket
        — Typed input envelope passed to _call_probe_agent().

Failure policy (two distinct cases):
  • Schema / key missing  — LLM responded but omitted 'verified' key or returned
    non-dict JSON. Treated as (False, "probe_error: ...") — fail-closed.
  • Transport / parse exception — LLM call raised (network, timeout, JSON error).
    Also treated as (False, "probe_error: ...") — fail-closed.

The probe is supplemental to score_drift(). Both hard failures and schema gaps
now fail closed so a broken probe cannot silently mark a claim as verified.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent

from ballast.core.agent_output import agent_run_result_payload
from ballast.core.constants import HAIKU_MODEL
from ballast.core.node_tools import duck_tool_info, extract_node_info
from ballast.core.spec import SpecModel

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```")


def _coerce_verified(val: object) -> bool:
    """Parse probe JSON verified field; strings like 'false' must not be truthy.

    Coercion policy:
        Explicit false set  → False   ("false", "0", "no", "off", int 0)
        Explicit true set   → True    ("true", "1", "yes", "on", int/bool ≠ 0)
        Unknown string      → False   (fail-closed: unknown ≠ verified)
        Non-string/non-bool → False   (safe default for unexpected types)
    Fail-closed on unknown input: if the LLM returns an unrecognised value we
    cannot confirm the claim, so we treat it as unverified.
    """
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val != 0
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("false", "0", "no", "off", ""):
            return False
        if s in ("true", "1", "yes", "on"):
            return True
        # Unknown string — log and fail-closed
        logger.warning(
            "_coerce_verified: unrecognised value %r — treating as unverified", val
        )
        return False
    return False


def _extract_json(raw: str) -> str:
    """Strip markdown code fences that LLMs sometimes wrap JSON in."""
    m = _JSON_FENCE_RE.search(raw)
    return m.group(1) if m else raw


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
    "If you cannot verify compliance with the constraints from the given facts, treat "
    "that as unverified: return {\"verified\": false, \"note\": \"cannot verify\"} "
    "(fail-closed — never guess \"verified\" when uncertain)."
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
# _get_tool_info — delegates to node_tools (shared with evaluator)
# ---------------------------------------------------------------------------

def _get_tool_info(node: Any) -> tuple[str, dict, str]:
    """Extract (tool_name, tool_args, content) from a pydantic-ai node."""
    return duck_tool_info(node, content_max=500)


# ---------------------------------------------------------------------------
# _call_probe_agent — async, never raises
# ---------------------------------------------------------------------------

async def _call_probe_agent(agent: Agent, packet: ProbePacket) -> dict:
    """Call the probe agent. Returns parsed dict or fail-closed dict on any exception.

    Async because agent.run() is async-native. run_sync() must NOT be used here:
    _call_probe_agent is always called from verify_node_claim() which is always
    called from the async run_with_spec() loop.

    Never raises. Any exception → {"verified": False, "note": "probe_error: <exc>"}.
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
        raw = agent_run_result_payload(result)
        if not isinstance(raw, str):
            raw = str(raw)
        parsed = json.loads(_extract_json(raw))
        if not isinstance(parsed, dict):
            return {"verified": False, "note": "probe_error: non-object JSON"}
        if "verified" not in parsed:
            logger.warning(
                "probe_response missing 'verified' key tool=%r — failing closed",
                packet.tool_name,
            )
        vraw = parsed.get("verified", False)
        return {
            "verified": _coerce_verified(vraw),
            "note": str(parsed.get("note", "")),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "probe_agent_failed tool=%r — failing closed",
            packet.tool_name,
            exc_info=True,
        )
        return {"verified": False, "note": f"probe_error: {type(exc).__name__}"}


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
        (True, "")                     — probe passed, no constraint violation.
        (True, "no tool call")         — node has no tool call; nothing to verify.
        (False, "probe_error: <exc>")  — probe agent failed; fail-closed.
        (False, "<note>")              — constraint violation detected.
    """
    _, content, tool_info = extract_node_info(node)
    tool_name = tool_info.get("tool_name", "")

    if not tool_name:
        return True, "no tool call"

    # Fail-closed for multi-tool nodes: the probe can only inspect one tool at a
    # time, so if the node batched multiple distinct tools, decline to verify and
    # return (False, ...) so the caller marks the node as VIOLATED rather than
    # silently passing a partially-checked multi-tool step.
    if tool_info.get("multi_tool"):
        logger.warning(
            "probe_multi_tool_fail_closed tool=%r (multi_tool=%r) — failing closed",
            tool_name,
            [t["tool_name"] for t in tool_info.get("all_tools", [])],
        )
        return False, "probe_multi_tool: cannot verify all tools in batched step"

    tool_args = tool_info.get("tool_args", {})
    packet = ProbePacket(
        tool_name=tool_name,
        tool_args=json.dumps(tool_args, default=str)[:400],
        tool_result=content,
        spec_constraints=list(spec.constraints),
    )

    result = await _call_probe_agent(_get_probe_agent(), packet)
    return result["verified"], result["note"]
