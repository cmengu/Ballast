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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent

from ballast.core.constants import HAIKU_MODEL
from ballast.core.spec import SpecModel

if TYPE_CHECKING:
    from ballast.core.trajectory import NodeAssessment

logger = logging.getLogger(__name__)

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
    ctx_n = packet.spec.harness.context_window_size
    ctx_slice = packet.context[-ctx_n:] if ctx_n > 0 else []
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
        + "\n".join(str(m) for m in ctx_slice)
    )
    try:
        result = await agent.run(prompt)
        raw = result.output if hasattr(result, "output") else str(result)
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "escalation_level_failed agent=%s exc=%s — treating as escalate",
            agent.__class__.__name__,
            exc,
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
    if not broker_result.get("escalate", True):
        resolution = broker_result.get("resolution", "")
        if resolution:
            logger.info(
                "escalation_resolved_broker node=%d run_id=%s",
                node_index,
                run_id,
            )
            return resolution

    logger.info("escalation_broker_escalated node=%d run_id=%s", node_index, run_id)

    # Level 2 — CEO
    ceo_result = await _call_level(_get_ceo_agent(), packet)
    if not ceo_result.get("escalate", True):
        resolution = ceo_result.get("resolution", "")
        if resolution:
            logger.info(
                "escalation_resolved_ceo node=%d run_id=%s",
                node_index,
                run_id,
            )
            return resolution

    logger.warning(
        "escalation_chain_exhausted node=%d tool=%s run_id=%s",
        node_index,
        assessment.tool_name,
        run_id,
    )

    # Level 3 — Human (ceiling — always raises)
    raise EscalationFailed(assessment, spec)
