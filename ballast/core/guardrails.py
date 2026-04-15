"""ballast/core/guardrails.py — Soft injection, hard interrupt, resume gating.

Public interface:
    build_correction(assessment, spec, node_index) -> str
        Builds the soft correction string injected between nodes when
        assessment.score < spec.drift_threshold. Replaces the inline
        TODO-Step-6 block in trajectory.py:run_with_spec.

    HardInterrupt(Exception)
        Raised when a VIOLATED_IRREVERSIBLE node has no escalation path.
        Carries assessment and spec so the caller can checkpoint and surface
        the interruption. Wired in run_with_spec once escalation.py is used.

    can_resume(progress, spec) -> bool
        Pure predicate. Returns True if progress is a non-complete checkpoint
        whose spec_hash matches spec.version_hash. Replaces the inline
        three-part boolean in run_with_spec's resume branch.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ballast.core.checkpoint import BallastProgress
from ballast.core.spec import SpecModel

if TYPE_CHECKING:
    # NodeAssessment lives in trajectory.py. Import only for type checkers —
    # avoids the circular import trajectory → guardrails → trajectory.
    from ballast.core.trajectory import NodeAssessment


# ---------------------------------------------------------------------------
# build_correction — soft correction string for drift events
# ---------------------------------------------------------------------------

def build_correction(
    assessment: "NodeAssessment",
    spec: SpecModel,
    node_index: int,
) -> str:
    """Build the soft correction string injected between nodes on drift.

    Called by run_with_spec when assessment.score < spec.drift_threshold
    and assessment.label is not VIOLATED_IRREVERSIBLE. The returned string
    is injected as a UserPromptPart between nodes — it does not stop the
    agent, only redirects it toward spec alignment.

    Args:
        assessment:  NodeAssessment from score_drift() for this node.
        spec:        Active SpecModel at the time of the drift event.
        node_index:  0-based index of the drifting node in the run.

    Returns:
        Multi-line correction string beginning with [BALLAST CORRECTION].
    """
    lines = [
        f"[BALLAST CORRECTION] Drift detected at node {node_index}.",
        f"Score: {assessment.score:.2f}  Label: {assessment.label}",
        f"Rationale: {assessment.rationale}",
    ]
    if assessment.tool_name:
        lines.append(f"Tool called: {assessment.tool_name}")
    lines.append(f"Re-align with spec intent: {spec.intent[:200]}")
    lines.append(
        f"Spec version: {spec.version_hash[:8]}  "
        f"Threshold: {spec.drift_threshold:.2f}"
    )
    lines.append("[Continue from current position. Do not restart the task.]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HardInterrupt — typed exception for VIOLATED_IRREVERSIBLE nodes
# ---------------------------------------------------------------------------

class HardInterrupt(Exception):
    """Raised when a VIOLATED_IRREVERSIBLE node is detected and no
    escalation path is available (escalation chain exhausted).

    Carries the NodeAssessment and active SpecModel. Callers should:
      1. Ensure BallastProgress is written at last_clean_node_index.
      2. Log the full context before re-raising.
      3. Surface to the operator for manual resolution.
    """

    def __init__(
        self,
        assessment: "NodeAssessment",
        spec: SpecModel,
        node_index: int,
    ) -> None:
        self.assessment = assessment
        self.spec = spec
        self.node_index = node_index
        super().__init__(
            f"hard interrupt at node {node_index}: "
            f"irreversible tool={assessment.tool_name!r} "
            f"spec_version={spec.version_hash[:8]}"
        )


# ---------------------------------------------------------------------------
# can_resume — resume-decision predicate
# ---------------------------------------------------------------------------

def can_resume(progress: BallastProgress | None, spec: SpecModel) -> bool:
    """Return True if the run should resume from an existing checkpoint.

    Pure predicate — does not read from disk. The caller (run_with_spec)
    has already read the checkpoint via BallastProgress.read().

    Args:
        progress: BallastProgress returned by BallastProgress.read(), or None
                  if no checkpoint file exists.
        spec:     The SpecModel being dispatched for this run.

    Returns:
        True  — resume from progress.last_clean_node_index.
        False — start a fresh run.
    """
    return (
        progress is not None
        and progress.spec_hash == spec.version_hash
        and not progress.is_complete
    )
