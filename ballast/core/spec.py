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
