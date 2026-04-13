"""Tests for ballast/core/trajectory.py — mid-run drift detection.

Unit tests mock score_intent_alignment and score_constraint_violation.
score_tool_compliance is tested directly (pure Python, no LLM).
Integration test requires ANTHROPIC_API_KEY. Skip with: pytest -m 'not integration'
"""
import os
from unittest.mock import MagicMock, patch

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
    assert result.spec_version == spec.version_hash


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


# ---------------------------------------------------------------------------
# score_drift — label system (Step 2 additions)
# ---------------------------------------------------------------------------

import asyncio
from contextlib import asynccontextmanager

from ballast.core.checkpoint import BallastProgress
from ballast.core.trajectory import NodeAssessment, _compact_node, run_with_spec, score_drift

# Pre-built NodeAssessment stubs for run_with_spec mocks
_A_PROGRESSING = NodeAssessment(
    score=1.0, label="PROGRESSING", rationale="",
    tool_score=1.0, constraint_score=1.0, intent_score=1.0, tool_name="",
)
_A_VIOLATED = NodeAssessment(
    score=0.3, label="VIOLATED", rationale="bad",
    tool_score=1.0, constraint_score=0.3, intent_score=1.0, tool_name="",
)


def _make_spec_with_irreversible() -> SpecModel:
    return lock(SpecModel(
        intent="count words",
        success_criteria=["returns integer"],
        irreversible_actions=["send_email"],
        allowed_tools=["read_file"],
        drift_threshold=0.4,
    ))


def test_score_drift_irreversible_tool_returns_violated_irreversible():
    spec = _make_spec_with_irreversible()
    a = score_drift(FakeToolNode("send_email"), [], spec)
    assert a.label == "VIOLATED_IRREVERSIBLE"
    assert a.score == 0.0
    assert a.tool_name == "send_email"


def test_score_drift_forbidden_tool_returns_violated():
    spec = _make_spec_with_irreversible()
    a = score_drift(FakeToolNode("forbidden"), [], spec)
    assert a.label == "VIOLATED"
    assert a.score == 0.0
    assert a.tool_name == "forbidden"


def test_score_drift_clean_node_returns_progressing():
    spec = _make_spec_with_irreversible()
    with patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0), \
         patch("ballast.core.trajectory.score_intent_alignment", return_value=0.9):
        a = score_drift(FakeTextNode("good output"), [], spec)
    assert a.label == "PROGRESSING"
    assert a.score == 0.9
    assert "intent=" in a.rationale


def test_score_drift_borderline_returns_stalled():
    spec = _make_spec_with_irreversible()
    with patch("ballast.core.trajectory.score_constraint_violation", return_value=0.6), \
         patch("ballast.core.trajectory.score_intent_alignment", return_value=0.6):
        a = score_drift(FakeTextNode("unclear"), [], spec)
    assert a.label == "STALLED"
    assert 0.25 < a.score < 0.85


def test_score_drift_low_score_returns_violated():
    spec = _make_spec_with_irreversible()
    with patch("ballast.core.trajectory.score_constraint_violation", return_value=0.1), \
         patch("ballast.core.trajectory.score_intent_alignment", return_value=0.9):
        a = score_drift(FakeTextNode("bad action"), [], spec)
    assert a.label == "VIOLATED"
    assert a.score <= 0.25


def test_score_drift_empty_node_returns_progressing():
    spec = _make_spec_with_irreversible()
    a = score_drift(FakeEmptyNode(), [], spec)
    assert a.label == "PROGRESSING"
    assert a.score == 1.0


def test_compact_node_returns_expected_keys():
    compact = _compact_node(FakeTextNode("some output"), 0.9, "PROGRESSING", 0.001, True)
    assert set(compact.keys()) == {"tool_name", "label", "score", "cost_usd", "verified", "summary"}
    assert compact["label"] == "PROGRESSING"
    assert compact["score"] == 0.9
    assert "some output" in compact["summary"]


# ---------------------------------------------------------------------------
# run_with_spec — orchestration loop (Step 3 additions)
# ---------------------------------------------------------------------------


class _RwsNode:
    """Minimal stand-in for a pydantic-ai node (no tool, no content)."""


class _RwsAgentRun:
    """Mock AgentRun for run_with_spec tests.

    Exposes get_output() so the preferred output-extraction branch fires.
    Without get_output(), the fallback uses result.data which auto-exists
    as a MagicMock sub-attribute — causing assertion failures on string equality.
    """

    def __init__(self, nodes, output="done"):
        self._nodes = nodes
        self._output = output
        self.message_history = []

        state = MagicMock()
        state.message_history = self.message_history
        ctx = MagicMock()
        ctx.state = state
        self._ctx = ctx

    @property
    def ctx(self):
        return self._ctx

    async def get_output(self):
        return self._output

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for node in self._nodes:
            yield node


def _rws_make_agent(nodes, output="done"):
    """Return (mock_agent, mock_run) for run_with_spec tests."""
    run = _RwsAgentRun(nodes, output)
    agent = MagicMock()

    @asynccontextmanager
    async def _iter(task):
        yield run

    agent.iter = _iter
    return agent, run


def _rws_make_poller(return_values):
    poller = MagicMock()
    poller.poll.side_effect = return_values
    return poller


def test_run_with_spec_requires_locked_spec():
    draft = SpecModel(intent="x", success_criteria=["y"])
    agent, _ = _rws_make_agent([])
    with pytest.raises(ValueError, match="locked"):
        asyncio.run(run_with_spec(agent, "task", draft))


def test_run_with_spec_returns_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec = _make_spec()
    nodes = [_RwsNode(), _RwsNode()]
    agent, _ = _rws_make_agent(nodes, output="my result")
    with patch("ballast.core.trajectory.score_drift", return_value=_A_PROGRESSING):
        out = asyncio.run(run_with_spec(agent, "task", spec))
    assert out == "my result"


def test_run_with_spec_writes_checkpoint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec = _make_spec()
    nodes = [_RwsNode()]
    agent, _ = _rws_make_agent(nodes)
    with patch("ballast.core.trajectory.score_drift", return_value=_A_PROGRESSING):
        asyncio.run(run_with_spec(agent, "task", spec))
    progress = BallastProgress.read(str(tmp_path / "ballast-progress.json"))
    assert progress is not None
    assert progress.is_complete is True
    assert len(progress.completed_node_summaries) == 1


def test_run_with_spec_node_summary_uses_active_spec_hash(tmp_path, monkeypatch):
    """Critical: NodeSummary.spec_hash must be active_spec.version_hash, not dispatch hash."""
    monkeypatch.chdir(tmp_path)
    spec = lock(SpecModel(intent="Task A", success_criteria=["done A"]))
    spec_v2 = lock(SpecModel(intent="Task B", success_criteria=["done B"]))
    assert spec.version_hash != spec_v2.version_hash

    nodes = [_RwsNode(), _RwsNode()]
    agent, _ = _rws_make_agent(nodes)
    # spec_v2 returned at node 0 poll; None at node 1
    poller = _rws_make_poller([spec_v2, None])

    with patch("ballast.core.trajectory.score_drift", return_value=_A_PROGRESSING):
        asyncio.run(run_with_spec(agent, "task", spec, poller=poller))

    progress = BallastProgress.read(str(tmp_path / "ballast-progress.json"))
    # Both nodes must be stamped with spec_v2 (active after node-0 poll)
    assert progress.completed_node_summaries[0].spec_hash == spec_v2.version_hash
    assert progress.completed_node_summaries[1].spec_hash == spec_v2.version_hash


def test_run_with_spec_violation_increments_counter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec = _make_spec(drift_threshold=0.7)
    nodes = [_RwsNode()]
    agent, _ = _rws_make_agent(nodes)
    with patch("ballast.core.trajectory.score_drift", return_value=_A_VIOLATED):
        asyncio.run(run_with_spec(agent, "task", spec))
    progress = BallastProgress.read(str(tmp_path / "ballast-progress.json"))
    assert progress.total_violations == 1
    assert progress.total_drift_events == 1


def test_run_with_spec_no_poller_skips_injection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec = _make_spec()
    nodes = [_RwsNode()]
    agent, run = _rws_make_agent(nodes)
    with patch("ballast.core.trajectory.score_drift", return_value=_A_PROGRESSING):
        asyncio.run(run_with_spec(agent, "task", spec))
    assert run.message_history == []
