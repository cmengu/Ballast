"""Tests for ballast/core/spec.py — contract tests, no live LLM calls.

Integration smoke test (test_lock_spec_infer_integration) requires ANTHROPIC_API_KEY.
Mark with: pytest -m 'not integration' to skip it in CI.
"""
import tempfile
from pathlib import Path

import pytest
import ballast.core.memory as mem
from ballast.core.spec import (
    AmbiguityScore,
    AmbiguityScores,
    AmbiguityType,
    ClarificationPolicy,
    IntentSignal,
    RunPhaseTracker,
    LockedSpec,
    lock_spec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_score(axis: AmbiguityType, score: float, blocking: bool) -> AmbiguityScore:
    return AmbiguityScore(
        axis=axis, score=score, reason="test reason", is_blocking=blocking
    )


def _make_scores(attr=0.2, scope=0.2, pref=0.2,
                 attr_b=False, scope_b=False, pref_b=False) -> AmbiguityScores:
    return AmbiguityScores(
        attribute=_make_score(AmbiguityType.ATTRIBUTE, attr, attr_b),
        scope=_make_score(AmbiguityType.SCOPE, scope, scope_b),
        preference=_make_score(AmbiguityType.PREFERENCE, pref, pref_b),
    )


def _make_spec(**overrides) -> LockedSpec:
    defaults = dict(
        goal="test goal",
        domain="test",
        success_criteria="done",
        scope="",
        constraints=[],
        output_format="",
        inferred_assumptions=[],
        intent_signal=IntentSignal(
            latent_goal="test", action_type="COORDINATE", salient_entity_types=[]
        ),
        clarification_asked=False,
        threshold_used=0.60,
    )
    defaults.update(overrides)
    return LockedSpec(**defaults)


# ---------------------------------------------------------------------------
# AmbiguityScores — derived properties
# ---------------------------------------------------------------------------

def test_blocking_axes_filters_correctly():
    scores = _make_scores(attr=0.8, scope=0.2, pref=0.7, attr_b=True, pref_b=True)
    blocking = scores.blocking_axes
    assert len(blocking) == 2
    axes = {b.axis for b in blocking}
    assert AmbiguityType.ATTRIBUTE in axes
    assert AmbiguityType.PREFERENCE in axes
    assert AmbiguityType.SCOPE not in axes


def test_blocking_axes_empty_when_none_blocking():
    scores = _make_scores(attr=0.9, scope=0.9, pref=0.9)  # all non-blocking
    assert scores.blocking_axes == []


def test_max_score_returns_highest():
    scores = _make_scores(attr=0.3, scope=0.7, pref=0.5)
    assert scores.max_score == 0.7


def test_max_score_with_equal_axes():
    scores = _make_scores(attr=0.5, scope=0.5, pref=0.5)
    assert scores.max_score == 0.5


# ---------------------------------------------------------------------------
# IntentSignal — model defaults
# ---------------------------------------------------------------------------

def test_intent_signal_step_index_defaults_to_zero():
    sig = IntentSignal(latent_goal="test", action_type="READ", salient_entity_types=[])
    assert sig.step_index == 0


def test_intent_signal_salient_entity_types_defaults_to_empty():
    sig = IntentSignal(latent_goal="test", action_type="READ")
    assert sig.salient_entity_types == []


# ---------------------------------------------------------------------------
# ClarificationPolicy — threshold and decision logic
# ---------------------------------------------------------------------------

def test_policy_reads_default_threshold_for_new_domain(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    policy = ClarificationPolicy("brand-new-domain-xyz")
    assert policy.threshold == 0.60


def test_policy_should_ask_true_when_blocking_axis_at_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    scores = _make_scores(attr=0.60, attr_b=True)
    policy = ClarificationPolicy("domain-a")
    assert policy.should_ask(scores) is True


def test_policy_should_ask_false_when_blocking_axis_below_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    scores = _make_scores(attr=0.59, attr_b=True)
    policy = ClarificationPolicy("domain-b")
    assert policy.should_ask(scores) is False


def test_policy_should_ask_false_when_no_blocking_axes(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    # High scores but none blocking
    scores = _make_scores(attr=0.95, scope=0.95, pref=0.95)
    policy = ClarificationPolicy("domain-c")
    assert policy.should_ask(scores) is False


def test_policy_threshold_updates_via_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    # Simulate multiple infer+succeed runs → threshold relaxes upward
    for _ in range(5):
        mem.update_domain_threshold(
            "domain-d",
            clarification_asked=False,
            run_succeeded=True,
            max_ambiguity_score=0.80,
        )
    policy = ClarificationPolicy("domain-d")
    assert policy.threshold > 0.60, f"Expected > 0.60, got {policy.threshold}"


# ---------------------------------------------------------------------------
# LockedSpec — field invariants
# ---------------------------------------------------------------------------

def test_locked_spec_constructs_with_all_fields():
    spec = _make_spec()
    assert spec.goal == "test goal"
    assert spec.threshold_used == 0.60
    assert spec.clarification_asked is False


def test_locked_spec_inferred_assumptions_defaults_empty():
    spec = _make_spec()
    assert spec.inferred_assumptions == []


def test_locked_spec_is_mutable_for_tracker():
    """LockedSpec must be mutable so RunPhaseTracker can update intent_signal."""
    spec = _make_spec()
    spec.intent_signal.step_index = 5
    assert spec.intent_signal.step_index == 5


# ---------------------------------------------------------------------------
# RunPhaseTracker — state transition contract
# ---------------------------------------------------------------------------

def test_tracker_step_count_starts_at_zero():
    spec = _make_spec()
    tracker = RunPhaseTracker(spec)
    assert tracker.step_count == 0


def test_tracker_increments_step_on_every_event():
    spec = _make_spec()
    tracker = RunPhaseTracker(spec)
    for i in range(5):
        tracker.update({"event": "on_chain_stream"})
    assert tracker.step_count == 5
    assert spec.intent_signal.step_index == 5


def test_tracker_updates_action_type_from_tool_start():
    spec = _make_spec()
    tracker = RunPhaseTracker(spec)
    tracker.update({"event": "on_tool_start", "name": "my_tool"})
    assert spec.intent_signal.action_type == "WRITE"


def test_tracker_updates_action_type_from_tool_end():
    spec = _make_spec()
    tracker = RunPhaseTracker(spec)
    tracker.update({"event": "on_tool_end", "name": "my_tool"})
    assert spec.intent_signal.action_type == "VERIFY"


def test_tracker_appends_tool_name_to_salient_entities():
    spec = _make_spec()
    tracker = RunPhaseTracker(spec)
    tracker.update({"event": "on_tool_start", "name": "search_db"})
    tracker.update({"event": "on_tool_start", "name": "write_file"})
    assert "search_db" in spec.intent_signal.salient_entity_types
    assert "write_file" in spec.intent_signal.salient_entity_types


def test_tracker_does_not_duplicate_salient_entities():
    spec = _make_spec()
    tracker = RunPhaseTracker(spec)
    tracker.update({"event": "on_tool_start", "name": "same_tool"})
    tracker.update({"event": "on_tool_start", "name": "same_tool"})
    assert spec.intent_signal.salient_entity_types.count("same_tool") == 1


def test_tracker_handles_malformed_event_without_raising():
    spec = _make_spec()
    tracker = RunPhaseTracker(spec)
    tracker.update({})             # empty dict — event_type = ""
    tracker.update({"event": None})  # event_type = None, not in map
    tracker.update(None)           # type: ignore — AttributeError on .get(), caught by except
    # All three must be swallowed silently and still increment step counter
    assert tracker.step_count == 3


def test_tracker_intent_summary_contains_step_and_action():
    spec = _make_spec()
    tracker = RunPhaseTracker(spec)
    tracker.update({"event": "on_tool_start", "name": "my_tool"})
    summary = tracker.intent_summary()
    assert "[step 1]" in summary
    assert "WRITE" in summary


# ---------------------------------------------------------------------------
# lock_spec — non-interactive path (integration, requires ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_lock_spec_infer_integration():
    """Smoke test: lock_spec with interactive=False returns a LockedSpec.

    Requires ANTHROPIC_API_KEY. Skip with: pytest -m 'not integration'
    """
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    spec, questions = lock_spec(
        "count the words in the file readme.md",
        domain="coding",
        interactive=False,
    )
    assert isinstance(spec, LockedSpec)
    assert questions == []
    assert spec.success_criteria != ""
    assert spec.intent_signal.action_type in {
        "READ", "WRITE", "TRANSFORM", "VERIFY", "SEARCH", "COORDINATE"
    }
    assert spec.threshold_used > 0.0
    print(f"\nInferred spec:\n  success_criteria: {spec.success_criteria}")
    print(f"  scope: {spec.scope}")
    print(f"  intent: {spec.intent_signal.action_type} / {spec.intent_signal.latent_goal}")
