"""Tests for ballast/core/checkpoint.py.

All tests use tmp_path fixture — never write to project root.
"""
import pytest

from ballast.core.checkpoint import BallastProgress, NodeSummary


def _make_node(index: int = 0, spec_hash: str = "abc00001") -> NodeSummary:
    return NodeSummary(
        index=index,
        tool_name="read_file",
        label="PROGRESSING",
        drift_score=0.9,
        cost_usd=0.001,
        verified=True,
        spec_hash=spec_hash,
        timestamp="2026-01-01T00:00:00Z",
    )


def _make_progress(spec_hash: str = "abc00001") -> BallastProgress:
    return BallastProgress(
        spec_hash=spec_hash,
        spec_intent="count words",
        run_id="run-001",
        started_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        remaining_success_criteria=["returns integer"],
    )


# ---------------------------------------------------------------------------
# __post_init__
# ---------------------------------------------------------------------------

def test_active_spec_hash_defaults_to_spec_hash():
    p = _make_progress("abc00001")
    assert p.active_spec_hash == "abc00001"


def test_active_spec_hash_explicit_value_preserved():
    p = BallastProgress(spec_hash="aaa", active_spec_hash="bbb")
    assert p.active_spec_hash == "bbb"


# ---------------------------------------------------------------------------
# write / read round-trip
# ---------------------------------------------------------------------------

def test_round_trip_empty_summaries(tmp_path):
    path = str(tmp_path / "progress.json")
    p = _make_progress()
    p.write(path)
    p2 = BallastProgress.read(path)
    assert p2 is not None
    assert p2.spec_hash == "abc00001"
    assert p2.active_spec_hash == "abc00001"
    assert p2.completed_node_summaries == []


def test_round_trip_with_node_summary(tmp_path):
    path = str(tmp_path / "progress.json")
    p = _make_progress()
    p.completed_node_summaries.append(_make_node(index=0, spec_hash="abc00001"))
    p.write(path)
    p2 = BallastProgress.read(path)
    assert len(p2.completed_node_summaries) == 1
    node = p2.completed_node_summaries[0]
    assert isinstance(node, NodeSummary)
    assert node.spec_hash == "abc00001"
    assert node.label == "PROGRESSING"
    assert node.index == 0


def test_node_summary_spec_hash_survives_round_trip(tmp_path):
    """Critical: per-node spec_hash (audit stamp) must survive write/read."""
    path = str(tmp_path / "progress.json")
    p = _make_progress("dispatch-hash")
    p.completed_node_summaries.append(_make_node(spec_hash="updated-hash"))
    p.write(path)
    p2 = BallastProgress.read(path)
    assert p2.completed_node_summaries[0].spec_hash == "updated-hash"


def test_read_returns_none_when_file_missing(tmp_path):
    result = BallastProgress.read(str(tmp_path / "nonexistent.json"))
    assert result is None


def test_read_returns_none_on_corrupt_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not valid json{{{", encoding="utf-8")
    assert BallastProgress.read(str(path)) is None


def test_spec_transitions_round_trip(tmp_path):
    path = str(tmp_path / "progress.json")
    p = _make_progress()
    p.spec_transitions.append({"at_node": 5, "from_hash": "aaa", "to_hash": "bbb"})
    p.write(path)
    p2 = BallastProgress.read(path)
    assert len(p2.spec_transitions) == 1
    assert p2.spec_transitions[0]["at_node"] == 5


# ---------------------------------------------------------------------------
# resume_context
# ---------------------------------------------------------------------------

def test_resume_context_contains_spec_hash_prefix():
    p = _make_progress("abcdef12")
    ctx = p.resume_context()
    assert "abcdef12"[:8] in ctx
    assert "BALLAST RESUME CONTEXT" in ctx
    assert "END RESUME CONTEXT" in ctx


def test_resume_context_shows_last_node_action():
    p = _make_progress()
    p.completed_node_summaries.append(_make_node(index=3))
    ctx = p.resume_context()
    assert "read_file" in ctx
    assert "PROGRESSING" in ctx


def test_resume_context_next_node_after_last_clean():
    p = _make_progress()
    p.last_clean_node_index = 7
    ctx = p.resume_context()
    assert "#8" in ctx
    assert "monotonic" in ctx.lower() or "audit" in ctx.lower()


def test_resume_context_no_summaries_shows_none():
    p = _make_progress()
    ctx = p.resume_context()
    assert "none" in ctx
