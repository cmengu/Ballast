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


# ---------------------------------------------------------------------------
# Spec inference — used when policy decides not to ask
# ---------------------------------------------------------------------------

_INFER_PROMPT = """You are an intent grounding system. Given a goal string, infer a locked specification.

Goal: {goal}
Domain: {domain}

Ambiguity analysis:
- Attribute: {attr_reason} (score {attr_score:.2f})
- Scope: {scope_reason} (score {scope_score:.2f})
- Preference: {pref_reason} (score {pref_score:.2f})

Produce a locked spec. Make reasonable default assumptions where ambiguous.
Be conservative — prefer narrower scope over broader."""

_INFER_TOOL = {
    "name": "infer_spec",
    "description": "Return a locked specification inferred from the goal.",
    "input_schema": {
        "type": "object",
        "properties": {
            "success_criteria": {"type": "string", "description": "Measurable definition of done — one sentence"},
            "scope": {"type": "string", "description": "Boundary of what the agent may touch — one phrase or empty string"},
            "constraints": {"type": "array", "items": {"type": "string"}, "description": "Hard constraints the agent must not violate"},
            "output_format": {"type": "string", "description": "Required output format, or empty string"},
            "inferred_assumptions": {"type": "array", "items": {"type": "string"}, "description": "Assumptions made when inferring the spec"},
            "latent_goal": {"type": "string", "description": "Thematic label — 3 words max"},
            "action_type": {"type": "string", "enum": ["READ", "WRITE", "TRANSFORM", "VERIFY", "SEARCH", "COORDINATE"]},
            "salient_entity_types": {"type": "array", "items": {"type": "string"}, "description": "Entity types relevant to this goal"},
        },
        "required": ["success_criteria", "scope", "constraints", "output_format",
                     "inferred_assumptions", "latent_goal", "action_type", "salient_entity_types"],
    },
}


def _infer_spec(goal: str, domain: str, scores: AmbiguityScores) -> LockedSpec:
    """Infer a LockedSpec from the goal without asking the user.

    Called when ClarificationPolicy.should_ask() returns False.
    Uses Claude tool_use (structured output) to fill in spec fields.
    Also extracts the initial IntentSignal from the inference.

    On error: returns a minimal valid LockedSpec with the raw goal as success_criteria
    and empty scope — safe for the agent to run against with no constraints.

    Args:
        goal:   Raw goal string.
        domain: Domain key (for context).
        scores: Ambiguity scores (included in prompt for calibration context).
    Returns:
        LockedSpec (never raises).
    """
    from ballast.core.memory import get_domain_threshold

    prompt = _INFER_PROMPT.format(
        goal=goal,
        domain=domain,
        attr_reason=scores.attribute.reason,
        attr_score=scores.attribute.score,
        scope_reason=scores.scope.reason,
        scope_score=scores.scope.score,
        pref_reason=scores.preference.reason,
        pref_score=scores.preference.score,
    )

    try:
        response = _get_spec_client().messages.create(
            model=_SPEC_MODEL,
            max_tokens=600,
            tools=[_INFER_TOOL],
            tool_choice={"type": "tool", "name": "infer_spec"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use":
                raw = block.input
                intent = IntentSignal(
                    latent_goal=raw.get("latent_goal", goal[:30]),
                    action_type=raw.get("action_type", "COORDINATE"),
                    salient_entity_types=raw.get("salient_entity_types", []),
                )
                return LockedSpec(
                    goal=goal,
                    domain=domain,
                    success_criteria=raw.get("success_criteria", goal),
                    scope=raw.get("scope", ""),
                    constraints=raw.get("constraints", []),
                    output_format=raw.get("output_format", ""),
                    inferred_assumptions=raw.get("inferred_assumptions", []),
                    ambiguity_scores=scores,
                    intent_signal=intent,
                    clarification_asked=False,
                    threshold_used=get_domain_threshold(domain),
                )
    except Exception:
        pass

    # Minimal safe fallback
    return LockedSpec(
        goal=goal,
        domain=domain,
        success_criteria=goal,
        scope="",
        constraints=[],
        output_format="",
        inferred_assumptions=["Spec inference failed — using raw goal as success criteria"],
        ambiguity_scores=scores,
        intent_signal=IntentSignal(
            latent_goal=goal[:30],
            action_type="COORDINATE",
            salient_entity_types=[],
        ),
        clarification_asked=False,
        threshold_used=get_domain_threshold(domain),
    )


# ---------------------------------------------------------------------------
# lock_spec — public entry point (Facade)
# ---------------------------------------------------------------------------

def lock_spec(
    goal: str,
    domain: str = "general",
    interactive: bool = False,
) -> "tuple[LockedSpec, list[str]]":
    """Ground a raw goal into a locked spec. Public API.

    Pipeline:
      1. Score goal on ATTRIBUTE, SCOPE, PREFERENCE axes independently
      2. Read per-domain threshold from memory (learned, not hardcoded)
      3. ClarificationPolicy decides: ask or infer
      4a. If ask (interactive=True): generate targeted choice questions (max 2)
          Return (spec_placeholder, questions) — caller surfaces questions to user
          Caller must call lock_spec_with_answers(goal, domain, answers) next
      4b. If infer (or interactive=False): infer spec from goal + ambiguity context
          Return (locked_spec, []) with inferred_assumptions surfaced as one-liner

    Args:
        goal:        Raw goal string from user.
        domain:      Domain key for threshold lookup and memory scoping.
        interactive: If False, always infer — never ask. Use for programmatic callers.
    Returns:
        (LockedSpec, questions: list[str])
        If questions is non-empty: spec is a placeholder, caller must handle questions.
        If questions is empty: spec is fully locked, ready to pass to stream().
    """
    scores = _score_axes(goal, domain)
    policy = ClarificationPolicy(domain)

    if interactive and policy.should_ask(scores):
        questions = _clarify(goal, scores)
        if questions:
            # Return placeholder spec + questions for caller to surface
            placeholder = LockedSpec(
                goal=goal,
                domain=domain,
                success_criteria="",
                scope="",
                constraints=[],
                output_format="",
                inferred_assumptions=[],
                ambiguity_scores=scores,
                intent_signal=IntentSignal(
                    latent_goal=goal[:30],
                    action_type="COORDINATE",
                    salient_entity_types=[],
                ),
                clarification_asked=True,
                threshold_used=policy.threshold,
            )
            return placeholder, questions

    # Infer path: interactive=False, or policy said don't ask, or _clarify returned []
    spec = _infer_spec(goal, domain, scores)
    return spec, []


def lock_spec_with_answers(
    goal: str,
    domain: str,
    questions: list[str],
    answers: list[str],
) -> LockedSpec:
    """Complete spec locking after user answered clarification questions.

    Called after lock_spec() returned non-empty questions and the caller
    surfaced them to the user and collected answers.

    Args:
        goal:      Original raw goal.
        domain:    Domain key.
        questions: Questions returned by lock_spec().
        answers:   User's answers (same length as questions).
    Returns:
        Fully locked LockedSpec. Never raises.
    """
    from ballast.core.memory import get_domain_threshold

    enriched_goal = goal
    if questions and answers:
        qa_context = "\n".join(
            f"Q: {q}\nA: {a}" for q, a in zip(questions, answers)
        )
        enriched_goal = f"{goal}\n\nUser clarifications:\n{qa_context}"

    scores = _score_axes(enriched_goal, domain)
    spec = _infer_spec(enriched_goal, domain, scores)
    spec.goal = goal  # preserve original goal for audit
    spec.clarification_asked = True
    return spec
