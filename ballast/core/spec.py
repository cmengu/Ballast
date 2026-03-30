"""ballast/core/spec.py — Intent grounding layer.

Public interface:
    lock_spec(goal, domain, interactive) -> LockedSpec
    RunPhaseTracker — propagates IntentSignal through a live event stream

Internal:
    _score_axes(goal, domain) -> AmbiguityScores
    ClarificationPolicy — reads per-domain threshold from memory
    _clarify(goal, axes) -> list[str]   (questions as structured choices)
    _infer_spec(goal, axes) -> LockedSpec (LLM-inferred, no questions asked)

STITCH reference:
    IntentSignal maps to STITCH's contextual intent cue:
      latent_goal        → thematic segment label
      action_type        → verb class of the current operation
      salient_entity_types → which attribute dimensions matter now
    RunPhaseTracker updates the signal per event so memory retrieval
    remains intent-compatible as context evolves mid-run.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Ambiguity axis taxonomy
# ---------------------------------------------------------------------------

class AmbiguityType(str, Enum):
    """The three orthogonal dimensions of goal underspecification.

    Derived from the robotics clarification literature:
      ATTRIBUTE  — which version/variant/format is wanted?
      SCOPE      — which files/services/environment does this touch?
      PREFERENCE — speed vs. thoroughness, brevity vs. completeness, etc.

    Each maps to a targeted clarification question class (see _clarify).
    """
    ATTRIBUTE = "attribute"
    SCOPE = "scope"
    PREFERENCE = "preference"


class AmbiguityScore(BaseModel):
    """Per-axis ambiguity assessment."""
    axis: AmbiguityType
    score: float = Field(
        ge=0.0, le=1.0,
        description="0.0 = fully specified, 1.0 = completely ambiguous"
    )
    reason: str = Field(
        description="One-sentence explanation used to generate a targeted question"
    )
    is_blocking: bool = Field(
        description="True if this ambiguity should trigger a clarification question"
    )


class AmbiguityScores(BaseModel):
    """Complete per-axis assessment for a goal."""
    attribute: AmbiguityScore
    scope: AmbiguityScore
    preference: AmbiguityScore

    @property
    def blocking_axes(self) -> list[AmbiguityScore]:
        """Axes flagged as blocking — these drive question generation."""
        return [a for a in [self.attribute, self.scope, self.preference]
                if a.is_blocking]

    @property
    def max_score(self) -> float:
        """Highest individual axis score — used by policy as the decision signal."""
        return max(
            self.attribute.score,
            self.scope.score,
            self.preference.score,
        )


# ---------------------------------------------------------------------------
# STITCH intent signal
# ---------------------------------------------------------------------------

class IntentSignal(BaseModel):
    """Structured contextual intent cue (STITCH-derived).

    Created at spec-lock time from the goal and inferred spec.
    Updated by RunPhaseTracker as events stream in during the run.

    Purpose: enables memory.recall() to filter history by intent
    compatibility, not just semantic similarity — suppressing
    context-incompatible snippets from earlier thematic segments.
    """
    latent_goal: str = Field(
        description="Thematic segment label — what is the agent fundamentally trying to achieve?"
    )
    action_type: str = Field(
        description="Verb class: READ | WRITE | TRANSFORM | VERIFY | SEARCH | COORDINATE"
    )
    salient_entity_types: list[str] = Field(
        default_factory=list,
        description="Which attribute dimensions matter now (e.g. ['file_path', 'function_name'])"
    )
    step_index: int = Field(
        default=0,
        description="Run-step counter — incremented by RunPhaseTracker per event"
    )


# ---------------------------------------------------------------------------
# Locked spec — the stable contract passed to every downstream component
# ---------------------------------------------------------------------------

class LockedSpec(BaseModel):
    """Frozen intent grounding contract.

    Produced by lock_spec(). Consumed by:
      - AGUIAdapter.stream(goal, spec)  — passed as spec arg
      - trajectory.py (Week 3)          — validates against success_criteria
      - memory.log_run()                — serialised as run context
      - RunPhaseTracker                   — carries intent_signal forward

    Field stability guarantee: names here are the Week 2–4 API surface.
    Do not rename without updating trajectory.py and memory.py call sites.
    """
    goal: str = Field(description="Original raw goal string — preserved for audit")
    domain: str = Field(description="Domain key used for per-domain threshold lookup")

    # Core spec fields — what trajectory.py validates against
    success_criteria: str = Field(
        description="Measurable definition of done. Must be verifiable from agent output."
    )
    scope: str = Field(
        description="Boundary of what the agent may touch. Empty = unconstrained."
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Hard constraints the agent must not violate."
    )
    output_format: str = Field(
        default="",
        description="Required output format if specified. Empty = inferred from context."
    )

    # Grounding metadata
    inferred_assumptions: list[str] = Field(
        default_factory=list,
        description="Assumptions made when spec was inferred without asking. Surfaced to user."
    )
    ambiguity_scores: Optional[AmbiguityScores] = Field(
        default=None,
        description="Per-axis scores at lock time. Stored for threshold calibration."
    )

    # STITCH intent signal — travels with spec, mutated by RunPhaseTracker
    intent_signal: IntentSignal = Field(
        description="Structured contextual intent cue. Updated per-event during run."
    )

    # Policy metadata
    clarification_asked: bool = Field(
        default=False,
        description="True if clarification questions were surfaced before locking."
    )
    threshold_used: float = Field(
        description="The domain threshold that decided ask-vs-infer. Stored for calibration."
    )

    model_config = {"frozen": False}  # RunPhaseTracker mutates intent_signal.step_index


# ---------------------------------------------------------------------------
# Anthropic client (lazy singleton — matches memory.py pattern)
# ---------------------------------------------------------------------------

import anthropic as _anthropic
import json as _json

_spec_client: "_anthropic.Anthropic | None" = None
_SPEC_MODEL: str = "claude-sonnet-4-6"


def _get_spec_client() -> "_anthropic.Anthropic":
    global _spec_client
    if _spec_client is None:
        _spec_client = _anthropic.Anthropic()
    return _spec_client


# ---------------------------------------------------------------------------
# Per-axis ambiguity scoring
# ---------------------------------------------------------------------------

_SCORING_PROMPT = """You are an intent grounding system for an AI agent orchestrator.

Analyse the following goal string across THREE independent ambiguity axes.
For each axis, produce a score from 0.0 (fully specified) to 1.0 (completely ambiguous)
and a one-sentence reason.

ATTRIBUTE ambiguity: Is it unclear which version, format, variant, or specific item is wanted?
Example high score: "fix the bug" — which bug? which file?
Example low score: "fix the off-by-one error in src/parser.py line 42"

SCOPE ambiguity: Is it unclear which files, services, environments, or resources are in play?
Example high score: "update the tests" — which tests? which test runner? which environment?
Example low score: "update tests/test_parser.py to cover the edge case added in PR #12"

PREFERENCE ambiguity: Is it unclear whether speed vs thoroughness, brevity vs completeness,
or safety vs risk is preferred?
Example high score: "summarise the document" — short or comprehensive? lose nuance or preserve it?
Example low score: "produce a 3-bullet executive summary of the document, prioritising action items"

Also decide: is each axis BLOCKING (score >= 0.55 suggests blocking, but use judgment)?
An axis is blocking if a wrong assumption would cause the agent to do the wrong thing.

Domain context: {domain}
Goal: {goal}"""


def _score_axes(goal: str, domain: str) -> AmbiguityScores:
    """Score a goal across ATTRIBUTE, SCOPE, PREFERENCE axes independently.

    Uses Claude with structured tool output to produce per-axis scores.
    Returns conservative all-blocking scores on any error (fail-safe: prefer asking).

    Args:
        goal:   Raw goal string from the user.
        domain: Domain key for context (included in prompt to calibrate scoring to domain norms).
    Returns:
        AmbiguityScores with three independent axis assessments.
    """
    prompt = _SCORING_PROMPT.format(goal=goal, domain=domain)
    try:
        response = _get_spec_client().messages.create(
            model=_SPEC_MODEL,
            max_tokens=400,
            tools=[{
                "name": "score_ambiguity",
                "description": "Return per-axis ambiguity scores.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "attribute": {
                            "type": "object",
                            "properties": {
                                "score": {"type": "number"},
                                "reason": {"type": "string"},
                                "is_blocking": {"type": "boolean"},
                            },
                            "required": ["score", "reason", "is_blocking"],
                        },
                        "scope": {
                            "type": "object",
                            "properties": {
                                "score": {"type": "number"},
                                "reason": {"type": "string"},
                                "is_blocking": {"type": "boolean"},
                            },
                            "required": ["score", "reason", "is_blocking"],
                        },
                        "preference": {
                            "type": "object",
                            "properties": {
                                "score": {"type": "number"},
                                "reason": {"type": "string"},
                                "is_blocking": {"type": "boolean"},
                            },
                            "required": ["score", "reason", "is_blocking"],
                        },
                    },
                    "required": ["attribute", "scope", "preference"],
                },
            }],
            tool_choice={"type": "tool", "name": "score_ambiguity"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use":
                raw = block.input
                return AmbiguityScores(
                    attribute=AmbiguityScore(
                        axis=AmbiguityType.ATTRIBUTE,
                        score=float(raw["attribute"]["score"]),
                        reason=raw["attribute"]["reason"],
                        is_blocking=bool(raw["attribute"]["is_blocking"]),
                    ),
                    scope=AmbiguityScore(
                        axis=AmbiguityType.SCOPE,
                        score=float(raw["scope"]["score"]),
                        reason=raw["scope"]["reason"],
                        is_blocking=bool(raw["scope"]["is_blocking"]),
                    ),
                    preference=AmbiguityScore(
                        axis=AmbiguityType.PREFERENCE,
                        score=float(raw["preference"]["score"]),
                        reason=raw["preference"]["reason"],
                        is_blocking=bool(raw["preference"]["is_blocking"]),
                    ),
                )
    except Exception:
        pass

    # Fail-safe: if scoring fails for any reason, return conservative blocking scores.
    # This ensures the system asks rather than makes wrong assumptions on error.
    _conservative = AmbiguityScore(
        axis=AmbiguityType.ATTRIBUTE,
        score=0.70,
        reason="Scoring failed — treating as ambiguous for safety.",
        is_blocking=True,
    )
    return AmbiguityScores(
        attribute=_conservative.model_copy(update={"axis": AmbiguityType.ATTRIBUTE}),
        scope=_conservative.model_copy(update={"axis": AmbiguityType.SCOPE}),
        preference=_conservative.model_copy(update={"axis": AmbiguityType.PREFERENCE}),
    )


# ---------------------------------------------------------------------------
# Clarification policy — the learned ask/infer decision
# ---------------------------------------------------------------------------

class ClarificationPolicy:
    """Encapsulates the per-domain ask-vs-infer decision.

    Reads the current domain threshold from memory at construction time.
    Decision: if any blocking axis score >= threshold → ask.

    The threshold is not hardcoded. It drifts over time via
    memory.update_domain_threshold() called at the end of each run.
    This approximates the RL policy the frontier literature describes:
    'when to ask' is a learned, domain-calibrated function, not a constant.

    Usage:
        policy = ClarificationPolicy(domain='coding')
        if policy.should_ask(scores):
            questions = _clarify(goal, scores)
    """

    def __init__(self, domain: str) -> None:
        from ballast.core.memory import get_domain_threshold
        self.domain = domain
        self.threshold: float = get_domain_threshold(domain)

    def should_ask(self, scores: AmbiguityScores) -> bool:
        """Return True if clarification questions should be surfaced.

        Decision: at least one blocking axis has score >= self.threshold.
        Non-blocking axes never trigger asking regardless of score.
        """
        return any(
            axis.score >= self.threshold
            for axis in scores.blocking_axes
        )


# ---------------------------------------------------------------------------
# Question generation — structured choices, not open text
# ---------------------------------------------------------------------------

_QUESTION_TYPE_PROMPTS: dict[AmbiguityType, str] = {
    AmbiguityType.ATTRIBUTE: (
        "Generate a clarification question about WHICH specific item, version, "
        "or variant is wanted. The question must offer 2-3 concrete choices. "
        "Reason for asking: {reason}"
    ),
    AmbiguityType.SCOPE: (
        "Generate a clarification question about the BOUNDARY of what should be "
        "touched (files, services, environment). Offer 2-3 concrete scope options. "
        "Reason for asking: {reason}"
    ),
    AmbiguityType.PREFERENCE: (
        "Generate a clarification question about the TRADE-OFF preferred "
        "(speed vs thoroughness, brevity vs detail, etc). Offer 2-3 named options. "
        "Reason for asking: {reason}"
    ),
}

_CLARIFY_SYSTEM_PROMPT = """You are generating targeted clarification questions for an AI agent.
Rules:
- Generate EXACTLY ONE question per axis provided.
- Each question must be a single sentence ending with '?'
- Each question must offer 2-3 concrete choices in square brackets like: [option A / option B]
- Do NOT ask about axes not provided.
- Keep questions to 25 words max.
- Return ONLY a JSON array of question strings. No preamble. No markdown.
Example: ["Which file should be updated? [parser.py / tokenizer.py / all affected files]",
           "Should the output be brief or comprehensive? [brief summary / full analysis]"]
"""


def _clarify(goal: str, scores: AmbiguityScores) -> list[str]:
    """Generate targeted clarification questions for blocking axes.

    Returns at most 2 questions (the 2 highest-scoring blocking axes).
    Questions are structured as choices, not open text.
    Returns [] on any error — caller falls through to _infer_spec.

    Args:
        goal:   Raw goal string (for question context).
        scores: Ambiguity scores — only blocking axes generate questions.
    Returns:
        list of question strings (0-2 items).
    """
    blocking = sorted(
        scores.blocking_axes,
        key=lambda a: a.score,
        reverse=True,
    )[:2]  # Cap at 2 questions maximum

    if not blocking:
        return []

    axis_instructions = "\n".join(
        f"Axis {i+1} ({ax.axis.value}): "
        + _QUESTION_TYPE_PROMPTS[ax.axis].format(reason=ax.reason)
        for i, ax in enumerate(blocking)
    )

    user_prompt = (
        f"Goal: {goal}\n\n"
        f"Generate clarification questions for these axes:\n{axis_instructions}"
    )

    try:
        response = _get_spec_client().messages.create(
            model=_SPEC_MODEL,
            max_tokens=300,
            system=_CLARIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(
            block.text for block in response.content
            if hasattr(block, "text")
        ).strip()
        questions = _json.loads(text)
        if isinstance(questions, list):
            return [q for q in questions if isinstance(q, str)][:2]
    except Exception:
        pass
    return []
