"""tests/test_guardrails.py — Unit tests for ballast/core/guardrails.py.

Tests three public symbols: build_correction, HardInterrupt, can_resume.
NodeAssessment is imported from trajectory (todo-1 pre-condition).
BallastProgress helpers are self-contained within this file.
"""
import pytest

from ballast.core.checkpoint import BallastProgress
from ballast.core.guardrails import HardInterrupt, build_correction, can_resume
from ballast.core.spec import SpecModel, lock
from ballast.core.trajectory import NodeAssessment


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_spec(**kwargs) -> SpecModel:
    """Return a locked SpecModel. Pass field=value kwargs to override defaults."""
    base = dict(
        intent="summarise the quarterly report without accessing external APIs",
        success_criteria=["summary written to output.txt"],
        constraints=["must not call external APIs"],
        irreversible_actions=["send_email", "delete_file"],
        drift_threshold=0.4,
        allowed_tools=["read_file", "write_file"],
    )
    base.update(kwargs)
    return lock(SpecModel(**base))


def _make_assessment(**overrides) -> NodeAssessment:
    defaults = dict(
        score=0.3,
        label="VIOLATED",
        rationale="action breaches the no-external-APIs constraint",
        tool_score=1.0,
        constraint_score=0.3,
        intent_score=1.0,
        tool_name="",
    )
    defaults.update(overrides)
    return NodeAssessment(**defaults)


def _make_progress(spec: SpecModel, **overrides) -> BallastProgress:
    raw = dict(
        spec_hash=spec.version_hash,
        active_spec_hash=spec.version_hash,
        spec_intent=spec.intent,
        run_id="test-run-abc",
        started_at="2026-04-12T00:00:00+00:00",
        updated_at="2026-04-12T00:00:00+00:00",
        last_clean_node_index=5,
        remaining_success_criteria=list(spec.success_criteria),
    )
    raw.update(overrides)
    return BallastProgress(**raw)


# ---------------------------------------------------------------------------
# build_correction tests
# ---------------------------------------------------------------------------

class TestBuildCorrection:
    def test_starts_with_ballast_prefix(self):
        spec = _make_spec()
        a = _make_assessment()
        result = build_correction(a, spec, node_index=7)
        assert result.startswith("[BALLAST CORRECTION]")

    def test_contains_node_index(self):
        spec = _make_spec()
        a = _make_assessment()
        result = build_correction(a, spec, node_index=12)
        assert "node 12" in result

    def test_contains_score(self):
        spec = _make_spec()
        a = _make_assessment(score=0.27)
        result = build_correction(a, spec, node_index=0)
        assert "0.27" in result

    def test_contains_label(self):
        spec = _make_spec()
        a = _make_assessment(label="STALLED")
        result = build_correction(a, spec, node_index=0)
        assert "STALLED" in result

    def test_contains_rationale(self):
        spec = _make_spec()
        a = _make_assessment(rationale="tool not in allowed list")
        result = build_correction(a, spec, node_index=0)
        assert "tool not in allowed list" in result

    def test_contains_spec_intent(self):
        spec = _make_spec()
        a = _make_assessment()
        result = build_correction(a, spec, node_index=0)
        assert spec.intent[:50] in result

    def test_intent_truncated_to_200_chars(self):
        long_intent = "x" * 300
        spec = _make_spec(intent=long_intent)
        a = _make_assessment()
        result = build_correction(a, spec, node_index=0)
        # Exactly the first 200 chars appear; the 201st does not follow them
        assert "x" * 200 in result
        assert "x" * 201 not in result

    def test_contains_spec_version_hash_prefix(self):
        spec = _make_spec()
        a = _make_assessment()
        result = build_correction(a, spec, node_index=0)
        assert spec.version_hash[:8] in result

    def test_includes_tool_name_when_present(self):
        spec = _make_spec()
        a = _make_assessment(tool_name="read_external_api")
        result = build_correction(a, spec, node_index=0)
        assert "read_external_api" in result

    def test_omits_tool_line_when_tool_name_empty(self):
        spec = _make_spec()
        a = _make_assessment(tool_name="")
        result = build_correction(a, spec, node_index=0)
        # Tool line is omitted — only present for non-empty tool_name
        assert "Tool called:" not in result

    def test_ends_with_continue_directive(self):
        spec = _make_spec()
        a = _make_assessment()
        result = build_correction(a, spec, node_index=0)
        assert result.strip().endswith(
            "[Continue from current position. Do not restart the task.]"
        )


# ---------------------------------------------------------------------------
# HardInterrupt tests
# ---------------------------------------------------------------------------

class TestHardInterrupt:
    def test_is_exception_subclass(self):
        spec = _make_spec()
        a = _make_assessment(label="VIOLATED_IRREVERSIBLE", tool_name="send_email")
        exc = HardInterrupt(a, spec, node_index=23)
        assert isinstance(exc, Exception)

    def test_carries_assessment(self):
        spec = _make_spec()
        a = _make_assessment(label="VIOLATED_IRREVERSIBLE", tool_name="delete_file")
        exc = HardInterrupt(a, spec, node_index=5)
        assert exc.assessment is a

    def test_carries_spec(self):
        spec = _make_spec()
        a = _make_assessment(label="VIOLATED_IRREVERSIBLE", tool_name="send_email")
        exc = HardInterrupt(a, spec, node_index=5)
        assert exc.spec is spec

    def test_carries_node_index(self):
        spec = _make_spec()
        a = _make_assessment(label="VIOLATED_IRREVERSIBLE", tool_name="send_email")
        exc = HardInterrupt(a, spec, node_index=42)
        assert exc.node_index == 42

    def test_str_contains_tool_name(self):
        spec = _make_spec()
        a = _make_assessment(label="VIOLATED_IRREVERSIBLE", tool_name="send_email")
        exc = HardInterrupt(a, spec, node_index=0)
        assert "send_email" in str(exc)

    def test_str_contains_spec_version_prefix(self):
        spec = _make_spec()
        a = _make_assessment(label="VIOLATED_IRREVERSIBLE", tool_name="send_email")
        exc = HardInterrupt(a, spec, node_index=0)
        assert spec.version_hash[:8] in str(exc)

    def test_is_raiseable(self):
        spec = _make_spec()
        a = _make_assessment(label="VIOLATED_IRREVERSIBLE", tool_name="delete_file")
        with pytest.raises(HardInterrupt) as exc_info:
            raise HardInterrupt(a, spec, node_index=7)
        assert exc_info.value.node_index == 7
        assert exc_info.value.assessment is a


# ---------------------------------------------------------------------------
# can_resume tests
# ---------------------------------------------------------------------------

class TestCanResume:
    def test_returns_true_when_progress_matches(self):
        spec = _make_spec()
        progress = _make_progress(spec)
        assert can_resume(progress, spec) is True

    def test_returns_false_when_progress_is_none(self):
        spec = _make_spec()
        assert can_resume(None, spec) is False

    def test_returns_false_when_spec_hash_differs(self):
        spec = _make_spec()
        other_spec = _make_spec(intent="completely different intent")
        progress = _make_progress(other_spec)  # checkpoint from a different spec
        assert can_resume(progress, spec) is False

    def test_returns_false_when_run_is_complete(self):
        spec = _make_spec()
        progress = _make_progress(spec, is_complete=True)
        assert can_resume(progress, spec) is False

    def test_returns_false_when_hash_differs_and_complete(self):
        spec = _make_spec()
        other_spec = _make_spec(intent="other")
        progress = _make_progress(other_spec, is_complete=True)
        assert can_resume(progress, spec) is False
