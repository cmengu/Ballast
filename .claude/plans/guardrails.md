# Feature Implementation Plan: guardrails.py

**Overall Progress:** `0% (0/3 steps done)`

---

## Decisions Log (pre-check resolutions)

| # | Flaw | Resolution applied |
|---|------|--------------------|
| 1 | `_make_spec` used `SpecModel.lock(raw)` — classmethod does not exist. `spec.py` exports a module-level `lock(spec: SpecModel)`. | Replaced with `lock(SpecModel(**base))` pattern; added `lock` to import in test file. Confirmed against `spec.py:492` and `test_trajectory.py:31`. |

---

## TLDR

Build `ballast/core/guardrails.py` — the module that owns soft correction injection, the hard-interrupt exception, and the resume-decision predicate. After this plan: `run_with_spec` delegates correction-string construction to `build_correction()` (replacing the inline TODO-Step-6 block), and the resume `if` condition is replaced by `can_resume()`. `HardInterrupt` is defined and tested but not yet wired into `run_with_spec` — that happens at Step 7 when `escalation.py` is built.

**Assumed pre-conditions (both todos already applied):**
- `score_drift()` returns `NodeAssessment`; `run_with_spec` uses `assessment = score_drift(...)` with `assessment.score`, `assessment.label`, `assessment.rationale`, `assessment.tool_name`.
- `cost_guard.check_and_record(agent_id, node_cost)` is the single call-site in `run_with_spec`.
- `seed_prior_spend` is wired into the resume branch.
- `_cost_usd_warned` flag exists before the loop.
- Test baseline is ≥ 147 (original 141 + todo-1 rewrites + todo-2 additions).

---

## Architecture Overview

**The problem this plan solves:**
`trajectory.py:run_with_spec` contains two inline implementations that belong elsewhere:
1. A hardcoded correction string built at lines `# TODO Step 6` — format logic mixed into the orchestration loop.
2. A three-part boolean condition `(progress and progress.spec_hash == spec.version_hash and not progress.is_complete)` — resume decision logic inline in the loop preamble.

**Patterns applied:**
- **Façade (`build_correction`):** All correction-string logic lives in one function. `run_with_spec` becomes a single-line caller. Future format changes — adding spec version, constraint list, etc. — require touching only `guardrails.py`, not the orchestration loop.
- **Predicate extraction (`can_resume`):** The resume condition is a business rule, not plumbing. Extracting it to `guardrails.py` lets tests verify the rule directly without running `run_with_spec`.
- **Typed exception (`HardInterrupt`):** The hard-interrupt signal needs a type so callers can `except HardInterrupt` without catching all exceptions. It is defined now and wired at Step 7.

**What stays unchanged:**
- `trajectory.py` — behaviour unchanged; only the import block, one `if` condition, and one inline block are replaced.
- `checkpoint.py`, `spec.py`, `sync.py`, `cost.py` — not touched.
- The VIOLATED_IRREVERSIBLE path in `run_with_spec` — still just logs and records. `HardInterrupt` is added to `guardrails.py` but NOT raised yet; that is Step 7.
- All existing tests — no behaviour changes, so no existing assertions break.

**What this plan adds:**
- `ballast/core/guardrails.py` — three public symbols: `build_correction`, `HardInterrupt`, `can_resume`.
- `tests/test_guardrails.py` — 23 unit tests covering all three symbols (11 + 7 + 5).
- Two replacements in `trajectory.py` (import block + inline correction + resume condition).

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|----------|----------------------|--------------------------|
| `guardrails.py` does NOT import `NodeAssessment` at module level | Import `NodeAssessment` from `trajectory.py` at top of `guardrails.py` | Creates a circular import: `trajectory → guardrails → trajectory`. Resolved by `TYPE_CHECKING` guard — annotation is a string at runtime. |
| `HardInterrupt` is NOT wired into `run_with_spec` at this step | Raise `HardInterrupt` for VIOLATED_IRREVERSIBLE now | Wiring requires escalation fallback logic (Step 7). Raising now without a catch would abort tests that exercise the VIOLATED_IRREVERSIBLE path. Keep the stub comment; Step 7 wires it. |
| `can_resume` takes `BallastProgress \| None` (not reads disk) | `can_resume(spec)` reads disk internally | Caller already holds `progress`; re-reading disk from inside the predicate adds I/O and makes the function impure. Predicate over already-read data is correct. |
| `build_correction` takes `NodeAssessment` (post-todo-1 type) | Take `score, label, rationale` as separate positional args | `NodeAssessment` carries `tool_name` too — eliminates a second argument. Consistent with the DTO design from todo-1. |

**Known limitations:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| `HardInterrupt` not raised in `run_with_spec` | Escalation path doesn't exist yet (Step 7) | Step 7 adds `from ballast.core.guardrails import HardInterrupt` + raise in the VIOLATED_IRREVERSIBLE block |
| `build_correction` format is static | Sufficient for Step 6 demo | Step 10+ can add constraint list, context window summary |

---

## Agent Failure Protocol

1. A verification command fails → read the full error output.
2. Cause is unambiguous → make ONE targeted fix → re-run the same verification command.
3. If still failing after one fix → **STOP**. Output full current contents of every modified file. Report: (a) command run, (b) full error verbatim, (c) fix attempted, (d) current state of each modified file, (e) why you cannot proceed.
4. Never attempt a second fix without human instruction.
5. Never modify files not named in the current step.

---

## Steps Analysis

```
Step 1 (Create guardrails.py)          — Non-critical — verification only   — Idempotent: Yes
Step 2 (Create test_guardrails.py)     — Non-critical — verification only   — Idempotent: Yes
Step 3 (Wire into trajectory.py)       — Critical     — full code review     — Idempotent: Yes
```

---

## Pre-Flight — Run Before Any Code Changes

```bash
cd /Users/ngchenmeng/Ballast && source venv/bin/activate

# (1) Confirm both todos are applied
grep -c 'NodeAssessment' ballast/core/trajectory.py      # expected: > 0
grep -c 'check_and_record' ballast/core/cost.py           # expected: > 0
grep -c 'seed_prior_spend' ballast/core/cost.py           # expected: > 0

# (2) Confirm guardrails.py does NOT exist yet
ls ballast/core/guardrails.py 2>&1                         # expected: No such file or directory

# (3) Confirm trajectory.py still has the inline TODO-Step-6 correction block
grep -n 'TODO Step 6' ballast/core/trajectory.py           # expected: exactly 1 match

# (4) Confirm trajectory.py still has the inline resume condition
grep -n 'progress.spec_hash == spec.version_hash' ballast/core/trajectory.py  # expected: 1 match

# (5) Baseline test count
python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3

# (6) Line counts
wc -l ballast/core/trajectory.py ballast/core/cost.py
```

**Baseline Snapshot (agent fills during pre-flight — do not pre-fill):**
```
NodeAssessment in trajectory.py:           ____  (expected: > 0)
check_and_record in cost.py:               ____  (expected: > 0)
seed_prior_spend in cost.py:               ____  (expected: > 0)
guardrails.py exists:                      ____  (expected: No such file)
TODO Step 6 line:                          ____  (expected: 1 match)
resume condition line:                     ____  (expected: 1 match)
Test count before plan:                    ____  (expected: ≥ 147)
trajectory.py line count:                  ____
cost.py line count:                        ____
```

**Automated checks (all must pass before Step 1):**
- [ ] `grep -c 'NodeAssessment' ballast/core/trajectory.py` returns > 0
- [ ] `grep -c 'check_and_record' ballast/core/cost.py` returns > 0
- [ ] `ls ballast/core/guardrails.py` returns "No such file"
- [ ] `grep -c 'TODO Step 6' ballast/core/trajectory.py` returns `1`
- [ ] Existing tests pass: record count

---

## Tasks

### Phase 1 — Add new module (no existing code touched)

**Goal:** `guardrails.py` and `test_guardrails.py` exist and are importable. No existing file is modified.

---

- [ ] 🟥 **Step 1: Create `ballast/core/guardrails.py`** — *Non-critical: pure new file*

  **Step Architecture Thinking:**

  **Pattern applied:** Façade (`build_correction`), Typed Exception (`HardInterrupt`), Predicate Extraction (`can_resume`).

  **Why this step is first:** Steps 2 and 3 both depend on this file existing. The file is a pure addition — no existing file is touched.

  **Why this file is the right location:** `ballast/core/` is the kernel module. `guardrails.py` belongs alongside `trajectory.py`, `cost.py`, and `checkpoint.py` because it is part of the node-boundary response layer — not an adapter or an optional feature.

  **Alternative approach considered and rejected:** Inlining `build_correction` as a private function inside `trajectory.py`. Rejected: it keeps format logic inside the orchestration loop, making future format changes require touching the loop. The overview names `guardrails.py` as a separate file explicitly.

  **What breaks if this step deviates:** If `HardInterrupt` does not carry `.assessment`, `.spec`, `.node_index`, Step 7 cannot build the escalation context from the exception alone. If `build_correction` does not start with `[BALLAST CORRECTION]`, existing trajectory tests that assert on the correction prefix would fail.

  ---

  **Idempotent:** Yes — new file only.

  **Pre-Read Gate:**
  - `ls ballast/core/guardrails.py` must return "No such file". If it exists → STOP.
  - `grep -n 'NodeAssessment' ballast/core/trajectory.py` — confirm at least 1 match (todo-1 applied). If 0 → STOP with "todo-1 not yet applied".

  **Self-Contained Rule:** All code below is complete and runnable. No references to other steps.

  **No-Placeholder Rule:** No `<VALUE>` tokens appear below.

  ```python
  """ballast/core/guardrails.py — Soft injection, hard interrupt, resume gating.

  Public interface:
      build_correction(assessment, spec, node_index) -> str
          Builds the soft correction string injected between nodes when
          assessment.score < spec.drift_threshold. Replaces the inline
          TODO-Step-6 block in trajectory.py:run_with_spec.

      HardInterrupt(Exception)
          Raised when a VIOLATED_IRREVERSIBLE node has no escalation path.
          Carries assessment and spec so the caller can checkpoint and surface
          the interruption. Not yet wired into run_with_spec — Step 7 does that
          once escalation.py is available.

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
      escalation path is available (escalation.py not yet wired, Step 7).

      Carries the NodeAssessment and active SpecModel. Callers should:
        1. Ensure BallastProgress is written at last_clean_node_index.
        2. Log the full context before re-raising.
        3. Surface to the operator for manual resolution.

      After Step 7 (escalation.py), run_with_spec will catch this,
      call escalate(), inject the resolution, and resume — not re-raise.
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
  ```

  **What it does:** Defines three public symbols. `build_correction` formats a multi-line correction message. `HardInterrupt` is a typed exception carrying assessment + spec + node index. `can_resume` is a pure boolean predicate over an existing checkpoint.

  **Why this approach:** `TYPE_CHECKING` guard on `NodeAssessment` import breaks the circular dependency at runtime while preserving type-checker annotations. `build_correction` is a plain function (not a class) because it has no state — format logic only.

  **Assumptions:**
  - `ballast/core/checkpoint.py` exports `BallastProgress` — confirmed by existing tests.
  - `ballast/core/spec.py` exports `SpecModel` — confirmed by existing tests.
  - `NodeAssessment` exists in `ballast/core/trajectory.py` — confirmed by todo-1 pre-condition.

  **Risks:**
  - Circular import at runtime if `TYPE_CHECKING` guard is removed → mitigation: guard is on the `if TYPE_CHECKING:` block; runtime never executes that import.
  - `build_correction` format change breaks existing trajectory tests that assert on correction string content → mitigation: `[BALLAST CORRECTION]` prefix is preserved; existing tests check for this prefix.

  **Git Checkpoint:**
  ```bash
  git add ballast/core/guardrails.py
  git commit -m "step 1: add guardrails.py — build_correction, HardInterrupt, can_resume"
  ```

  **✓ Verification Test:**

  **Type:** Unit (import only)

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate && python -c "
  from ballast.core.guardrails import build_correction, HardInterrupt, can_resume
  print('build_correction:', callable(build_correction))
  print('HardInterrupt:', issubclass(HardInterrupt, Exception))
  print('can_resume:', callable(can_resume))
  import inspect
  sig = inspect.signature(build_correction)
  assert list(sig.parameters) == ['assessment', 'spec', 'node_index'], sig
  print('signatures OK')
  " && python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  **Pass:** Prints `build_correction: True`, `HardInterrupt: True`, `can_resume: True`, `signatures OK`. Test count equals pre-flight baseline (unchanged — no new tests yet).

  **Fail:**
  - `ModuleNotFoundError: No module named 'ballast.core.guardrails'` → file not written to correct path → confirm `ls ballast/core/guardrails.py`.
  - `ImportError: cannot import name 'build_correction'` → function missing or misspelled → check file content.
  - `AssertionError: sig` → wrong parameter names → check function signature.
  - Test count drops → existing file accidentally modified → `git diff`.

---

- [ ] 🟥 **Step 2: Create `tests/test_guardrails.py`** — *Non-critical: pure new test file*

  **Step Architecture Thinking:**

  **Pattern applied:** Unit tests per public symbol. Each of the three public symbols gets its own test group. Tests verify observable output (string content, exception attributes, boolean value) — not internal implementation.

  **Why this step exists here in the sequence:** `guardrails.py` must exist (Step 1). This step verifies all three symbols independently before wiring them into `trajectory.py`. If any symbol is wrong, this step catches it before the wiring step makes the error harder to isolate.

  **Why this file is the right location:** All ballast tests live in `tests/`. Naming convention matches: `test_guardrails.py` tests `guardrails.py`.

  **Alternative approach considered and rejected:** Adding guardrails tests directly to `test_trajectory.py`. Rejected: mixing concerns — `test_trajectory.py` tests the orchestration loop, not the correction-string format.

  **What breaks if this step deviates:** If `NodeAssessment` is imported at the top of `test_guardrails.py` without a `try/except` and todo-1 is not applied, the import fails. The pre-condition check prevents this (Step 1 pre-read gate confirmed todo-1 applied).

  ---

  **Idempotent:** Yes — new file only.

  **Pre-Read Gate:**
  - `ls tests/test_guardrails.py` must return "No such file". If it exists → STOP.
  - `python -c "from ballast.core.guardrails import build_correction, HardInterrupt, can_resume"` must succeed. If not → Step 1 not complete → STOP.

  **Self-Contained Rule:** All code below is complete and runnable.

  **No-Placeholder Rule:** No `<VALUE>` tokens appear below.

  ```python
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
  ```

  **What it does:** 23 unit tests across the three public symbols. No I/O, no mocking, no network calls. All assertions are on observable output values.

  **Why this approach:** Each test class covers one public symbol. Tests use shared `_make_*` helpers to avoid repetition while keeping each test self-contained.

  **Assumptions:**
  - `NodeAssessment` is importable from `ballast.core.trajectory` (todo-1 applied).
  - `lock(SpecModel(**fields))` is the correct API — module-level `lock()` from `ballast.core.spec`, NOT `SpecModel.lock()`. Confirmed from `spec.py` line 492 and existing test pattern at `test_trajectory.py:31`.
  - `BallastProgress` accepts `is_complete` as a kwarg (defaults to `False`).

  **Risks:**
  - `BallastProgress(**raw)` fails if a required field is missing in `_make_progress` → mitigation: pre-flight confirmed BallastProgress shape via existing tests.
  - `lock(SpecModel(**base))` rejects an unknown field in `base` → mitigation: all fields in `_make_spec` defaults (`intent`, `success_criteria`, `constraints`, `irreversible_actions`, `drift_threshold`, `allowed_tools`) are confirmed SpecModel fields from `spec.py:119–172`.

  **Git Checkpoint:**
  ```bash
  git add tests/test_guardrails.py
  git commit -m "step 2: add test_guardrails.py — 23 unit tests for build_correction, HardInterrupt, can_resume"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate && \
  python -m pytest tests/test_guardrails.py -v 2>&1 | tail -30
  ```

  **Expected:**
  - 23 tests collected.
  - 0 failures, 0 errors.

  **Pass:** `23 passed` in the final line.

  **Fail:**
  - `ImportError: cannot import name 'NodeAssessment'` → todo-1 not applied → STOP (pre-condition not met).
  - `AttributeError: 'BallastProgress' object has no attribute 'is_complete'` → BallastProgress field missing → check checkpoint.py.
  - `AssertionError` in `test_intent_truncated_to_200_chars` → `build_correction` not truncating → check `spec.intent[:200]` slice in `guardrails.py`.

---

### Phase 2 — Wire into trajectory.py

**Goal:** `run_with_spec` delegates correction-string construction to `build_correction()` and the resume condition to `can_resume()`. The inline TODO-Step-6 block is gone. All pre-plan tests still pass.

---

- [ ] 🟥 **Step 3: Wire `build_correction` and `can_resume` into `trajectory.py`** — *Critical: modifies run_with_spec*

  **Step Architecture Thinking:**

  **Pattern applied:** Façade (delegation). `run_with_spec` stops being the owner of correction-string logic and resume-decision logic — it delegates both to `guardrails.py`. The orchestration loop becomes a pure coordinator.

  **Why this step exists here in the sequence:** `guardrails.py` must exist (Step 1) and be tested (Step 2) before wiring. Wiring is the last step so the source of truth for both functions is proven correct before being connected.

  **Why this file is the right location:** `trajectory.py` is the only caller of both `build_correction` and `can_resume`. The edits are minimal: one import line, one `if` condition replacement, one inline block replacement.

  **Alternative approach considered and rejected:** Patching `run_with_spec` to call `guardrails.build_correction` via the module reference without an explicit import. Rejected: explicit import is the project convention and makes the dependency visible to static type checkers.

  **What breaks if this step deviates:** If the `elif assessment.score < ...` block is replaced but the surrounding `agent_run.ctx.state.message_history.append(...)` call is also removed (scope creep), the correction is built but never injected — a silent regression. The replacement must only remove the `# TODO Step 6` comment and the inline `correction = (...)` lines.

  ---

  **Idempotent:** Yes — replacements of unique strings. Grepping for them before each edit confirms uniqueness.

  **Context:** After this step: (1) the inline `correction = (...)` block and its `# TODO Step 6` comment are gone; (2) the three-part `if (progress and ...)` condition is `if can_resume(progress, spec):`; (3) all existing trajectory tests still pass because behaviour is unchanged.

  **Pre-Read Gate:**

  Before any edit, run ALL of these. Each must return exactly the match count shown:
  ```bash
  # Edit A anchor — must appear exactly once
  grep -c 'from ballast.core.cost import RunCostGuard' ballast/core/trajectory.py
  # expected: 1

  # Confirm guardrails not yet imported
  grep -c 'from ballast.core.guardrails import' ballast/core/trajectory.py
  # expected: 0

  # Edit B anchor — must appear exactly once
  grep -c 'progress.spec_hash == spec.version_hash' ballast/core/trajectory.py
  # expected: 1

  # Edit C anchor — must appear exactly once (after todo-1 applies assessment.score)
  grep -c 'TODO Step 6: replace with build_correction' ballast/core/trajectory.py
  # expected: 1

  # Confirm assessment.score is used (not score — todo-1 applied)
  grep -c 'assessment.score < active_spec.drift_threshold' ballast/core/trajectory.py
  # expected: 1
  ```

  If any check returns 0 or 2+ → STOP and report which check failed.

  ---

  **Edit A — Add `build_correction, can_resume` import (alphabetical slot: after `cost`, before `spec`).**

  Replace:
  ```python
  from ballast.core.cost import RunCostGuard
  from ballast.core.spec import SpecModel, is_locked
  ```
  With:
  ```python
  from ballast.core.cost import RunCostGuard
  from ballast.core.guardrails import build_correction, can_resume
  from ballast.core.spec import SpecModel, is_locked
  ```

  **Edit B — Replace inline resume condition with `can_resume(progress, spec)`.**

  Replace:
  ```python
      if (
          progress
          and progress.spec_hash == spec.version_hash
          and not progress.is_complete
      ):
  ```
  With:
  ```python
      if can_resume(progress, spec):
  ```

  **Edit C — Replace inline correction block with `build_correction(assessment, active_spec, node_index)`.**

  Replace:
  ```python
              elif assessment.score < active_spec.drift_threshold:
                  # TODO Step 6: replace with build_correction() from ballast.core.guardrails
                  correction = (
                      f"[BALLAST CORRECTION] Drift at node {node_index} "
                      f"(score={assessment.score:.2f}, label={assessment.label}). "
                      f"Rationale: {assessment.rationale}. "
                      f"Re-align with intent: {active_spec.intent[:200]}"
                  )
                  agent_run.ctx.state.message_history.append(
                      ModelRequest(parts=[UserPromptPart(content=correction)])
                  )
  ```
  With:
  ```python
              elif assessment.score < active_spec.drift_threshold:
                  correction = build_correction(assessment, active_spec, node_index)
                  agent_run.ctx.state.message_history.append(
                      ModelRequest(parts=[UserPromptPart(content=correction)])
                  )
  ```

  **What it does:** Three edits total. One adds the import. One collapses a three-line boolean into one function call. One removes seven lines of inline string building and replaces them with one function call. All remaining code in `run_with_spec` — logging, counters, checkpoint — is unchanged.

  **Why this approach:** Minimal diff. Only the lines that belong in `guardrails.py` are removed from `trajectory.py`. The `agent_run.ctx.state.message_history.append(...)` call stays in place — it is orchestration plumbing, not correction-format logic.

  **Assumptions:**
  - After todo-1, `assessment = score_drift(...)` is the call-site (not `score, label, rationale = ...`).
  - After todo-1, the inline block uses `assessment.score`, `assessment.label`, `assessment.rationale` (not bare `score`, `label`, `rationale`).
  - The `if (progress and progress.spec_hash == spec.version_hash and not progress.is_complete):` block is on four lines (the form produced by todo-2's seed_prior_spend edit).

  **Risks:**
  - Edit C old_string doesn't match if todo-1 applied a slightly different format → mitigation: pre-read gate confirms `assessment.score < active_spec.drift_threshold` exists; grep for `TODO Step 6` confirms the comment is present.
  - Edit B collapses the wrong `if` block if `progress.spec_hash == spec.version_hash` appears twice → mitigation: pre-read gate confirms exactly 1 occurrence.

  **Git Checkpoint:**
  ```bash
  git add ballast/core/trajectory.py
  git commit -m "step 3: wire build_correction + can_resume into trajectory.py; remove TODO-Step-6 inline block"
  ```

  **Post-edit confirmation (run before verification test):**
  ```bash
  # Confirm TODO Step 6 is gone
  grep -c 'TODO Step 6' ballast/core/trajectory.py
  # expected: 0

  # Confirm build_correction is called
  grep -c 'build_correction(assessment' ballast/core/trajectory.py
  # expected: 1

  # Confirm can_resume is called
  grep -c 'if can_resume(progress, spec)' ballast/core/trajectory.py
  # expected: 1

  # Confirm inline correction multiline is gone
  grep -c 'BALLAST CORRECTION.*Drift at node' ballast/core/trajectory.py
  # expected: 0
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate && \
  python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -5
  ```

  **Expected:**
  - Test count ≥ pre-flight baseline + 23 (the new guardrails tests).
  - 0 failures, 0 errors.
  - No `ImportError` or `AttributeError`.

  **Pass:** All tests pass. Test count = pre-flight baseline + 23.

  **Fail:**
  - `ImportError: cannot import name 'build_correction' from 'ballast.core.guardrails'` → Edit A applied before Step 1 was complete → confirm `ls ballast/core/guardrails.py` and `python -c "from ballast.core.guardrails import build_correction"`.
  - `TypeError: can_resume() takes 2 positional arguments` → Edit B produced wrong call signature → check `guardrails.py` function signature.
  - Test count drops vs baseline → an existing test was accidentally deleted → `git diff tests/test_trajectory.py` and check for accidental removals.
  - `AttributeError: 'tuple' object has no attribute 'score'` in trajectory tests → todo-1 not applied (score_drift still returns tuple) → STOP; pre-condition not met.

---

## Regression Guard

**Systems at risk from this plan:**
- `run_with_spec` resume path — `can_resume` replaces the inline condition; if the logic differs, resume tests fail.
- `run_with_spec` drift response path — `build_correction` replaces the inline string; if the prefix differs, any test asserting `[BALLAST CORRECTION]` in the injected message would fail.

**Regression verification:**

| System | Pre-change behaviour | Post-change verification |
|--------|---------------------|--------------------------|
| `run_with_spec` resume | `if (progress and progress.spec_hash == ... and not progress.is_complete)` | `can_resume` returns identical True/False; all resume tests pass |
| `run_with_spec` drift inject | Inline `[BALLAST CORRECTION] Drift at node N ...` | `build_correction` starts with `[BALLAST CORRECTION]`; all drift tests pass |
| All other `run_with_spec` paths | Unchanged | Full test suite passes at count ≥ baseline + 23 |

**Test count regression check:**
- Tests before plan (from Pre-Flight baseline): `____`
- Tests after plan: run `python -m pytest tests/ -m 'not integration' -q` — must be `≥ baseline + 23`

---

## Rollback Procedure

```bash
# Rollback Step 3 (trajectory.py wiring)
git revert HEAD     # reverts step 3 commit

# Rollback Step 2 (test file)
git revert HEAD     # reverts step 2 commit

# Rollback Step 1 (guardrails.py)
git revert HEAD     # reverts step 1 commit

# Confirm system is back to pre-plan state:
python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
grep -c 'TODO Step 6' ballast/core/trajectory.py   # must return 1
ls ballast/core/guardrails.py 2>&1                  # must return "No such file"
```

---

## Success Criteria

| Feature | Target | Verification |
|---------|--------|--------------|
| `guardrails.py` importable | All three symbols importable without error | `python -c "from ballast.core.guardrails import build_correction, HardInterrupt, can_resume"` |
| `build_correction` format | Starts with `[BALLAST CORRECTION]`, ends with continue directive | `test_guardrails.py::TestBuildCorrection` — 11 tests pass |
| `HardInterrupt` shape | Exception subclass; carries `.assessment`, `.spec`, `.node_index` | `test_guardrails.py::TestHardInterrupt` — 7 tests pass |
| `can_resume` predicate | Returns True/False per resume rules | `test_guardrails.py::TestCanResume` — 5 tests pass |
| `TODO Step 6` removed | Inline correction block gone from trajectory.py | `grep -c 'TODO Step 6' ballast/core/trajectory.py` returns `0` |
| Resume condition refactored | `can_resume(progress, spec)` call present | `grep -c 'if can_resume(progress, spec)' ballast/core/trajectory.py` returns `1` |
| All regression tests pass | Count ≥ pre-flight baseline + 23 | `python -m pytest tests/ -m 'not integration' -q` |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Do not proceed past a Human Gate without explicit human input.**
⚠️ **If blocked, mark 🟨 In Progress and output the State Manifest before stopping.**
⚠️ **Do not batch multiple steps into one git commit.**
⚠️ **Edit C old_string must match the post-todo-1 form (`assessment.score`, `assessment.label`, `assessment.rationale`) — NOT the pre-todo-1 form (`score`, `label`, `rationale`). If they don't match, STOP.**
⚠️ **`HardInterrupt` is NOT raised in `run_with_spec` at this step. Do not add a `raise HardInterrupt(...)` call to trajectory.py — that is Step 7.**
