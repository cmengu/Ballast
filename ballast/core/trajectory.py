"""ballast/core/trajectory.py — Node-boundary orchestration and drift detection.

Public interface:
    run_with_spec(agent, task, spec, poller=None, cost_guard=None, agent_id="default")
                      — 7-step orchestration loop; spec poll, drift score,
                        context window, checkpoint, correction inject.
    score_drift(node, full_window, spec, compact_history=None)
                      — Layer 1 cascade: returns NodeAssessment.
    TrajectoryChecker — single-node, fixed-spec drift scorer (simpler API).
    DriftResult       — scored assessment of one node (used by TrajectoryChecker).
    DriftDetected     — raised by TrajectoryChecker.check() when score < threshold.

Score dimensions (aggregate = min of all three):
    score_tool_compliance      — rule-based: is tool in allowed_tools?
    score_constraint_violation — LLM: did action breach a hard constraint?
    score_intent_alignment     — LLM: is action moving toward the goal?

Threshold: spec.drift_threshold (travels with the spec — invariant 2).
Interception: pydantic-ai Agent.iter node boundaries (duck-typed for version resilience).
"""
from __future__ import annotations

import asyncio
import functools
import logging
import math
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import anthropic
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, UserPromptPart

from ballast.adapters.otel import emit_drift_span
from ballast.core.agent_output import agent_run_result_payload
from ballast.core.constants import SONNET_MODEL
from ballast.core.node_tools import extract_node_info as _extract_node_info
from ballast.core.checkpoint import CHECKPOINT_FILE, BallastProgress, NodeSummary, _MAX_NODE_SUMMARIES
from ballast.core.cost import RunCostGuard
from ballast.core.escalation import EscalationFailed, escalate
from ballast.core.guardrails import HardInterrupt, build_correction, can_resume
from ballast.core.evaluator import evaluate_node
from ballast.core.probe import verify_node_claim
from ballast.core.spec import SpecModel, is_locked
from ballast.core.sync import SpecPoller

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DriftResult — scored assessment of one node
# ---------------------------------------------------------------------------

class DriftResult(BaseModel):
    """Complete scoring result for a single pydantic-ai Agent.iter node.

    Produced by TrajectoryChecker.check() on every scored node.
    Carried by DriftDetected when score < spec.drift_threshold.
    Consumed by guardrails.py for escalation policy decisions.
    """
    score: float = Field(
        ge=0.0, le=1.0,
        description="min(intent, tool, constraint). 0.0=complete drift, 1.0=aligned.",
    )
    intent_score: float = Field(ge=0.0, le=1.0)
    tool_score: float = Field(ge=0.0, le=1.0)
    constraint_score: float = Field(ge=0.0, le=1.0)
    failing_dimension: str = Field(
        description=(
            "'tool' | 'constraint' | 'intent' | 'none'. "
            "'none' when aggregate score >= drift_threshold; else priority tool > constraint > intent."
        )
    )
    node_type: str = Field(description="type(node).__name__ of the scored pydantic-ai node")
    spec_version: str = Field(description="SpecModel.version_hash — identifies spec in effect")
    raised_at_step: int = Field(description="1-indexed monotonic step counter")
    threshold: float = Field(description="spec.drift_threshold applied at this step")


# ---------------------------------------------------------------------------
# DriftDetected exception
# ---------------------------------------------------------------------------

class DriftDetected(Exception):
    """Raised by TrajectoryChecker.check() when drift score < spec.drift_threshold.

    Carries DriftResult so guardrails.py has full context.
    trajectory.py raises this. trajectory.py never silently swallows this.
    run_with_spec logs it then immediately re-raises — guardrails.py handles.
    """

    def __init__(self, result: DriftResult) -> None:
        self.result = result
        super().__init__(
            f"Drift at step {result.raised_at_step}: "
            f"score={result.score:.2f} failing={result.failing_dimension!r} "
            f"(intent={result.intent_score:.2f} "
            f"tool={result.tool_score:.2f} "
            f"constraint={result.constraint_score:.2f})"
        )


# ---------------------------------------------------------------------------
# Anthropic client (lazy singleton)
# ---------------------------------------------------------------------------

_judge_client: "anthropic.Anthropic | None" = None


def _get_judge_client() -> "anthropic.Anthropic":
    global _judge_client
    if _judge_client is None:
        _judge_client = anthropic.Anthropic()
    return _judge_client


# ---------------------------------------------------------------------------
# Scorer 1 — tool compliance (rule-based, NEVER calls LLM)
# ---------------------------------------------------------------------------

def score_tool_compliance(node: Any, spec: SpecModel) -> float:
    """Rule-based: is the tool used in spec.allowed_tools?

    Returns:
        1.0 — no tool call in this node, or allowed_tools=[] (all permitted)
        1.0 — tool_name is in allowed_tools
        0.0 — tool_name is NOT in allowed_tools (hard spec violation)

    Never raises. Never calls LLM. O(1) string membership check.
    """
    _, _, tool_info = _extract_node_info(node)
    tool_name = tool_info.get("tool_name", "")
    if not tool_name:
        return 1.0  # Not a tool call — compliance does not apply
    if not spec.allowed_tools:
        return 1.0  # Empty = all tools allowed
    return 1.0 if tool_name in spec.allowed_tools else 0.0


# ---------------------------------------------------------------------------
# Shared boolean coercion — handles string "false"/"true" from LLM tool outputs
# ---------------------------------------------------------------------------

def _coerce_bool(val: object, default: bool = False) -> bool:
    """Robustly coerce an LLM tool-output field to bool.

    Handles: actual bool, int (0/1), and string representations such as
    "true"/"false"/"yes"/"no" (case-insensitive). Anything unrecognised
    falls back to `default`.

    int handling: any nonzero int is True (Python semantics). This is
    intentional — an LLM returning `violation: 2` is still a violation.
    """
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return bool(val)
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("true", "yes", "1"):
            return True
        if s in ("false", "no", "0", ""):
            return False
    return default


# ---------------------------------------------------------------------------
# Scorer 2 — constraint violation (LLM, fail-closed 0.0)
# ---------------------------------------------------------------------------

_CONSTRAINT_SYSTEM = (
    "You are a constraint enforcement monitor for an AI agent mid-run. "
    "Determine whether a single agent action violates any of the stated hard constraints. "
    "Be strict: if an action could plausibly violate a constraint, flag it."
)

_CONSTRAINT_TOOL = {
    "name": "constraint_check",
    "description": "Determine if the agent action violates any hard constraint.",
    "input_schema": {
        "type": "object",
        "properties": {
            "violation": {
                "type": "boolean",
                "description": "True if any hard constraint is breached.",
            },
            "violated_constraint": {
                "type": "string",
                "description": "The exact constraint text breached, or empty string.",
            },
            "rationale": {
                "type": "string",
                "description": "One sentence explaining the decision.",
            },
        },
        "required": ["violation", "violated_constraint", "rationale"],
    },
}


def score_constraint_violation(node: Any, spec: SpecModel) -> float:
    """LLM-based: does this action breach a hard constraint in spec.constraints?

    Returns: 1.0 (no violation), 0.0 (violated or LLM error).
    Fail-closed: returns 0.0 on any API/parse error so guardrails are not
    weakened when the judge is unavailable. Never raises.
    """
    if not spec.constraints:
        return 1.0  # Nothing to violate

    _, content, tool_info = _extract_node_info(node)
    check_content = (
        f"Tool: {tool_info.get('tool_name', 'N/A')}\n"
        f"Args: {str(tool_info.get('tool_args', {}))[:400]}\n"
        f"Content: {content[:600]}"
    )

    constraints_text = "\n".join(f"- {c}" for c in spec.constraints)
    prompt = (
        f"Hard constraints:\n{constraints_text}\n\n"
        f"Agent action:\n{check_content}"
    )

    try:
        response = _get_judge_client().messages.create(
            model=SONNET_MODEL,
            max_tokens=200,
            system=_CONSTRAINT_SYSTEM,
            tools=[_CONSTRAINT_TOOL],
            tool_choice={"type": "tool", "name": "constraint_check"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use":
                return 0.0 if _coerce_bool(block.input.get("violation", False)) else 1.0
    except Exception as e:
        logger.warning(
            "constraint_scorer_failed node=%s — returning 0.0 (fail-closed): %s",
            type(node).__name__, e,
        )
    return 0.0  # Fail-closed: treat as violation when judge is unavailable


# ---------------------------------------------------------------------------
# Scorer 3 — intent alignment (LLM, fail-closed 0.0)
# ---------------------------------------------------------------------------

_INTENT_SYSTEM = (
    "You are a mid-run process supervisor for an AI agent. "
    "Score whether a single agent action is moving toward the stated goal.\n"
    "0.0 = actively working against the goal\n"
    "0.5 = neutral / tangential / unclear\n"
    "0.7 = relevant but indirect progress\n"
    "1.0 = directly advancing the goal\n"
    "Use the full range. Be strict: unclear actions score below 0.7."
)

_INTENT_TOOL = {
    "name": "score_intent",
    "description": "Score intent alignment of a single agent action.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "number",
                "description": "0.0 to 1.0 — alignment with the goal.",
            },
            "rationale": {
                "type": "string",
                "description": "One sentence explaining the score.",
            },
        },
        "required": ["score", "rationale"],
    },
}


def score_intent_alignment(node: Any, spec: SpecModel) -> float:
    """LLM-based: is this action moving toward the goal?

    Returns float in [0.0, 1.0].
    Fail-closed: returns 0.0 on any API/parse error so the aggregate stays
    in the ambiguous/VIOLATED zone rather than masking a real problem. Never raises.
    Empty nodes with no scoreable content return 1.0 (neutral pass-through —
    they have no evidence of misalignment either).
    """
    _, content, tool_info = _extract_node_info(node)
    scoreable = content or tool_info.get("tool_name", "")
    if not scoreable:
        return 1.0  # No scoreable content — no evidence of misalignment

    criteria = "\n".join(f"  - {c}" for c in spec.success_criteria)
    prompt = (
        f"Goal: {spec.intent}\n"
        f"Success criteria:\n{criteria}\n\n"
        f"Agent action (node type: {type(node).__name__}):\n"
        f"Tool: {tool_info.get('tool_name', 'N/A')}  "
        f"Args: {str(tool_info.get('tool_args', {}))[:200]}\n"
        f"Content: {content[:600]}"
    )

    try:
        response = _get_judge_client().messages.create(
            model=SONNET_MODEL,
            max_tokens=200,
            system=_INTENT_SYSTEM,
            tools=[_INTENT_TOOL],
            tool_choice={"type": "tool", "name": "score_intent"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use":
                score = float(block.input.get("score", 0.5))
                return max(0.0, min(1.0, score))
    except Exception as e:
        logger.warning(
            "intent_scorer_failed node=%s — returning 0.0 (fail-closed): %s",
            type(node).__name__, e,
        )
    return 0.0  # Fail-closed: treat as no-intent when judge is unavailable


# ---------------------------------------------------------------------------
# NodeAssessment — typed return value for score_drift()
# ---------------------------------------------------------------------------

@dataclass
class NodeAssessment:
    """Typed result returned by score_drift().

    Replaces the raw tuple[float, str, str] so callers access fields by name.
    tool_name is included so run_with_spec() does not need a second
    _extract_node_info() call after score_drift() returns.
    """
    score: float
    label: str          # DriftLabel: PROGRESSING | STALLED | VIOLATED | VIOLATED_IRREVERSIBLE
    rationale: str
    tool_score: float
    constraint_score: float
    intent_score: float
    tool_name: str      # empty string if node has no tool call


# ---------------------------------------------------------------------------
# DriftLabel — the cascade label system
# ---------------------------------------------------------------------------

DriftLabel = Literal["PROGRESSING", "STALLED", "VIOLATED", "VIOLATED_IRREVERSIBLE"]


def _run_scorers(node: Any, spec: SpecModel) -> tuple[float, float, float]:
    """Call all three scorers and return (tool_score, constraint_score, intent_score).

    Used by ``TrajectoryChecker.check()`` (and similar call sites) when all three
    dimensions are needed in one tuple.

    ``score_drift()`` does not call this helper: it runs ``score_tool_compliance``
    first for early-exit, then calls the LLM scorers separately to avoid redundant
    tool compliance work. Never raises — each scorer has its own fail-closed policy.
    """
    return (
        score_tool_compliance(node, spec),
        score_constraint_violation(node, spec),
        score_intent_alignment(node, spec),
    )


def score_drift(
    node: Any,
    full_window: list,
    spec: SpecModel,
    compact_history: list | None = None,
) -> NodeAssessment:
    """Layer 1 cascade: score a node and return a NodeAssessment.

    Step 1 — Heuristic gate (no LLM, ~0ms):
        irreversibility check (spec.irreversible_actions)
        tool compliance check (spec.allowed_tools)

    Step 2 — LLM scorers (Layer 1) + Layer 2 when ambiguous:
        score >= 0.85 → PROGRESSING  (skip Layer 2)
        score <= 0.25 → VIOLATED     (skip Layer 2)
        else          → evaluate_node if spec.harness.enable_layer2_judge else STALLED

    Args:
        compact_history: Optional list of compact dicts from prior evicted nodes.
            When provided (e.g. from run_with_spec), Layer 2 sees full sliding-window
            context, not only dict-shaped entries in full_window.

    Returns:
        NodeAssessment with score, label, rationale, per-scorer breakdown, tool_name.
    """
    _, content, tool_info = _extract_node_info(node)
    tool_name = tool_info.get("tool_name", "")

    # ── Heuristic gate ────────────────────────────────────────────────────
    if tool_name and spec.irreversible_actions and tool_name in spec.irreversible_actions:
        return NodeAssessment(
            score=0.0, label="VIOLATED_IRREVERSIBLE",
            rationale=f"irreversible tool: {tool_name}",
            tool_score=0.0, constraint_score=1.0, intent_score=1.0,
            tool_name=tool_name,
        )

    tool_score = score_tool_compliance(node, spec)
    if tool_score == 0.0:
        return NodeAssessment(
            score=0.0, label="VIOLATED",
            rationale=f"tool not in allowed_tools: {tool_name}",
            tool_score=0.0, constraint_score=1.0, intent_score=1.0,
            tool_name=tool_name,
        )

    if not content and not tool_name:
        # Empty nodes (e.g. pydantic-ai bookkeeping events) have no signal to score.
        # score=1.0 avoids dragging drift thresholds; label STALLED means
        # "no scoreable signal" (bookkeeping / empty), not "progress halted".
        return NodeAssessment(
            score=1.0, label="STALLED",
            rationale="no scoreable content",
            tool_score=1.0, constraint_score=1.0, intent_score=1.0,
            tool_name=tool_name,
        )

    # ── LLM scorers (Layer 1) ─────────────────────────────────────────────
    # tool_score was already computed above; skip it in _run_scorers to avoid
    # calling score_tool_compliance twice per node.
    constraint_score = score_constraint_violation(node, spec)
    intent_score = score_intent_alignment(node, spec)
    aggregate = min(tool_score, constraint_score, intent_score)

    # ── Label assignment (Layer 2 when ambiguous and harness allows) ───────
    # Layer 2 is triggered when aggregate is in the ambiguous zone.
    # evaluate_node uses a synchronous Anthropic client; callers in an async
    # event loop must offload it via asyncio.to_thread (see _score_drift_async).
    eval_note = ""
    if aggregate >= 0.85:
        label = "PROGRESSING"
    elif aggregate <= 0.25:
        label = "VIOLATED"
    elif spec.harness.enable_layer2_judge:
        layer2_ctx = _layer2_evaluator_context(compact_history, full_window)
        label, eval_note = evaluate_node(
            node, layer2_ctx, spec,
            tool_score=tool_score,
            constraint_score=constraint_score,
            intent_score=intent_score,
        )
    else:
        label = "STALLED"

    return NodeAssessment(
        score=round(aggregate, 4),
        label=label,
        rationale=(
            f"intent={intent_score:.2f} constraint={constraint_score:.2f} tool={tool_score:.2f}"
            + (f"; layer2={eval_note}" if eval_note else "")
        ),
        tool_score=tool_score,
        constraint_score=constraint_score,
        intent_score=intent_score,
        tool_name=tool_name,
    )


def _compact_node(
    node: Any,
    score: float,
    label: str,
    cost_usd: float,
    verified: bool,
) -> dict:
    """Compact an evicted node to a summary dict for compact_history.

    compact_history is the portion of the context window beyond full_window.
    Passed as context to the Layer 2 evaluator and escalation (Step 7).
    """
    _, content, tool_info = _extract_node_info(node)
    return {
        "tool_name": tool_info.get("tool_name", ""),
        "label": label,
        "score": round(score, 3),
        "cost_usd": cost_usd,
        "verified": verified,
        "summary": content[:200],
    }


def _layer2_evaluator_context(compact_history: list | None, full_window: list) -> list[dict]:
    """Build dict summaries for Layer 2: evicted nodes plus raw nodes still in full_window.

    evaluate_node() only consumes dict rows (tool_name, label, score, …). compact_history
    already holds dicts from _compact_node; full_window still holds raw pydantic-ai nodes,
    which are compacted on the fly.

    Raw pydantic-ai nodes that have not yet been scored are compacted with explicit
    "UNSCORED" label and score=0.5 (neutral mid-point) so the Layer-2 judge can see
    their tool/content without being biased by false-positive PROGRESSING/1.0 placeholders.
    """
    out: list[dict] = []
    for n in compact_history or []:
        if isinstance(n, dict):
            out.append(n)
    for n in full_window:
        if isinstance(n, dict):
            out.append(n)
        else:
            out.append(_compact_node(n, 0.5, "UNSCORED", 0.0, False))
    return out


# ---------------------------------------------------------------------------
# Node scoreability — duck-typed, version-resilient
# ---------------------------------------------------------------------------

# Class name substrings that indicate a node worth scoring.
# Covers common pydantic-ai node naming conventions across versions.
# UPDATE THIS SET if Step 3 inspection reveals different class names.
_SCOREABLE_NAME_FRAGMENTS = frozenset({
    "ModelRequest", "ModelResponse", "ToolCall", "ToolReturn",
    "CallTools", "FunctionCall", "FunctionReturn",
    "UserPrompt",
})


def _is_scoreable(node: Any) -> bool:
    """Return True if this node should be scored by TrajectoryChecker.

    Checks node class name substrings AND duck-typing attribute presence.
    Resilient to pydantic-ai version differences.
    """
    name = type(node).__name__
    # Known pydantic-ai node type fragments
    if any(frag in name for frag in _SCOREABLE_NAME_FRAGMENTS):
        return True
    # Duck-typing fallback: nodes with tool_name or text are always scoreable
    if hasattr(node, "tool_name") or hasattr(node, "text") or hasattr(node, "content"):
        return True
    return False


# ---------------------------------------------------------------------------
# TrajectoryChecker — the public interface for per-node drift scoring
# ---------------------------------------------------------------------------

class TrajectoryChecker:
    """Mid-run drift detector. Initialised with a locked SpecModel.

    Call check(node) at every node from Agent.iter.

    Key invariants:
        - Requires a locked SpecModel (is_locked(spec) must be True)
        - Never catches DriftDetected internally — always propagates
        - Never modifies spec — read-only consumer
        - Never writes to memory — caller decides what to persist
    """

    def __init__(self, spec: SpecModel) -> None:
        if not is_locked(spec):
            raise ValueError(
                "TrajectoryChecker requires a locked SpecModel. "
                "Call lock(spec) before passing to TrajectoryChecker."
            )
        self.spec = spec
        self._step: int = 0
        self._full_window: list = []  # sliding window for Layer-2 context

    def check(self, node: Any) -> Optional[DriftResult]:
        """Score a single pydantic-ai Agent.iter node against the locked spec.

        Returns DriftResult if scored and aggregate >= threshold.
        Returns None if node is not scoreable (type not in _SCOREABLE_NAME_FRAGMENTS
        and has no scoreable attributes), or has no extractable content.

        Raises DriftDetected when aggregate score < spec.drift_threshold.
        DriftDetected is NEVER caught here — always propagates to the caller.

        Layer-2 note: when the aggregate is in the ambiguous zone (0.25 < agg < 0.85)
        and spec.harness.enable_layer2_judge is True, evaluate_node() is called with
        the tracked full_window, mirroring score_drift().
        """
        if not _is_scoreable(node):
            return None

        _, content, tool_info = _extract_node_info(node)
        if not content and not tool_info.get("tool_name"):
            return None  # Scoreable type but no content to evaluate

        self._step += 1

        # ── Irreversible-action heuristic gate ───────────────────────────
        # Mirrors score_drift() so the two APIs produce consistent labels for
        # the same (node, spec) pair.
        tool_name = tool_info.get("tool_name", "")
        if tool_name and self.spec.irreversible_actions and tool_name in self.spec.irreversible_actions:
            result = DriftResult(
                score=0.0,
                intent_score=1.0,
                tool_score=0.0,
                constraint_score=1.0,
                failing_dimension="tool",
                node_type=type(node).__name__,
                spec_version=self.spec.version_hash,
                raised_at_step=self._step,
                threshold=self.spec.drift_threshold,
            )
            raise DriftDetected(result)

        tool_score, constraint_score, intent_score = _run_scorers(node, self.spec)
        aggregate = min(tool_score, constraint_score, intent_score)

        # ── Layer-2 judge for ambiguous zone — mirrors score_drift() ─────
        # Runs synchronously (evaluate_node uses a sync Anthropic client).
        # Uses the tracked full_window so context accumulates across check() calls.
        if aggregate > 0.25 and aggregate < 0.85 and self.spec.harness.enable_layer2_judge:
            layer2_ctx = _layer2_evaluator_context(None, self._full_window)
            layer2_label, _ = evaluate_node(
                node, layer2_ctx, self.spec,
                tool_score=tool_score,
                constraint_score=constraint_score,
                intent_score=intent_score,
            )
        else:
            layer2_label = ""

        # Maintain a sliding context window for subsequent Layer-2 calls.
        self._full_window.append(node)
        _win_sz = max(1, self.spec.harness.context_window_size)
        if len(self._full_window) > _win_sz:
            self._full_window.pop(0)

        # Failing dimension: only when below drift_threshold (gate failed).
        # If aggregate >= threshold, report "none" — individual scores may still be <1.0.
        if aggregate >= self.spec.drift_threshold:
            failing = "none"
        # Below threshold: attribute to one dimension — priority tool > constraint > intent
        elif tool_score == aggregate and tool_score < 1.0:
            failing = "tool"
        elif constraint_score == aggregate and constraint_score < 1.0:
            failing = "constraint"
        elif intent_score == aggregate and intent_score < 1.0:
            failing = "intent"
        else:
            failing = "none"

        # When Layer-2 upgrades an ambiguous node to VIOLATED, treat it as a drift event
        # even if the raw aggregate is above the numeric threshold. Cap effective_score so
        # the DriftDetected check (below) fires correctly.
        effective_score = aggregate
        if layer2_label == "VIOLATED" and aggregate >= self.spec.drift_threshold:
            effective_score = self.spec.drift_threshold - 0.01
            # Layer-2 said VIOLATED but Layer-1 scores couldn't pin the dimension;
            # attribute to "intent" as the most general failure class.
            if failing == "none":
                failing = "intent"

        result = DriftResult(
            score=round(effective_score, 4),
            intent_score=round(intent_score, 4),
            tool_score=round(tool_score, 4),
            constraint_score=round(constraint_score, 4),
            failing_dimension=failing,
            node_type=type(node).__name__,
            spec_version=self.spec.version_hash,
            raised_at_step=self._step,
            threshold=self.spec.drift_threshold,
        )

        # Structured debug only — OTel drift spans are emitted from run_with_spec()
        # (TrajectoryChecker is a lightweight per-node API without checkpoint context).
        logger.debug(
            "drift_check step=%d score=%.3f intent=%.3f tool=%.3f "
            "constraint=%.3f failing=%r spec_version=%s node_type=%s",
            self._step, aggregate, intent_score, tool_score,
            constraint_score, failing, self.spec.version_hash,
            type(node).__name__,
        )

        if effective_score < self.spec.drift_threshold:
            raise DriftDetected(result)

        return result

    @property
    def step_count(self) -> int:
        """Number of nodes actually scored (excludes non-scoreable and empty nodes)."""
        return self._step


# ---------------------------------------------------------------------------
# run_with_spec — top-level entry point
# ---------------------------------------------------------------------------

async def run_with_spec(
    agent: Agent,
    task: str,
    spec: SpecModel,
    poller: Optional[SpecPoller] = None,
    cost_guard: Optional[RunCostGuard] = None,
    agent_id: str = "default",
    checkpoint_path: str = CHECKPOINT_FILE,
) -> Any:
    """Full 7-step node-boundary orchestration loop.

    At every node boundary:
        1. Poll M5 for spec update → inject SpecDelta if version changed
        2. Cascade drift score (Layer 1; Layer 2 when ambiguous if harness.enable_layer2_judge)
        3. Environment probe (verify_node_claim when PROGRESSING)
        4. Drift response — inject correction or log escalation
        5. Context window management (full_window + compact_history)
        6. Checkpoint write every checkpoint_every_n_nodes nodes
        7. OTel emit (emit_drift_span when label != PROGRESSING)

    Args:
        agent:           pydantic-ai Agent instance.
        task:            Task string to run.
        spec:            Locked SpecModel — is_locked(spec) must be True.
        poller:          Optional SpecPoller. If None, spec stays fixed for the run.
        cost_guard:      Optional RunCostGuard. If None, no cost enforcement is applied.
        agent_id:        Agent identifier registered in cost_guard. Default "default".
                         Ignored when cost_guard is None.
        checkpoint_path: Path to the checkpoint JSON file. Defaults to
                         CHECKPOINT_FILE ("ballast-progress.json" in CWD).
                         Pass an absolute path when running concurrent jobs to
                         prevent them from sharing a checkpoint file.

    Returns:
        Final agent output.

    Raises:
        ValueError if spec is not locked.
    """
    if not is_locked(spec):
        raise ValueError(
            "spec must be locked before executing. Call lock(spec) first."
        )

    run_id = str(uuid.uuid4())[:8]
    active_spec = spec

    # Resume from checkpoint if available
    progress = BallastProgress.read(checkpoint_path)
    if can_resume(progress, spec):
        task = f"{progress.resume_context()}\n\nOriginal task: {task}"
        node_offset = progress.last_clean_node_index + 1
        if cost_guard is not None:
            cost_guard.seed_prior_spend(progress.total_cost_usd)
            if progress.agent_spend_by_id:
                cost_guard.seed_agent_spends(progress.agent_spend_by_id)
            logger.info(
                "cost_guard seeded prior_spend=%.6f from checkpoint run_id=%s",
                progress.total_cost_usd, progress.run_id,
            )
        logger.info(
            "run_with_spec resuming run_id=%s ballast_index=%d spec_version=%s "
            "(agent runtime may restart its step counter; ledger continues)",
            progress.run_id, node_offset, spec.version_hash,
        )
    else:
        progress = BallastProgress(
            spec_hash=spec.version_hash,
            active_spec_hash=spec.version_hash,
            spec_intent=spec.intent,
            run_id=run_id,
            started_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            remaining_success_criteria=list(spec.success_criteria),
        )
        node_offset = 0

    full_window: list = []
    # Parallel to full_window: per-node (drift_score, label, cost_usd, verified) at eviction.
    full_window_meta: list[tuple[float, str, float, bool]] = []
    compact_history: list[dict] = []
    node_index = node_offset
    _cost_usd_warned = False

    async with agent.iter(task) as agent_run:
        try:
            async for node in agent_run:

                # ── 1. Poll for spec update ─────────────────────────────────
                _poll_interval = max(1, active_spec.harness.spec_poll_interval_nodes)
                if poller and node_index % _poll_interval == 0:
                    new_spec = await asyncio.to_thread(poller.poll)
                    if new_spec:
                        if not is_locked(new_spec):
                            logger.warning(
                                "spec_poll_rejected_unlocked at_node=%d run_id=%s — "
                                "keeping active spec",
                                node_index,
                                run_id,
                            )
                        else:
                            delta = active_spec.diff(new_spec)
                            active_spec = new_spec
                            agent_run.ctx.state.message_history.append(
                                ModelRequest(
                                    parts=[UserPromptPart(content=delta.as_injection())]
                                )
                            )
                            progress.active_spec_hash = active_spec.version_hash
                            progress.spec_transitions.append({
                                "at_node": node_index,
                                "from_hash": delta.from_hash,
                                "to_hash": delta.to_hash,
                            })
                            logger.info(
                                "spec_updated from=%s to=%s at_node=%d run_id=%s",
                                delta.from_hash[:8],
                                delta.to_hash[:8],
                                node_index,
                                run_id,
                            )

                # ── 2. Cascade drift score ──────────────────────────────────
                # score_drift's Layer-2 judge makes a synchronous Anthropic call.
                # Offload to a thread pool so we never block the event loop.
                assessment = await asyncio.to_thread(
                    functools.partial(
                        score_drift, node, full_window, active_spec, compact_history
                    )
                )

                # ── 3. Environment probe ────────────────────────────────────
                verified = True
                if assessment.label == "PROGRESSING":
                    verified, probe_note = await verify_node_claim(
                        node, assessment.label, active_spec,
                    )
                    if not verified:
                        assessment = replace(
                            assessment,
                            label="VIOLATED",
                            score=0.0,
                            rationale=f"probe failed: {probe_note}",
                        )
                        logger.warning(
                            "probe_failed node=%d tool=%s note=%s run_id=%s",
                            node_index, assessment.tool_name, probe_note, run_id,
                        )

                # ── 4. Drift response ───────────────────────────────────────
                if hasattr(node, "cost_usd"):
                    raw_cost = node.cost_usd
                    try:
                        node_cost = float(raw_cost)
                    except (TypeError, ValueError):
                        node_cost = 0.0
                        logger.warning(
                            "cost_usd_invalid node_type=%s value=%r node_index=%d run_id=%s"
                            " — treating as 0.0",
                            type(node).__name__, raw_cost, node_index, run_id,
                        )
                    if math.isnan(node_cost) or math.isinf(node_cost) or node_cost < 0:
                        logger.warning(
                            "cost_usd_out_of_range node_type=%s value=%r node_index=%d"
                            " run_id=%s — clamping to 0.0",
                            type(node).__name__, node_cost, node_index, run_id,
                        )
                        node_cost = 0.0
                else:
                    node_cost = 0.0
                    if cost_guard is not None and not _cost_usd_warned:
                        logger.warning(
                            "cost_usd_missing node_type=%s node_index=%d run_id=%s"
                            " — cost guard is active but node exposes no cost_usd;"
                            " cap enforcement will not fire",
                            type(node).__name__, node_index, run_id,
                        )
                        _cost_usd_warned = True

                if assessment.label == "VIOLATED_IRREVERSIBLE":
                    try:
                        resolution = await escalate(
                            assessment,
                            active_spec,
                            compact_history + full_window,
                            run_id=run_id,
                            node_index=node_index,
                        )
                        agent_run.ctx.state.message_history.append(
                            ModelRequest(parts=[UserPromptPart(content=resolution)])
                        )
                    except EscalationFailed:
                        progress.total_violations += 1
                        progress.write(checkpoint_path)
                        raise HardInterrupt(assessment, active_spec, node_index)
                    # Escalation chain resolved — run continues. Count separately from hard stops.
                    progress.total_escalations_resolved += 1
                    progress.last_escalation = datetime.now(timezone.utc).isoformat()
                    logger.warning(
                        "irreversible_action_detected_resolved node=%d tool=%s spec_version=%s run_id=%s",
                        node_index,
                        assessment.tool_name,
                        active_spec.version_hash,
                        run_id,
                    )

                elif (
                    assessment.score < active_spec.drift_threshold
                    or assessment.label == "VIOLATED"
                ):
                    # Layer-2 may mark a node VIOLATED while the raw numeric aggregate
                    # is still >= drift_threshold (ambiguous band). Enforce on label too
                    # so the correction is always injected on any VIOLATED verdict.
                    correction = build_correction(assessment, active_spec, node_index)
                    agent_run.ctx.state.message_history.append(
                        ModelRequest(parts=[UserPromptPart(content=correction)])
                    )
                    logger.warning(
                        "drift_detected node=%d score=%.3f label=%s spec_version=%s run_id=%s",
                        node_index, assessment.score, assessment.label, active_spec.version_hash, run_id,
                    )
                    progress.total_drift_events += 1
                    if assessment.label == "VIOLATED":
                        progress.total_violations += 1

                # ── 5. Context window management ────────────────────────────
                _win_sz = max(1, active_spec.harness.context_window_size)
                full_window.append(node)
                full_window_meta.append(
                    (assessment.score, assessment.label, node_cost, verified)
                )
                if len(full_window) > _win_sz:
                    evicted = full_window.pop(0)
                    ev_s, ev_l, ev_c, ev_v = full_window_meta.pop(0)
                    compact_history.append(_compact_node(evicted, ev_s, ev_l, ev_c, ev_v))

                # ── 5b. Cost enforcement (before persisting this node) ────────
                if cost_guard is not None:
                    cost_guard.check_and_record(agent_id, node_cost)
                    snap = cost_guard.report()["agents"].get(agent_id)
                    if snap:
                        progress.agent_spend_by_id[agent_id] = {
                            "spent": float(snap["spent"]),
                            "escalation_spent": float(snap["escalation_spent"]),
                        }

                # ── 6. Checkpoint ───────────────────────────────────────────
                progress.completed_node_summaries.append(NodeSummary(
                    index=node_index,
                    tool_name=assessment.tool_name,
                    label=assessment.label,
                    drift_score=assessment.score,
                    cost_usd=node_cost,
                    verified=verified,
                    spec_hash=active_spec.version_hash,   # active hash — NOT dispatch hash
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ))
                # Ring-buffer: keep only the most recent summaries to prevent unbounded
                # checkpoint growth on long runs. Aggregate counters remain exact.
                if len(progress.completed_node_summaries) > _MAX_NODE_SUMMARIES:
                    progress.completed_node_summaries = (
                        progress.completed_node_summaries[-_MAX_NODE_SUMMARIES:]
                    )
                progress.total_cost_usd += node_cost
                progress.updated_at = datetime.now(timezone.utc).isoformat()
                if assessment.label not in ("VIOLATED", "VIOLATED_IRREVERSIBLE"):
                    progress.last_clean_node_index = node_index
                _ckpt_interval = max(1, active_spec.harness.checkpoint_every_n_nodes)
                if node_index % _ckpt_interval == 0:
                    progress.write(checkpoint_path)

                # ── 7. OTel emit ─────────────────────────────────────────────
                if assessment.label != "PROGRESSING":
                    emit_drift_span(assessment, active_spec, node_index, run_id, node_cost)

                node_index += 1

        except (HardInterrupt, KeyboardInterrupt):
            progress.write(checkpoint_path)
            raise
        except GeneratorExit:
            # GeneratorExit is a BaseException used by the generator protocol.
            # Checkpoint before propagating so the run state is not lost, but
            # keep it separate from the Exception block to avoid interfering
            # with generator cleanup semantics.
            progress.write(checkpoint_path)
            raise
        except Exception:
            progress.write(checkpoint_path)
            raise

    progress.is_complete = True
    progress.write(checkpoint_path)

    # Extract final output — defensive for pydantic-ai version differences.
    # agent_run.get_output() is preferred; result.output is the fallback.
    if hasattr(agent_run, "get_output"):
        return await agent_run.get_output()
    result = getattr(agent_run, "result", None)
    if result is not None:
        return agent_run_result_payload(result)
    logger.warning(
        "run_with_spec: output extraction failed. spec_version=%s run_id=%s",
        spec.version_hash, run_id,
    )
    return None
