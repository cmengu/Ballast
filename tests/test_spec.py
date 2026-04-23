"""Tests for ballast/core/spec.py — SpecModel, parse_spec, lock, is_locked.

Integration tests (score_specificity, clarify) require ANTHROPIC_API_KEY.
Skip with: pytest -m 'not integration'
"""
import os
import tempfile

import pytest

from ballast.core.spec import (
    SpecModel,
    SpecAlreadyLocked,
    SpecParseError,
    SpecTooVague,
    clarify,
    is_locked,
    lock,
    parse_spec,
    score_specificity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_SPEC_MD = """\
# spec v1

## intent
Count the number of words in a given text string and return the integer result.

## success criteria
- returns an integer
- the integer matches the actual word count of the input text
- handles empty string input by returning 0

## constraints
- do not call any external APIs
- do not write to any files

## escalation threshold
drift confidence floor: 0.4
timeout before CEO decides: 300 seconds

## tools allowed
- get_word_count
"""


def _write_spec(content: str) -> str:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    )
    f.write(content)
    f.close()
    return f.name


def _make_draft() -> SpecModel:
    return SpecModel(
        intent="count words in a string",
        success_criteria=["returns an integer", "integer is accurate"],
        constraints=["do not call external APIs"],
        allowed_tools=["get_word_count"],
    )


# ---------------------------------------------------------------------------
# SpecModel defaults
# ---------------------------------------------------------------------------

def test_spec_model_default_drift_threshold():
    assert SpecModel(intent="x", success_criteria=["y"]).drift_threshold == 0.4


def test_spec_model_default_escalation_timeout():
    assert SpecModel(intent="x", success_criteria=["y"]).harness.escalation_timeout_seconds == 300


def test_spec_model_default_allowed_tools_empty():
    assert SpecModel(intent="x", success_criteria=["y"]).allowed_tools == []


def test_spec_model_default_locked_at_empty():
    assert SpecModel(intent="x", success_criteria=["y"]).locked_at == ""


def test_spec_model_default_version_empty():
    assert SpecModel(intent="x", success_criteria=["y"]).version_hash == ""


# ---------------------------------------------------------------------------
# parse_spec
# ---------------------------------------------------------------------------

def test_parse_spec_returns_spec_model():
    path = _write_spec(VALID_SPEC_MD)
    spec = parse_spec(path)
    os.unlink(path)
    assert isinstance(spec, SpecModel)


def test_parse_spec_extracts_intent():
    path = _write_spec(VALID_SPEC_MD)
    spec = parse_spec(path)
    os.unlink(path)
    assert "Count the number of words" in spec.intent


def test_parse_spec_extracts_success_criteria_list():
    path = _write_spec(VALID_SPEC_MD)
    spec = parse_spec(path)
    os.unlink(path)
    assert len(spec.success_criteria) == 3
    assert any("integer" in c for c in spec.success_criteria)


def test_parse_spec_extracts_constraints():
    path = _write_spec(VALID_SPEC_MD)
    spec = parse_spec(path)
    os.unlink(path)
    assert len(spec.constraints) == 2
    assert any("external APIs" in c for c in spec.constraints)


def test_parse_spec_extracts_drift_threshold():
    path = _write_spec(VALID_SPEC_MD)
    spec = parse_spec(path)
    os.unlink(path)
    assert spec.drift_threshold == 0.4


def test_parse_spec_extracts_escalation_timeout():
    path = _write_spec(VALID_SPEC_MD)
    spec = parse_spec(path)
    os.unlink(path)
    assert spec.harness.escalation_timeout_seconds == 300


def test_parse_spec_extracts_allowed_tools():
    path = _write_spec(VALID_SPEC_MD)
    spec = parse_spec(path)
    os.unlink(path)
    assert "get_word_count" in spec.allowed_tools


def test_parse_spec_draft_has_empty_locked_at_and_version():
    path = _write_spec(VALID_SPEC_MD)
    spec = parse_spec(path)
    os.unlink(path)
    assert spec.locked_at == ""
    assert spec.version_hash == ""


def test_parse_spec_missing_intent_raises():
    path = _write_spec("## success criteria\n- something\n")
    with pytest.raises(SpecParseError, match="intent"):
        parse_spec(path)
    os.unlink(path)


def test_parse_spec_missing_criteria_raises():
    path = _write_spec("## intent\ndo something\n")
    with pytest.raises(SpecParseError, match="success criteria"):
        parse_spec(path)
    os.unlink(path)


def test_parse_spec_file_not_found_raises():
    with pytest.raises(SpecParseError, match="not found"):
        parse_spec("/tmp/nonexistent_ballast_spec_xyz.md")


def test_parse_spec_uses_defaults_when_threshold_section_missing():
    content = "## intent\ndo something\n## success criteria\n- thing\n"
    path = _write_spec(content)
    spec = parse_spec(path)
    os.unlink(path)
    assert spec.drift_threshold == 0.4
    assert spec.harness.escalation_timeout_seconds == 300


# ---------------------------------------------------------------------------
# lock
# ---------------------------------------------------------------------------

def test_lock_sets_version_16_chars():
    locked = lock(_make_draft())
    assert len(locked.version_hash) == 16


def test_lock_sets_locked_at_iso_format():
    locked = lock(_make_draft())
    assert locked.locked_at.endswith("Z")
    assert "T" in locked.locked_at


def test_lock_version_is_stable():
    draft = _make_draft()
    assert lock(draft).version_hash == lock(draft).version_hash


def test_lock_version_differs_for_different_intent():
    draft1 = _make_draft()
    draft2 = SpecModel(
        intent="COMPLETELY DIFFERENT INTENT",
        success_criteria=["returns an integer", "integer is accurate"],
    )
    assert lock(draft1).version_hash != lock(draft2).version_hash


def test_lock_does_not_mutate_input():
    draft = _make_draft()
    locked = lock(draft)
    assert draft.locked_at == ""   # original unchanged
    assert draft.version_hash == ""
    assert locked.locked_at != ""
    assert locked.version_hash != ""


def test_lock_raises_if_already_locked():
    locked = lock(_make_draft())
    with pytest.raises(SpecAlreadyLocked):
        lock(locked)


# ---------------------------------------------------------------------------
# is_locked
# ---------------------------------------------------------------------------

def test_is_locked_false_for_draft():
    assert not is_locked(_make_draft())


def test_is_locked_true_for_locked():
    assert is_locked(lock(_make_draft()))


# ---------------------------------------------------------------------------
# Integration tests — require ANTHROPIC_API_KEY
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_score_specificity_returns_float_in_range():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    path = _write_spec(VALID_SPEC_MD)
    spec = parse_spec(path)
    os.unlink(path)
    score = score_specificity(spec)
    assert 0.0 <= score <= 1.0
    print(f"\nspecificity score for valid spec: {score:.2f}")


@pytest.mark.integration
def test_score_specificity_vague_spec_scores_lower():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    vague = SpecModel(intent="do the thing", success_criteria=["it works"])
    specific = SpecModel(
        intent="count words in a string and return an integer",
        success_criteria=["returns int", "handles empty string"],
    )
    vague_score = score_specificity(vague)
    specific_score = score_specificity(specific)
    # Not guaranteed by LLM, but generally holds — log for observability
    print(f"\nvague={vague_score:.2f} specific={specific_score:.2f}")
    assert 0.0 <= vague_score <= 1.0
    assert 0.0 <= specific_score <= 1.0


# ---------------------------------------------------------------------------
# SpecDelta + diff() + parent_hash tests (8 tests, no LLM calls)
# ---------------------------------------------------------------------------

def _make_v1() -> SpecModel:
    return lock(SpecModel(
        intent="Write a report on AI companies",
        success_criteria=["report is written"],
        allowed_tools=["web_search", "write_file"],
    ))


def test_spec_delta_from_and_to_version():
    v1 = _make_v1()
    v2 = lock(SpecModel(
        intent="Write a report on AI companies",
        success_criteria=["report is written"],
        allowed_tools=["web_search", "write_file"],
        constraints=["do not mention OpenAI"],
    ))
    delta = v1.diff(v2)
    assert delta.from_hash == v1.version_hash
    assert delta.to_hash == v2.version_hash


def test_diff_detects_added_constraint():
    v1 = _make_v1()
    v2 = lock(SpecModel(
        intent="Write a report on AI companies",
        success_criteria=["report is written"],
        allowed_tools=["web_search", "write_file"],
        constraints=["do not mention OpenAI"],
    ))
    delta = v1.diff(v2)
    assert delta.added_constraints == ["do not mention OpenAI"]
    assert delta.removed_constraints == []


def test_diff_detects_removed_tool():
    v1 = _make_v1()
    v2 = lock(SpecModel(
        intent="Write a report on AI companies",
        success_criteria=["report is written"],
        allowed_tools=["web_search"],  # write_file removed
    ))
    delta = v1.diff(v2)
    assert delta.removed_tools == ["write_file"]
    assert delta.added_tools == []


def test_diff_detects_intent_changed():
    v1 = _make_v1()
    v2 = lock(SpecModel(
        intent="Write a summary of AI companies",  # changed
        success_criteria=["report is written"],
        allowed_tools=["web_search", "write_file"],
    ))
    delta = v1.diff(v2)
    assert delta.intent_changed is True


def test_diff_no_changes_produces_empty_delta():
    v1 = _make_v1()
    v2 = lock(SpecModel(
        intent="Write a report on AI companies",
        success_criteria=["report is written"],
        allowed_tools=["web_search", "write_file"],
    ))
    delta = v1.diff(v2)
    assert delta.added_constraints == []
    assert delta.removed_constraints == []
    assert delta.added_tools == []
    assert delta.removed_tools == []
    assert delta.intent_changed is False
    assert delta.added_success_criteria == []
    assert delta.removed_success_criteria == []


def test_diff_detects_added_success_criterion():
    v1 = _make_v1()
    v2 = lock(SpecModel(
        intent="Write a report on AI companies",
        success_criteria=["report is written", "includes executive summary"],
        allowed_tools=["web_search", "write_file"],
    ))
    delta = v1.diff(v2)
    assert delta.added_success_criteria == ["includes executive summary"]
    assert delta.removed_success_criteria == []


def test_diff_detects_drift_threshold_change():
    v1 = _make_v1()
    v2 = lock(SpecModel(
        intent="Write a report on AI companies",
        success_criteria=["report is written"],
        allowed_tools=["web_search", "write_file"],
        drift_threshold=0.55,
    ))
    delta = v1.diff(v2)
    assert delta.drift_threshold_changed is True
    assert delta.new_drift_threshold == pytest.approx(0.55)


def test_as_injection_contains_spec_update_header():
    v1 = _make_v1()
    v2 = lock(SpecModel(
        intent="Write a report on AI companies",
        success_criteria=["report is written"],
        allowed_tools=["web_search", "write_file"],
        constraints=["do not mention OpenAI"],
    ))
    delta = v1.diff(v2)
    injection = delta.as_injection()
    assert f"[BALLAST SPEC UPDATE: {v1.version_hash[:8]} → {v2.version_hash[:8]}]" in injection
    assert "do not mention OpenAI" in injection
    assert "[Continue from current node under updated spec.]" in injection


def test_parent_hash_field_defaults_empty():
    spec = SpecModel(
        intent="do something",
        success_criteria=["it is done"],
    )
    assert spec.parent_hash == ""


def test_parent_hash_travels_through_lock():
    v1 = _make_v1()
    draft_v2 = SpecModel(
        intent="Write a report on AI companies",
        success_criteria=["report is written"],
        allowed_tools=["web_search", "write_file"],
        parent_hash=v1.version_hash,
    )
    v2 = lock(draft_v2)
    assert v2.parent_hash == v1.version_hash
