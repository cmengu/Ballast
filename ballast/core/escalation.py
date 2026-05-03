"""ballast/core/escalation.py — Three-level escalation chain for irreversible violations.

Public interface:
    escalate(assessment, spec, context, *, run_id, node_index) -> str
        — Walks Broker → CEO → Human. Returns resolution string or raises EscalationFailed.
    EscalationFailed
        — Raised when all levels exhaust without resolution. Carries .assessment and .spec.
    EscalationPacket
        — Typed input envelope passed to every level.

Chain behaviour:
    _call_level() never raises. Any exception → {"escalate": True}.
    Level 3 (Human) always raises EscalationFailed — it is the ceiling.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent

from ballast.core.agent_output import agent_run_result_payload
from ballast.core.constants import HAIKU_MODEL
from ballast.core.node_tools import extract_node_info
from ballast.core.spec import SpecModel

if TYPE_CHECKING:
    from ballast.core.trajectory import NodeAssessment

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```")


def _extract_json(raw: str) -> str:
    """Strip markdown code fences that LLMs sometimes wrap JSON in."""
    m = _JSON_FENCE_RE.search(raw)
    return m.group(1) if m else raw


_BROKER_SYSTEM = (
    "You are Broker, a spec-compliance reviewer for an AI agent system called Ballast. "
    "An agent has taken an irreversible action that violates its spec. "
    "Your job: decide if you can provide a corrective instruction that gets the agent back on track, "
    "or if this requires escalation to a higher authority. "
    "Respond ONLY with valid JSON: "
    '{"escalate": false, "resolution": "<corrective instruction>"} '
    'or {"escalate": true}. '
    "Be decisive. If unsure, escalate."
)

_CEO_SYSTEM = (
    "You are CEO, the final automated authority in Ballast's escalation chain. "
    "A Broker-level reviewer has already decided this violation requires escalation. "
    "You have full authority to override, redirect, or accept the violation. "
    "Your job: provide a definitive corrective instruction or confirm escalation to a human. "
    "Respond ONLY with valid JSON: "
    '{"escalate": false, "resolution": "<definitive corrective instruction>"} '
    'or {"escalate": true}. '
    "Only escalate if you genuinely cannot provide a resolution."
)

# Agents are constructed lazily — NOT at module level — so importing this
# module in tests does not require ANTHROPIC_API_KEY to be set.
# Consistent with trajectory.py's _get_judge_client() lazy-singleton pattern.
_broker_agent: "Agent | None" = None
_ceo_agent: "Agent | None" = None


def _get_broker_agent() -> Agent:
    global _broker_agent
    if _broker_agent is None:
        _broker_agent = Agent(model=HAIKU_MODEL, system_prompt=_BROKER_SYSTEM)
    return _broker_agent


def _get_ceo_agent() -> Agent:
    global _ceo_agent
    if _ceo_agent is None:
        _ceo_agent = Agent(model=HAIKU_MODEL, system_prompt=_CEO_SYSTEM)
    return _ceo_agent


# ---------------------------------------------------------------------------
# EscalationPacket — typed input envelope
# ---------------------------------------------------------------------------

@dataclass
class EscalationPacket:
    """Structured input passed to every escalation level.

    All fields are set at construction in escalate(); levels treat this as
    read-only. run_id and node_index are for logging only.
    """

    assessment: "NodeAssessment"
    spec: SpecModel
    context: list[Any]
    run_id: str = field(default="")
    node_index: int = field(default=0)


# ---------------------------------------------------------------------------
# EscalationFailed — raised when all levels exhaust
# ---------------------------------------------------------------------------

class EscalationFailed(Exception):
    """Raised by escalate() when all automated levels fail to resolve.

    Callers should write a checkpoint and raise HardInterrupt.
    """

    def __init__(self, assessment: "NodeAssessment", spec: SpecModel) -> None:
        self.assessment = assessment
        self.spec = spec
        super().__init__(
            f"escalation chain exhausted: tool={assessment.tool_name!r} "
            f"spec_version={spec.version_hash[:8]}"
        )


# ---------------------------------------------------------------------------
# JSON flag coercion — LLMs may return string "false" (truthy in Python)
# ---------------------------------------------------------------------------

def _escalate_continue_up(result: dict) -> bool:
    """True → escalate to next level; False → this level produced a resolution."""
    if "escalate" not in result:
        return True
    v = result["escalate"]
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("false", "0", "no", "off"):
            return False
        if s in ("true", "1", "yes", "on"):
            return True
        return True
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0
    return True


# ---------------------------------------------------------------------------
# _call_level — async, never raises
# ---------------------------------------------------------------------------

async def _call_level(agent: Agent, packet: EscalationPacket) -> dict:
    """Call one escalation level. Returns parsed dict or {"escalate": True} on any failure.

    Async because agent.run() is async-native. run_sync() must NOT be used here:
    _call_level is always called from escalate() which is always called from the
    async run_with_spec() loop — run_sync() would raise RuntimeError (event loop
    already running).

    Never raises. Any exception (LLM error, parse error, network error) is treated
    as an implicit escalation signal so the chain always continues upward.
    """
    try:
        harness = packet.spec.harness
        ctx_n = int(getattr(harness, "context_window_size", 0) or 0)
        ctx_slice = packet.context[-ctx_n:] if ctx_n > 0 else []

        # Compact raw pydantic-ai nodes to readable dict summaries.
        # Dict entries (compact_history) are already in the right shape.
        def _compact_ctx(entry: Any) -> str:
            if isinstance(entry, dict):
                tool = entry.get("tool_name", "?")
                label = entry.get("label", "?")
                score = entry.get("score", 0.0)
                summary = entry.get("summary", "")[:120]
                return f"tool={tool!r} label={label} score={score:.3f} summary={summary!r}"
            # Raw pydantic-ai node — duck-type extract essentials
            _, content, tool_info = extract_node_info(entry)
            tool = tool_info.get("tool_name", "?")
            return f"tool={tool!r} content={content[:120]!r}"

        ctx_lines = "\n".join(f"  [{i}] {_compact_ctx(m)}" for i, m in enumerate(ctx_slice))
        prompt = (
            f"ASSESSMENT\n"
            f"  tool: {packet.assessment.tool_name!r}\n"
            f"  score: {packet.assessment.score:.3f}\n"
            f"  label: {packet.assessment.label}\n"
            f"  rationale: {packet.assessment.rationale}\n\n"
            f"SPEC INTENT\n  {packet.spec.intent[:400]}\n\n"
            f"SPEC VERSION\n  {packet.spec.version_hash[:8]}\n\n"
            f"RUN CONTEXT\n  run_id={packet.run_id}  node_index={packet.node_index}\n\n"
            f"CONTEXT WINDOW (last {len(ctx_slice)} of {len(packet.context)} messages)\n"
            + ctx_lines
        )
        result = await agent.run(prompt)
        raw = agent_run_result_payload(result)
        if not isinstance(raw, str):
            raw = str(raw)
        parsed = json.loads(_extract_json(raw))
        if not isinstance(parsed, dict):
            logger.warning(
                "escalation_level_non_dict agent=%s type=%s — treating as escalate",
                agent.__class__.__name__,
                type(parsed).__name__,
            )
            return {"escalate": True}
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "escalation_level_failed agent=%s exc_type=%s — treating as escalate",
            agent.__class__.__name__,
            type(exc).__name__,
            exc_info=True,
        )
        return {"escalate": True}


# ---------------------------------------------------------------------------
# escalate — public async entry point
# ---------------------------------------------------------------------------

async def escalate(
    assessment: "NodeAssessment",
    spec: SpecModel,
    context: list[Any],
    *,
    run_id: str = "",
    node_index: int = 0,
) -> str:
    """Walk the escalation chain. Return resolution string or raise EscalationFailed.

    Chain: Broker → CEO → Human (EscalationFailed).

    Args:
        assessment:  NodeAssessment that triggered escalation.
        spec:        Active locked SpecModel.
        context:     Node conversation history for LLM context.
        run_id:      For logging. Optional.
        node_index:  For logging. Optional.

    Returns:
        Resolution string to inject into the agent's message history.

    Raises:
        EscalationFailed: when all automated levels fail to resolve.
    """
    packet = EscalationPacket(
        assessment=assessment,
        spec=spec,
        context=context,
        run_id=run_id,
        node_index=node_index,
    )

    # Level 1 — Broker
    broker_result = await _call_level(_get_broker_agent(), packet)
    if not _escalate_continue_up(broker_result):
        resolution = broker_result.get("resolution", "")
        if resolution:
            logger.info(
                "escalation_resolved_broker node=%d run_id=%s",
                node_index,
                run_id,
            )
            return resolution
        # Broker said "don't escalate" but gave no resolution text — treat as
        # an implicit escalate so CEO gets a chance rather than returning empty.
        logger.warning(
            "escalation_broker_empty_resolution node=%d run_id=%s — escalating to CEO",
            node_index,
            run_id,
        )

    logger.info("escalation_broker_escalated node=%d run_id=%s", node_index, run_id)

    # Level 2 — CEO
    ceo_result = await _call_level(_get_ceo_agent(), packet)
    if not _escalate_continue_up(ceo_result):
        resolution = ceo_result.get("resolution", "")
        if resolution:
            logger.info(
                "escalation_resolved_ceo node=%d run_id=%s",
                node_index,
                run_id,
            )
            return resolution
        # Same policy: CEO said resolve but gave no text — fall to Human level.
        logger.warning(
            "escalation_ceo_empty_resolution node=%d run_id=%s — escalating to Human",
            node_index,
            run_id,
        )

    logger.warning(
        "escalation_chain_exhausted node=%d tool=%s run_id=%s",
        node_index,
        assessment.tool_name,
        run_id,
    )

    # Level 3 — Human (ceiling — always raises)
    raise EscalationFailed(assessment, spec)
