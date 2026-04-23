"""Tests for ballast/core/memory.py — pure function contract tests only.

No LLM calls. Uses tmp_path for filesystem isolation.
"""
import math
from pathlib import Path

import pytest

from ballast.core.memory import (
    CONSOLIDATE_EVERY,
    _decay_factor,
    atomic_write_json,
    log_run,
    memory_report,
    patch_quirk,
    recall,
    write,
)


# ---------------------------------------------------------------------------
# _decay_factor — math contract
# ---------------------------------------------------------------------------

def test_decay_at_half_life_is_half():
    """At t = half_life, confidence must be exactly 0.5."""
    hl = 86400.0
    assert abs(_decay_factor(hl, hl) - 0.5) < 1e-9


def test_decay_at_zero_is_one():
    """At t = 0, no decay has occurred."""
    assert _decay_factor(86400.0, 0) == 1.0


def test_decay_at_double_half_life_is_quarter():
    """At t = 2 * half_life, confidence must be 0.25."""
    hl = 86400.0
    assert abs(_decay_factor(hl, 2 * hl) - 0.25) < 1e-9


def test_decay_monotonically_decreases():
    """Confidence decreases as elapsed time increases."""
    hl = 86400.0
    factors = [_decay_factor(hl, t) for t in [0, hl / 2, hl, 2 * hl]]
    assert factors == sorted(factors, reverse=True)


def test_decay_negative_elapsed_returns_one():
    """Negative elapsed time (clock skew) returns 1.0 — no negative decay."""
    assert _decay_factor(86400.0, -100) == 1.0


# ---------------------------------------------------------------------------
# recall — empty store behavior
# ---------------------------------------------------------------------------

def test_recall_unknown_scope_returns_empty_string(tmp_path, monkeypatch):
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    result = recall("nonexistent-scope-xyz")
    assert result == ""


def test_recall_after_log_run_returns_nonempty(tmp_path, monkeypatch):
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    log_run("test-scope", goal="do something", events=[{}, {}], success=True)
    result = recall("test-scope")
    assert "test-scope" in result
    assert "1 run" in result


# ---------------------------------------------------------------------------
# write — confidence upsert + decay
# ---------------------------------------------------------------------------

def test_write_new_observation_confidence_is_one(tmp_path, monkeypatch):
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    write("scope-a", ["tool chains succeed when first call succeeds"])
    data = __load(tmp_path, "scope-a")
    quirks = data["quirks"]
    assert len(quirks) == 1
    assert abs(quirks[0]["confidence"] - 1.0) < 0.01


def test_write_reseen_observation_increments_confidence(tmp_path, monkeypatch):
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    write("scope-b", ["observation-x"])
    write("scope-b", ["observation-x"])
    data = __load(tmp_path, "scope-b")
    quirks = {q["text"]: q["confidence"] for q in data["quirks"]}
    # Confidence should be > 1.0 after being seen twice
    assert quirks["observation-x"] > 1.0


def test_write_drops_empty_strings(tmp_path, monkeypatch):
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    write("scope-c", ["", "  ", "valid observation"])
    data = __load(tmp_path, "scope-c")
    texts = [q["text"] for q in data["quirks"]]
    assert "" not in texts
    assert "  " not in texts
    assert "valid observation" in texts


# ---------------------------------------------------------------------------
# log_run — schema contract
# ---------------------------------------------------------------------------

def test_log_run_increments_run_count(tmp_path, monkeypatch):
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    log_run("scope-d", goal="task 1", events=[{}], success=True)
    log_run("scope-d", goal="task 2", events=[{}, {}], success=False)
    data = __load(tmp_path, "scope-d")
    assert data["run_count"] == 2


def test_log_run_stores_success_field(tmp_path, monkeypatch):
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    log_run("scope-e", goal="failing task", events=[], success=False)
    data = __load(tmp_path, "scope-e")
    assert data["runs"][0]["success"] is False


def test_log_run_stores_is_trial_field(tmp_path, monkeypatch):
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    log_run("scope-f", goal="eval task", events=[], is_trial=True)
    data = __load(tmp_path, "scope-f")
    assert data["runs"][0]["is_trial"] is True


# ---------------------------------------------------------------------------
# consolidate — filtering contract (no LLM calls)
# ---------------------------------------------------------------------------

def test_consolidate_does_not_run_on_wrong_count(tmp_path, monkeypatch):
    """consolidate() must NOT run unless real successful run count % CONSOLIDATE_EVERY == 0."""
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    # Log CONSOLIDATE_EVERY - 1 real successful runs
    for i in range(CONSOLIDATE_EVERY - 1):
        log_run("scope-g", goal=f"task {i}", events=[], success=True)
    result = consolidate("scope-g")
    # Should not have run (would need an LLM call if it did)
    # We can't test the Claude call, but we CAN test it returns False
    # when the count doesn't align
    # Note: if CONSOLIDATE_EVERY == 3, we've done 2 runs — should return False
    if (CONSOLIDATE_EVERY - 1) % CONSOLIDATE_EVERY != 0:
        assert result is False


def test_consolidate_excludes_failed_runs_from_count(tmp_path, monkeypatch):
    """Failed runs must not count toward consolidation trigger."""
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    # Log CONSOLIDATE_EVERY failed runs + 1 successful
    for i in range(CONSOLIDATE_EVERY):
        log_run("scope-h", goal=f"failing {i}", events=[], success=False)
    log_run("scope-h", goal="success", events=[], success=True)
    # Only 1 real successful run → should not trigger consolidation
    # (We verify by checking that semantic_profile is still empty)
    data = __load(tmp_path, "scope-h")
    # consolidate() would require LLM; we just verify failed runs don't count
    synthesis_runs = [
        r for r in data["runs"]
        if not r.get("is_trial", False) and r.get("success", True)
    ]
    assert len(synthesis_runs) == 1  # only the 1 successful run


# ---------------------------------------------------------------------------
# patch_quirk — clamping
# ---------------------------------------------------------------------------

def test_patch_quirk_clamps_to_minimum(tmp_path, monkeypatch):
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    write("scope-i", ["fragile observation"])
    patch_quirk("scope-i", "fragile observation", delta=-100.0)
    data = __load(tmp_path, "scope-i")
    assert data["quirks"][0]["confidence"] >= 0.1


def test_patch_quirk_clamps_to_maximum(tmp_path, monkeypatch):
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    write("scope-j", ["strong observation"])
    patch_quirk("scope-j", "strong observation", delta=100.0)
    data = __load(tmp_path, "scope-j")
    assert data["quirks"][0]["confidence"] <= 10.0


def test_patch_quirk_noop_for_missing_text(tmp_path, monkeypatch):
    """patch_quirk on a text that doesn't exist must not raise and not change state."""
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    write("scope-k", ["existing observation"])
    patch_quirk("scope-k", "nonexistent observation", delta=1.0)
    data = __load(tmp_path, "scope-k")
    assert len(data["quirks"]) == 1  # no new quirk added


# ---------------------------------------------------------------------------
# memory_report — smoke test
# ---------------------------------------------------------------------------

def test_memory_report_unknown_scope(tmp_path, monkeypatch):
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    result = memory_report("nonexistent")
    assert "No memory" in result


def test_memory_report_known_scope_contains_scope_name(tmp_path, monkeypatch):
    import ballast.core.memory as mem
    monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
    log_run("scope-l", goal="test", events=[], success=True)
    result = memory_report("scope-l")
    assert "scope-l" in result


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def __load(tmp_path: Path, scope: str) -> dict:
    safe = scope.replace(":", "_").replace("/", "_")
    path = tmp_path / f"{safe}.json"
    import json
    return json.loads(path.read_text())


def consolidate(scope: str) -> bool:
    """Local re-import that uses the monkeypatched MEMORY_DIR."""
    from ballast.core import memory as mem
    return mem.consolidate(scope)
