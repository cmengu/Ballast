"""Tests for ballast/core/trajectory.py — mid-run drift detection.

Unit tests mock score_intent_alignment and score_constraint_violation.
score_tool_compliance is tested directly (pure Python, no LLM).
Integration test requires ANTHROPIC_API_KEY. Skip with: pytest -m 'not integration'
"""
import os
from unittest.mock import patch

import pytest

from ballast.core.spec import SpecModel, lock
from ballast.core.trajectory import (
    DriftDetected,
    DriftResult,
    TrajectoryChecker,
    _extract_node_info,
    score_tool_compliance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(
    allowed_tools: list = None,
    constraints: list = None,
    drift_threshold: float = 0.7,
) -> SpecModel:
    return lock(SpecModel(
        intent="count words in a string",
        success_criteria=["returns an integer", "integer is accurate"],
        constraints=constraints or [],
        allowed_tools=allowed_tools or [],
        drift_threshold=drift_threshold,
    ))


class FakeToolNode:
    """Simulates a pydantic-ai node with tool_name and args attributes."""
    def __init__(self, tool_name: str, args: dict = None):
        self.tool_name = tool_name
        self.args = args or {}


class FakeTextNode:
    """Simulates a pydantic-ai node with a text attribute."""
    def __init__(self, text: str):
        self.text = text


class FakeEmptyNode:
    """Node with no scoreable attributes."""
    pass


# ---------------------------------------------------------------------------
# _extract_node_info
# ---------------------------------------------------------------------------

def test_extract_tool_node_detects_tool_name():
    node = FakeToolNode("get_word_count", {"text": "hello"})
    node_type, content, tool_info = _extract_node_info(node)
    assert tool_info["tool_name"] == "get_word_count"
    assert tool_info["tool_args"] == {"text": "hello"}


def test_extract_text_node_captures_content():
    node = FakeTextNode("The word count is 4.")
    node_type, content, tool_info = _extract_node_info(node)
    assert "word count" in content
    assert tool_info == {}


def test_extract_empty_node_returns_empty():
    node_type, content, tool_info = _extract_node_info(FakeEmptyNode())
    assert content == ""
    assert tool_info == {}


def test_extract_node_type_name_is_class_name():
    node = FakeToolNode("any")
    node_type, _, _ = _extract_node_info(node)
    assert node_type == "FakeToolNode"


# ---------------------------------------------------------------------------
# score_tool_compliance (rule-based, no LLM)
# ---------------------------------------------------------------------------

def test_tool_compliance_empty_allowed_all_permitted():
    spec = _make_spec(allowed_tools=[])
    assert score_tool_compliance(FakeToolNode("any_tool"), spec) == 1.0


def test_tool_compliance_tool_in_list():
    spec = _make_spec(allowed_tools=["get_word_count"])
    assert score_tool_compliance(FakeToolNode("get_word_count"), spec) == 1.0


def test_tool_compliance_tool_not_in_list():
    spec = _make_spec(allowed_tools=["get_word_count"])
    assert score_tool_compliance(FakeToolNode("forbidden"), spec) == 0.0


def test_tool_compliance_non_tool_node_always_passes():
    spec = _make_spec(allowed_tools=["get_word_count"])
    assert score_tool_compliance(FakeTextNode("some output"), spec) == 1.0


# ---------------------------------------------------------------------------
# TrajectoryChecker — init guards
# ---------------------------------------------------------------------------

def test_checker_requires_locked_spec():
    draft = SpecModel(intent="x", success_criteria=["y"])
    with pytest.raises(ValueError, match="locked"):
        TrajectoryChecker(draft)


def test_checker_accepts_locked_spec():
    checker = TrajectoryChecker(_make_spec())
    assert checker.step_count == 0


# ---------------------------------------------------------------------------
# TrajectoryChecker — non-scoreable events
# ---------------------------------------------------------------------------

def test_checker_empty_node_returns_none():
    checker = TrajectoryChecker(_make_spec())
    result = checker.check(FakeEmptyNode())
    assert result is None
    assert checker.step_count == 0


# ---------------------------------------------------------------------------
# TrajectoryChecker — passing checks
# ---------------------------------------------------------------------------

def test_checker_passing_tool_check_returns_drift_result():
    spec = _make_spec(allowed_tools=["get_word_count"])
    checker = TrajectoryChecker(spec)
    node = FakeToolNode("get_word_count", {"text": "hello"})
    with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.9), \
         patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
        result = checker.check(node)
    assert isinstance(result, DriftResult)
    assert result.tool_score == 1.0
    assert result.failing_dimension == "none"
    assert checker.step_count == 1


def test_checker_step_count_increments_per_scored_node():
    checker = TrajectoryChecker(_make_spec())
    with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.9), \
         patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
        checker.check(FakeToolNode("t1"))
        checker.check(FakeTextNode("output"))
    assert checker.step_count == 2


def test_checker_non_scoreable_does_not_increment_step():
    checker = TrajectoryChecker(_make_spec())
    checker.check(FakeEmptyNode())
    assert checker.step_count == 0


# ---------------------------------------------------------------------------
# TrajectoryChecker — drift detected
# ---------------------------------------------------------------------------

def test_checker_forbidden_tool_raises_drift_detected():
    spec = _make_spec(allowed_tools=["get_word_count"])
    checker = TrajectoryChecker(spec)
    with pytest.raises(DriftDetected) as exc_info:
        with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
             patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
            checker.check(FakeToolNode("forbidden_tool"))
    result = exc_info.value.result
    assert result.tool_score == 0.0
    assert result.failing_dimension == "tool"
    assert result.score == 0.0


def test_checker_constraint_violation_raises_drift():
    spec = _make_spec(constraints=["do not write files"])
    checker = TrajectoryChecker(spec)
    with pytest.raises(DriftDetected) as exc_info:
        with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.9), \
             patch("ballast.core.trajectory.score_constraint_violation", return_value=0.0):
            checker.check(FakeTextNode("I modified the file"))
    assert exc_info.value.result.constraint_score == 0.0
    assert exc_info.value.result.failing_dimension == "constraint"


def test_checker_intent_misalignment_raises_drift():
    checker = TrajectoryChecker(_make_spec())
    with pytest.raises(DriftDetected) as exc_info:
        with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.2), \
             patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
            checker.check(FakeTextNode("completely unrelated output"))
    assert exc_info.value.result.intent_score == 0.2
    assert exc_info.value.result.failing_dimension == "intent"
    assert exc_info.value.result.score == 0.2


# ---------------------------------------------------------------------------
# failing_dimension priority
# ---------------------------------------------------------------------------

def test_failing_dimension_tool_beats_constraint_when_both_zero():
    spec = _make_spec(allowed_tools=["safe"], constraints=["do not do x"])
    checker = TrajectoryChecker(spec)
    with pytest.raises(DriftDetected) as exc_info:
        with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
             patch("ballast.core.trajectory.score_constraint_violation", return_value=0.0):
            checker.check(FakeToolNode("forbidden"))
    result = exc_info.value.result
    # tool=0.0, constraint=0.0, intent=1.0 → tool priority
    assert result.failing_dimension == "tool"


def test_failing_dimension_constraint_beats_intent_when_equal():
    # Regression: constraint_score == intent_score == aggregate; constraint has priority
    spec = _make_spec(constraints=["do not write files"])
    checker = TrajectoryChecker(spec)
    with pytest.raises(DriftDetected) as exc_info:
        with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.5), \
             patch("ballast.core.trajectory.score_constraint_violation", return_value=0.5):
            checker.check(FakeTextNode("I modified a file"))
    assert exc_info.value.result.failing_dimension == "constraint"


def test_failing_dimension_none_when_all_pass():
    checker = TrajectoryChecker(_make_spec())
    with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
         patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
        result = checker.check(FakeTextNode("word count returned"))
    assert result.failing_dimension == "none"
    assert result.score == 1.0


# ---------------------------------------------------------------------------
# DriftResult fields
# ---------------------------------------------------------------------------

def test_drift_result_spec_version_matches_spec():
    spec = _make_spec()
    checker = TrajectoryChecker(spec)
    with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
         patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
        result = checker.check(FakeToolNode("t"))
    assert result.spec_version == spec.version


def test_drift_result_threshold_matches_spec_drift_threshold():
    spec = _make_spec(drift_threshold=0.6)
    checker = TrajectoryChecker(spec)
    with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
         patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
        result = checker.check(FakeToolNode("t"))
    assert result.threshold == 0.6


def test_drift_result_raised_at_step_increments():
    checker = TrajectoryChecker(_make_spec(drift_threshold=0.0))  # threshold=0 → never raises
    with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
         patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
        r1 = checker.check(FakeTextNode("step 1"))
        r2 = checker.check(FakeTextNode("step 2"))
    assert r1.raised_at_step == 1
    assert r2.raised_at_step == 2


def test_drift_detected_message_contains_step_and_failing():
    spec = _make_spec(allowed_tools=["safe"])
    checker = TrajectoryChecker(spec)
    try:
        with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
             patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
            checker.check(FakeToolNode("forbidden"))
    except DriftDetected as e:
        assert "step 1" in str(e)
        assert "tool" in str(e)


# ---------------------------------------------------------------------------
# Integration test — requires ANTHROPIC_API_KEY
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_trajectory_checker_real_llm():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    spec = _make_spec(
        allowed_tools=["get_word_count"],
        constraints=["do not modify any files"],
        drift_threshold=0.4,
    )
    checker = TrajectoryChecker(spec)
    node = FakeToolNode("get_word_count", {"text": "the quick brown fox"})
    result = checker.check(node)
    assert isinstance(result, DriftResult)
    assert result.tool_score == 1.0
    print(
        f"\nIntegration: score={result.score:.2f} "
        f"intent={result.intent_score:.2f} "
        f"tool={result.tool_score:.2f} "
        f"constraint={result.constraint_score:.2f} "
        f"failing={result.failing_dimension}"
    )
