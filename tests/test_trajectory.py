"""Tests for ballast/core/trajectory.py — contract tests, no LLM calls."""
import tempfile
from pathlib import Path

import ballast.core.memory as mem
from ballast.core.spec import IntentSignal, LockedSpec
from ballast.core.trajectory import (
    TrajectoryReport,
    _extract_keywords,
    _keywords_present,
    validate_trajectory,
)


def _make_spec(success_criteria: str = "the word count was returned", domain: str = "test") -> LockedSpec:
    return LockedSpec(
        goal="count words",
        domain=domain,
        success_criteria=success_criteria,
        scope="",
        constraints=[],
        output_format="",
        inferred_assumptions=[],
        intent_signal=IntentSignal(
            latent_goal="word count", action_type="READ", salient_entity_types=[]
        ),
        clarification_asked=False,
        threshold_used=0.60,
    )


def _make_events(content: str) -> list[dict]:
    """Minimal on_chain_end event with message content."""
    return [
        {"event": "on_chain_start", "data": {}},
        {
            "event": "on_chain_end",
            "data": {"output": {"messages": [{"content": content}]}},
        },
    ]


# ---------------------------------------------------------------------------
# _extract_keywords
# ---------------------------------------------------------------------------

def test_extract_keywords_removes_stop_words():
    kws = _extract_keywords("the word count was returned")
    assert "the" not in kws
    assert "was" not in kws
    assert "word" in kws
    assert "count" in kws
    assert "returned" in kws


def test_extract_keywords_empty_string_returns_empty():
    assert _extract_keywords("") == []


def test_extract_keywords_short_words_excluded():
    kws = _extract_keywords("do it now")
    assert "it" not in kws  # len 2, excluded


# ---------------------------------------------------------------------------
# _keywords_present
# ---------------------------------------------------------------------------

def test_keywords_present_true_when_majority_match():
    assert _keywords_present(["word", "count", "returned"], "the word count was returned") is True


def test_keywords_present_false_when_none_match():
    assert _keywords_present(["database", "schema", "migration"], "the word count was 4") is False


def test_keywords_present_empty_keywords_returns_false():
    assert _keywords_present([], "anything") is False


# ---------------------------------------------------------------------------
# validate_trajectory
# ---------------------------------------------------------------------------

def test_validate_passes_when_criteria_keywords_in_output():
    spec = _make_spec("the word count was returned")
    events = _make_events("The word count is 4.")
    report = validate_trajectory(spec, events, update_calibration=False)
    assert isinstance(report, TrajectoryReport)
    assert report.passed is True
    assert report.event_count == 2


def test_validate_fails_when_criteria_keywords_not_in_output():
    spec = _make_spec("the word count was returned")
    events = _make_events("An error occurred during processing.")
    report = validate_trajectory(spec, events, update_calibration=False)
    assert report.passed is False


def test_validate_passes_when_no_keywords_extractable():
    spec = _make_spec("do it")  # all stop words / short words
    events = _make_events("Some output here.")
    report = validate_trajectory(spec, events, update_calibration=False)
    assert report.passed is True
    assert any("no extractable keywords" in n for n in report.notes)


def test_validate_empty_events_fails():
    spec = _make_spec("word count returned")
    report = validate_trajectory(spec, [], update_calibration=False)
    assert report.passed is False
    assert report.event_count == 0


def test_validate_wires_calibration(tmp_path, monkeypatch):
    """validate_trajectory with update_calibration=True calls update_domain_threshold."""
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    spec = _make_spec(success_criteria="word count returned", domain="test-calibration")
    events = _make_events("The word count was returned: 4 words.")
    report = validate_trajectory(spec, events, update_calibration=True)
    assert report.passed is True
    # Confirm calibration ran: threshold must differ from the 0.60 default.
    # _make_spec() sets ambiguity_scores=None → max_score=0.0 →
    # update rule: 0.60 + 0.05*(0.0-0.60) = 0.57 (tightens slightly).
    # We don't assert direction here — just that calibration fired and clamping holds.
    threshold = mem.get_domain_threshold("test-calibration")
    assert threshold != 0.60, "Threshold unchanged — update_domain_threshold was not called"
    assert 0.20 <= threshold <= 0.90, f"Threshold {threshold} outside clamped bounds"


def test_validate_report_contains_matched_fragment():
    spec = _make_spec("word count returned")
    events = _make_events("The word count was returned: 4 words.")
    report = validate_trajectory(spec, events, update_calibration=False)
    assert report.passed is True
    assert "word" in report.matched_in.lower() or len(report.matched_in) > 0


def test_validate_handles_langchain_message_objects():
    """_extract_content must handle LangChain AIMessage objects (not just dicts).

    Real LangGraph output from create_react_agent uses AIMessage objects.
    The dict path (isinstance(last, dict)) is the test-only path.
    The hasattr(last, "content") path is the production path.
    """
    class FakeAIMessage:
        def __init__(self, content: str):
            self.content = content

    spec = _make_spec("word count returned")
    events = [{
        "event": "on_chain_end",
        "data": {"output": {"messages": [FakeAIMessage("The word count was returned: 4.")]}},
    }]
    report = validate_trajectory(spec, events, update_calibration=False)
    assert report.passed is True, (
        "FakeAIMessage.content not extracted — hasattr(last, 'content') path is broken"
    )
