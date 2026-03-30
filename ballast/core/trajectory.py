"""ballast/core/trajectory.py — Thin trajectory validator.

Public interface:
    validate_trajectory(spec, events) -> TrajectoryReport
        Check agent output against spec.success_criteria.
        Call update_domain_threshold after the run to close the calibration loop.

This is a Week 2 thin validator: string-presence check against success_criteria.
Week 3 upgrade: replace with structured output comparison once trajectory
patterns are known from observe.py runs.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from ballast.core.spec import LockedSpec


class TrajectoryReport(BaseModel):
    """Output of validate_trajectory(). Consumed by memory.log_run() and callers."""
    spec_goal: str = Field(description="Original goal from LockedSpec")
    success_criteria: str = Field(description="The criteria that was checked")
    passed: bool = Field(description="True if success_criteria was satisfied")
    matched_in: str = Field(
        default="",
        description="The event content fragment that satisfied the criteria, or empty string"
    )
    event_count: int = Field(description="Total events processed")
    notes: list[str] = Field(
        default_factory=list,
        description="Human-readable notes about the validation result"
    )


def validate_trajectory(
    spec: LockedSpec,
    events: list[dict[str, Any]],
    update_calibration: bool = True,
) -> TrajectoryReport:
    """Validate a completed agent run against the locked spec.

    Week 2 implementation: checks whether any 'on_chain_end' event output
    contains content that overlaps with keywords from success_criteria.
    This is deliberately simple — correctness over sophistication.

    Calls memory.update_domain_threshold() after validation (unless
    update_calibration=False) to close the threshold calibration loop.

    Args:
        spec:               The locked spec the agent ran against.
        events:             List of LangGraph events from AGUIAdapter.stream().
        update_calibration: If True, call update_domain_threshold after validation.
                            Set False in unit tests to avoid filesystem writes.
    Returns:
        TrajectoryReport with passed=True if criteria satisfied, False otherwise.
    """
    criteria_keywords = _extract_keywords(spec.success_criteria)
    passed = False
    matched_in = ""
    notes: list[str] = []

    for event in events:
        if event.get("event") != "on_chain_end":
            continue
        data = event.get("data", {})
        output = data.get("output", {})
        content = _extract_content(output)
        if not content:
            continue
        if criteria_keywords and _keywords_present(criteria_keywords, content):
            passed = True
            matched_in = content[:200]
            notes.append(f"Criteria keywords found in on_chain_end output at event index {events.index(event)}")
            break

    if not passed and not criteria_keywords:
        # No keywords to check — cannot validate; mark as passed with a note
        passed = True
        notes.append("success_criteria had no extractable keywords — cannot validate; marking passed")

    if update_calibration:
        _update_calibration(spec, passed)

    return TrajectoryReport(
        spec_goal=spec.goal,
        success_criteria=spec.success_criteria,
        passed=passed,
        matched_in=matched_in,
        event_count=len(events),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_keywords(text: str) -> list[str]:
    """Extract significant words from success_criteria for keyword matching.

    Strips stop words. Returns empty list if text is empty.
    """
    stop = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "has", "have", "had", "do", "does", "did", "will", "would",
        "should", "could", "may", "might", "must", "shall", "and",
        "or", "but", "in", "on", "at", "to", "for", "of", "with",
        "by", "from", "as", "into", "through", "that", "this", "it",
    }
    words = text.lower().split()
    return [w.strip(".,!?:;\"'") for w in words if w.strip(".,!?:;\"'") not in stop and len(w) > 2]


def _extract_content(output: Any) -> str:
    """Extract text content from a LangGraph chain output dict."""
    if isinstance(output, str):
        return output
    if not isinstance(output, dict):
        return ""
    # Try messages list (standard ReAct agent output shape)
    messages = output.get("messages", [])
    if messages:
        last = messages[-1]
        if isinstance(last, dict):
            return str(last.get("content", ""))
        if hasattr(last, "content"):
            return str(last.content)
    # Try direct output field
    return str(output.get("output", ""))


def _keywords_present(keywords: list[str], content: str) -> bool:
    """Return True if at least half the keywords appear in content (case-insensitive)."""
    if not keywords:
        return False
    content_lower = content.lower()
    matches = sum(1 for kw in keywords if kw in content_lower)
    return matches >= max(1, len(keywords) // 2)


def _update_calibration(spec: LockedSpec, run_succeeded: bool) -> None:
    """Wire the run outcome to memory calibration. Never raises."""
    try:
        from ballast.core.memory import update_domain_threshold
        max_score = 0.0
        if spec.ambiguity_scores is not None:
            max_score = spec.ambiguity_scores.max_score
        update_domain_threshold(
            domain=spec.domain,
            clarification_asked=spec.clarification_asked,
            run_succeeded=run_succeeded,
            max_ambiguity_score=max_score,
        )
    except Exception:
        pass  # Calibration update is best-effort — never break the caller
