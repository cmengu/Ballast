# Feature Implementation Plan: NodeAssessment dataclass + DRY cleanup in trajectory.py

**Overall Progress:** `0% (0/3 steps done)`

---

## TLDR

`score_drift()` returns a raw `tuple[float, str, str]` — positional, fragile, carries no `tool_name`. The three scorer calls (`score_tool_compliance`, `score_constraint_violation`, `score_intent_alignment`) plus `min()` are duplicated verbatim in both `score_drift()` and `TrajectoryChecker.check()`. `run_with_spec()` calls `_extract_node_info(node)` a second time just to get `tool_name` after `score_drift()` already extracted it. This plan: (1) adds a `NodeAssessment` dataclass and `_run_scorers()` private helper, (2) makes `score_drift()` return `NodeAssessment` and eliminates the second `_extract_node_info` call from `run_with_spec()`, (3) makes `TrajectoryChecker.check()` delegate to `_run_scorers()`. No behaviour changes. All 141 tests must pass after each step.

---

## Architecture Overview

**The problem this plan solves:**

`ballast/core/trajectory.py` has three concrete DRY violations:

1. `score_drift()` line 425 and `TrajectoryChecker.check()` line 540–544 both call all three scorers in the same order and compute `aggregate = min(...)`. Any change to scorer priority must be made in two places.
2. `score_drift()` returns `tuple[float, str, str]` — callers unpack by position. Adding a field (e.g. `reversible`, `scorer_error`) requires touching every callsite.
3. `run_with_spec()` line 705 calls `_extract_node_info(node)` a second time after `score_drift()` already called it internally, solely to retrieve `tool_name`.

**Patterns applied:**

- **DTO (`NodeAssessment`):** All scoring output travels as a typed object. Callers access `.score`, `.label`, `.tool_name` by name — not position. Adding a field later requires no callsite changes.
- **DRY / Extract Helper (`_run_scorers`):** The three scorer calls are a single implementation detail. Centralising them means the scoring contract (order, min logic) lives in one place. Both `score_drift` and `TrajectoryChecker.check` are consumers, not owners, of that logic.

**What stays unchanged:**

- `DriftResult` (Pydantic BaseModel) — public API carried by `DriftDetected`. Not touched.
- `DriftDetected` exception — not touched.
- `checkpoint.py`, `spec.py`, `sync.py`, `cost.py` — not touched.
- `TrajectoryChecker.check()` public behaviour — same inputs, same outputs, same `DriftDetected` raise condition. Only the internal implementation changes.
- `score_drift()` public behaviour — same inputs, same semantics. Return type changes from tuple to `NodeAssessment`.

**What this plan adds:**

- `NodeAssessment` dataclass (7 fields): `score`, `label`, `rationale`, `tool_score`, `constraint_score`, `intent_score`, `tool_name`.
- `_run_scorers(node, spec) -> tuple[float, float, float]` private helper.

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|---|---|---|
| `NodeAssessment` is a `@dataclass` in trajectory.py | Pydantic `BaseModel` like `DriftResult` | `DriftResult` is the public API surface carried by exceptions. `NodeAssessment` is internal loop state — no serialisation, no validation needed. `dataclass` is zero-dependency and faster to construct. |
| `_run_scorers` returns all three scores as a tuple | Return a sub-dataclass | Callers immediately unpack into named locals. A sub-dataclass adds no clarity at that use-site. |
| `score_drift` gate still calls `score_tool_compliance` directly before `_run_scorers` | Call `_run_scorers` first (all three) then gate | Gate purpose is to skip LLM calls when rule-based checks fail. Calling `_run_scorers` first would invoke two LLM APIs then throw the result away. Preserving gate-first order is load-bearing. |
| Steps 2 and 5 (score_drift change + test assertion fix) done in same step | Fix trajectory.py first, tests in separate step | After `score_drift` returns `NodeAssessment`, tests that do `s, lbl, _ = score_drift(...)` break immediately. Keeping them in the same step means verification always passes. |

**Known limitations:**

| Limitation | Why acceptable now | Upgrade path |
|---|---|---|
| `NodeAssessment` does not include `reversible` or `scorer_error` fields | Previously discussed as improvement; out of scope for this DRY refactor | Add fields to `NodeAssessment` in a later step; callers already access by name |

---

## Agent Failure Protocol

1. A verification command fails → read the full error output.
2. Cause is unambiguous → make ONE targeted fix → re-run the same verification command.
3. If still failing after one fix → **STOP**. Output full current contents of every file modified in this step. Report: (a) command run, (b) full error verbatim, (c) fix attempted, (d) current state of each modified file, (e) why you cannot proceed.
4. Never attempt a second fix without human instruction.
5. Never modify files not named in the current step.

---

## Pre-Flight — Run Before Any Code Changes

```bash
cd /Users/ngchenmeng/Ballast
source venv/bin/activate

# (1) Baseline test count
python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3

# (2) Confirm NodeAssessment does NOT exist yet
grep -n 'NodeAssessment\|_run_scorers' ballast/core/trajectory.py

# (3) Confirm score_drift return type is tuple
grep -n 'def score_drift\|-> tuple' ballast/core/trajectory.py

# (4) Confirm duplicate scorer calls in TrajectoryChecker
grep -n 'score_tool_compliance\|score_constraint_violation\|score_intent_alignment' ballast/core/trajectory.py

# (5) Confirm second _extract_node_info call in run_with_spec
grep -n '_extract_node_info' ballast/core/trajectory.py

# (6) Line counts
wc -l ballast/core/trajectory.py tests/test_trajectory.py
```

**Baseline Snapshot (agent fills during pre-flight — do not pre-fill):**
```
Test count before plan:            ____   (expected: 141)
trajectory.py line count:          ____   (expected: 795)
test_trajectory.py line count:     ____   (expected: 527)
NodeAssessment exists:             ____   (expected: 0 matches)
_run_scorers exists:               ____   (expected: 0 matches)
_extract_node_info call count:     ____   (expected: 2)
```

**Automated checks (all must pass before Step 1):**
- [ ] 141 tests pass: `python -m pytest tests/ -m 'not integration' -q`
- [ ] `grep -c 'NodeAssessment' ballast/core/trajectory.py` returns `0`
- [ ] `grep -c '_run_scorers' ballast/core/trajectory.py` returns `0`
- [ ] `grep -c '_extract_node_info' ballast/core/trajectory.py` returns `2`

---

## Steps Analysis

```
Step 1 (Add NodeAssessment + _run_scorers)              — Non-critical — verification only   — Idempotent: Yes
Step 2 (Update score_drift + run_with_spec + tests)     — Critical     — full code review     — Idempotent: Yes
Step 3 (Update TrajectoryChecker.check to _run_scorers) — Critical     — full code review     — Idempotent: Yes
```

---

## Tasks

### Phase 1 — Add new primitives (no existing code touched)

**Goal:** `NodeAssessment` and `_run_scorers` exist and are importable. No existing function is modified.

---

- [ ] 🟥 **Step 1: Add `NodeAssessment` dataclass + `_run_scorers()` helper to trajectory.py** — *Non-critical: pure additions*

  **Step Architecture Thinking:**

  **Pattern applied:** DTO (`NodeAssessment`), Extract Helper (`_run_scorers`).

  **Why this step exists here in the sequence:**
  Steps 2 and 3 both import or call these. They must exist before any function is modified.

  **Why this file is the right location:**
  Both are used only within trajectory.py. `NodeAssessment` is the return type of `score_drift()`; `_run_scorers` wraps the three scorers that live here. No cross-file import needed.

  **Alternative approach considered and rejected:**
  Adding `NodeAssessment` to `checkpoint.py`. Rejected: it's a live scoring result, not a checkpoint artifact. `checkpoint.py` consumers (dashboard, resume) don't need the live score breakdown.

  **What breaks if this step deviates:**
  If `NodeAssessment.tool_name` is omitted, Step 2's `run_with_spec` update cannot drop the second `_extract_node_info` call — the DRY violation remains.

  ---

  **Idempotent:** Yes — new additions only. Pre-flight confirmed neither exists.

  **Pre-Read Gate:**
  - `grep -c 'NodeAssessment' ballast/core/trajectory.py` must return `0`. If not → STOP.
  - `grep -c '_run_scorers' ballast/core/trajectory.py` must return `0`. If not → STOP.
  - `grep -n 'from dataclasses import' ballast/core/trajectory.py` — note whether `dataclass` is already imported. If not, the import block edit below is required.
  - Confirm anchor for `NodeAssessment` insertion: `grep -n 'DriftLabel = Literal' ballast/core/trajectory.py` must return exactly 1 match.
  - Confirm anchor for `_run_scorers` insertion: `grep -n 'def score_drift' ballast/core/trajectory.py` must return exactly 1 match.

  **Edit A — Add `dataclass` to the import block.**

  Replace:
  ```python
  import logging
  import uuid
  from datetime import datetime, timezone
  from typing import Any, Literal, Optional
  ```
  With:
  ```python
  import logging
  import uuid
  from dataclasses import dataclass
  from datetime import datetime, timezone
  from typing import Any, Literal, Optional
  ```

  **Edit B — Insert `NodeAssessment` immediately before `DriftLabel = Literal[...]`.**

  Insert before:
  ```python
  DriftLabel = Literal["PROGRESSING", "STALLED", "VIOLATED", "VIOLATED_IRREVERSIBLE"]
  ```

  The block to insert (goes immediately before that line):
  ```python
  # ---------------------------------------------------------------------------
  # NodeAssessment — typed return value for score_drift()
  # ---------------------------------------------------------------------------

  @dataclass
  class NodeAssessment:
      """Typed result returned by score_drift().

      Replaces the raw tuple[float, str, str] so callers access fields by name.
      tool_name is included so run_with_spec() does not need a second
      _extract_node_info() call after score_drift() returns.
      """
      score: float
      label: str          # DriftLabel: PROGRESSING | STALLED | VIOLATED | VIOLATED_IRREVERSIBLE
      rationale: str
      tool_score: float
      constraint_score: float
      intent_score: float
      tool_name: str      # empty string if node has no tool call


  ```

  **Edit C — Insert `_run_scorers()` immediately before `def score_drift`.**

  Insert before:
  ```python
  def score_drift(
  ```

  The block to insert:
  ```python
  def _run_scorers(node: Any, spec: SpecModel) -> tuple[float, float, float]:
      """Call all three scorers and return (tool_score, constraint_score, intent_score).

      Private helper. Eliminates scorer duplication between score_drift() (LLM path)
      and TrajectoryChecker.check(). Never raises — each scorer has its own fail-safe.
      """
      return (
          score_tool_compliance(node, spec),
          score_constraint_violation(node, spec),
          score_intent_alignment(node, spec),
      )


  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/core/trajectory.py
  git commit -m "step 1: add NodeAssessment dataclass + _run_scorers() helper"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate && python -c "
  from ballast.core.trajectory import NodeAssessment, _run_scorers
  a = NodeAssessment(score=1.0, label='PROGRESSING', rationale='ok',
      tool_score=1.0, constraint_score=1.0, intent_score=1.0, tool_name='')
  assert a.score == 1.0
  assert a.label == 'PROGRESSING'
  assert a.tool_name == ''
  print('NodeAssessment OK')
  # _run_scorers is callable (do not call — it would hit LLM)
  assert callable(_run_scorers)
  print('_run_scorers OK')
  " && python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  **Pass:** Prints `NodeAssessment OK` and `_run_scorers OK`. Test count equals pre-flight baseline (141).

  **Fail:**
  - `ImportError: cannot import name 'NodeAssessment'` → `@dataclass` decorator or class body missing → check Edit B anchor matched correctly.
  - `ImportError: cannot import name 'dataclass'` → Edit A not applied → check import block.
  - Test count drops → existing code accidentally modified → `git diff ballast/core/trajectory.py` and confirm only additions.

---

### Phase 2 — Update callers + tests

**Goal:** `score_drift()` returns `NodeAssessment`. `run_with_spec()` uses `assessment.X` fields. `TrajectoryChecker.check()` delegates to `_run_scorers()`. All 141 tests pass.

---

- [ ] 🟥 **Step 2: Update `score_drift()` return type + `run_with_spec()` + score_drift test assertions** — *Critical: changes public return type of score_drift and all its callsites*

  **Step Architecture Thinking:**

  **Pattern applied:** DTO. `score_drift` becomes the single source of both the score and the `tool_name` — `run_with_spec` no longer re-derives what `score_drift` already computed.

  **Why this step exists here in the sequence:**
  Step 1 added `NodeAssessment`. This step makes `score_drift` use it. `run_with_spec` and the score_drift tests must be updated in the same step because as soon as `score_drift` returns `NodeAssessment`, `s, lbl, _ = score_drift(...)` unpacking breaks and the score_drift tests fail.

  **Why this file is the right location:**
  All three changes are in trajectory.py and test_trajectory.py — the only two files that touch `score_drift`.

  **Alternative approach considered and rejected:**
  Making `NodeAssessment` iterable (`__iter__`) so old tuple-unpacking still works. Rejected: it silently preserves the old fragile pattern — the point is to force named access.

  **What breaks if this step deviates:**
  If `assessment.tool_name` is not used to replace `tool_info.get("tool_name", "")` in `run_with_spec()`, the second `_extract_node_info` call remains and the DRY violation is only half-fixed.

  ---

  **Idempotent:** Yes — all edits are replacements of unique strings.

  **Pre-Read Gate:**
  - `grep -n 'def score_drift' ballast/core/trajectory.py` — confirm exactly 1 match.
  - `grep -n '-> tuple\[float, str, str\]' ballast/core/trajectory.py` — confirm exactly 1 match (the current return annotation).
  - `grep -n 'score, label, rationale = score_drift' ballast/core/trajectory.py` — confirm exactly 1 match (in `run_with_spec`).
  - `grep -n '_, _, tool_info = _extract_node_info(node)' ballast/core/trajectory.py` — confirm exactly 1 match (inside `run_with_spec`, not inside `score_drift`).
  - `grep -n 's, lbl' tests/test_trajectory.py` — note all lines; these are the assertions to update.

  **Edit A — Update `score_drift` signature and return type annotation.**

  Replace:
  ```python
  def score_drift(
      node: Any,
      full_window: list,
      spec: SpecModel,
  ) -> tuple[float, str, str]:
      """Layer 1 cascade: score a node and return (score, label, rationale).

      Step 1 — Heuristic gate (no LLM, ~0ms):
          irreversibility check (spec.irreversible_actions)
          tool compliance check (spec.allowed_tools)

      Step 2 — LLM scorers (interim Layer 1; replaced by evaluate_node at Step 10):
          score >= 0.85 → PROGRESSING  (skip Layer 2)
          score <= 0.25 → VIOLATED     (skip Layer 2)
          else          → STALLED      (Layer 2 stub — wired at Step 10)

      Returns:
          (aggregate_score, label, rationale)
      """
  ```
  With:
  ```python
  def score_drift(
      node: Any,
      full_window: list,
      spec: SpecModel,
  ) -> NodeAssessment:
      """Layer 1 cascade: score a node and return a NodeAssessment.

      Step 1 — Heuristic gate (no LLM, ~0ms):
          irreversibility check (spec.irreversible_actions)
          tool compliance check (spec.allowed_tools)

      Step 2 — LLM scorers (interim Layer 1; replaced by evaluate_node at Step 10):
          score >= 0.85 → PROGRESSING  (skip Layer 2)
          score <= 0.25 → VIOLATED     (skip Layer 2)
          else          → STALLED      (Layer 2 stub — wired at Step 10)

      Returns:
          NodeAssessment with score, label, rationale, per-scorer breakdown, tool_name.
      """
  ```

  **Edit B — Replace the three early-return tuple literals inside `score_drift` with `NodeAssessment`.**

  Replace:
  ```python
      if tool_name and spec.irreversible_actions and tool_name in spec.irreversible_actions:
          return 0.0, "VIOLATED_IRREVERSIBLE", f"irreversible tool: {tool_name}"

      tool_score = score_tool_compliance(node, spec)
      if tool_score == 0.0:
          return 0.0, "VIOLATED", f"tool not in allowed_tools: {tool_name}"

      if not content and not tool_name:
          return 1.0, "PROGRESSING", "no scoreable content"

      # ── LLM scorers ───────────────────────────────────────────────────────
      # Interim Layer 1: uses existing scorers until evaluator.py is built (Step 10).
      constraint_score = score_constraint_violation(node, spec)
      intent_score = score_intent_alignment(node, spec)
      aggregate = min(tool_score, constraint_score, intent_score)

      # ── Label assignment ──────────────────────────────────────────────────
      if aggregate >= 0.85:
          label = "PROGRESSING"
      elif aggregate <= 0.25:
          label = "VIOLATED"
      else:
          # LAYER_2_STUB: passes as STALLED until evaluator.py is wired at Step 10.
          label = "STALLED"

      rationale = (
          f"intent={intent_score:.2f} constraint={constraint_score:.2f} "
          f"tool={tool_score:.2f}"
      )
      return round(aggregate, 4), label, rationale
  ```
  With:
  ```python
      if tool_name and spec.irreversible_actions and tool_name in spec.irreversible_actions:
          return NodeAssessment(
              score=0.0, label="VIOLATED_IRREVERSIBLE",
              rationale=f"irreversible tool: {tool_name}",
              tool_score=0.0, constraint_score=1.0, intent_score=1.0,
              tool_name=tool_name,
          )

      tool_score = score_tool_compliance(node, spec)
      if tool_score == 0.0:
          return NodeAssessment(
              score=0.0, label="VIOLATED",
              rationale=f"tool not in allowed_tools: {tool_name}",
              tool_score=0.0, constraint_score=1.0, intent_score=1.0,
              tool_name=tool_name,
          )

      if not content and not tool_name:
          return NodeAssessment(
              score=1.0, label="PROGRESSING",
              rationale="no scoreable content",
              tool_score=1.0, constraint_score=1.0, intent_score=1.0,
              tool_name=tool_name,
          )

      # ── LLM scorers ───────────────────────────────────────────────────────
      # Interim Layer 1: uses existing scorers until evaluator.py is built (Step 10).
      constraint_score = score_constraint_violation(node, spec)
      intent_score = score_intent_alignment(node, spec)
      aggregate = min(tool_score, constraint_score, intent_score)

      # ── Label assignment ──────────────────────────────────────────────────
      if aggregate >= 0.85:
          label = "PROGRESSING"
      elif aggregate <= 0.25:
          label = "VIOLATED"
      else:
          # LAYER_2_STUB: passes as STALLED until evaluator.py is wired at Step 10.
          label = "STALLED"

      return NodeAssessment(
          score=round(aggregate, 4),
          label=label,
          rationale=f"intent={intent_score:.2f} constraint={constraint_score:.2f} tool={tool_score:.2f}",
          tool_score=tool_score,
          constraint_score=constraint_score,
          intent_score=intent_score,
          tool_name=tool_name,
      )
  ```

  **Edit C — Update `run_with_spec()`: replace tuple unpack + second `_extract_node_info` call with `assessment.X` field access.**

  Replace:
  ```python
              # ── 2. Cascade drift score ──────────────────────────────────
              score, label, rationale = score_drift(node, full_window, active_spec)

              # ── 3. Environment probe — STUB ─────────────────────────────
              # TODO Step 9: replace with verify_node_claim from ballast.core.probe
              # if label in ("PROGRESSING", "COMPLETE"):
              #     verified, probe_note = await verify_node_claim(node, label, active_spec)
              #     if not verified:
              #         label, score = "VIOLATED", 0.0
              #         rationale = f"probe failed: {probe_note}"
              verified = True

              # ── 4. Drift response ───────────────────────────────────────
              node_cost = getattr(node, "cost_usd", 0.0)
              _, _, tool_info = _extract_node_info(node)

              if label == "VIOLATED_IRREVERSIBLE":
                  # TODO Step 7: replace with escalate() from ballast.core.escalation
                  # resolution = await escalate(node, active_spec, compact_history + full_window)
                  # agent_run.ctx.state.message_history.append(
                  #     ModelRequest(parts=[UserPromptPart(content=resolution)])
                  # )
                  progress.total_violations += 1
                  progress.last_escalation = datetime.now(timezone.utc).isoformat()
                  logger.warning(
                      "irreversible_action_detected node=%d tool=%s spec_version=%s run_id=%s",
                      node_index,
                      tool_info.get("tool_name", ""),
                      active_spec.version_hash,
                      run_id,
                  )

              elif score < active_spec.drift_threshold:
                  # TODO Step 6: replace with build_correction() from ballast.core.guardrails
                  correction = (
                      f"[BALLAST CORRECTION] Drift at node {node_index} "
                      f"(score={score:.2f}, label={label}). "
                      f"Rationale: {rationale}. "
                      f"Re-align with intent: {active_spec.intent[:200]}"
                  )
                  agent_run.ctx.state.message_history.append(
                      ModelRequest(parts=[UserPromptPart(content=correction)])
                  )
                  # TODO Step 13: emit_drift_span(node, active_spec, score, label)
                  logger.warning(
                      "drift_detected node=%d score=%.3f label=%s spec_version=%s run_id=%s",
                      node_index, score, label, active_spec.version_hash, run_id,
                  )
                  progress.total_drift_events += 1
                  if label == "VIOLATED":
                      progress.total_violations += 1

              # ── 5. Context window management ────────────────────────────
              full_window.append(node)
              if len(full_window) > active_spec.harness.context_window_size:
                  evicted = full_window.pop(0)
                  compact_history.append(
                      _compact_node(evicted, score, label, node_cost, verified)
                  )

              # ── 6. Checkpoint ───────────────────────────────────────────
              progress.completed_node_summaries.append(NodeSummary(
                  index=node_index,
                  tool_name=tool_info.get("tool_name", ""),
                  label=label,
                  drift_score=score,
                  cost_usd=node_cost,
                  verified=verified,
                  spec_hash=active_spec.version_hash,   # active hash — NOT dispatch hash
                  timestamp=datetime.now(timezone.utc).isoformat(),
              ))
  ```
  With:
  ```python
              # ── 2. Cascade drift score ──────────────────────────────────
              assessment = score_drift(node, full_window, active_spec)

              # ── 3. Environment probe — STUB ─────────────────────────────
              # TODO Step 9: replace with verify_node_claim from ballast.core.probe
              # if assessment.label in ("PROGRESSING", "COMPLETE"):
              #     verified, probe_note = await verify_node_claim(node, assessment.label, active_spec)
              #     if not verified:
              #         assessment.label, assessment.score = "VIOLATED", 0.0
              #         assessment.rationale = f"probe failed: {probe_note}"
              verified = True

              # ── 4. Drift response ───────────────────────────────────────
              node_cost = getattr(node, "cost_usd", 0.0)

              if assessment.label == "VIOLATED_IRREVERSIBLE":
                  # TODO Step 7: replace with escalate() from ballast.core.escalation
                  # resolution = await escalate(node, active_spec, compact_history + full_window)
                  # agent_run.ctx.state.message_history.append(
                  #     ModelRequest(parts=[UserPromptPart(content=resolution)])
                  # )
                  progress.total_violations += 1
                  progress.last_escalation = datetime.now(timezone.utc).isoformat()
                  logger.warning(
                      "irreversible_action_detected node=%d tool=%s spec_version=%s run_id=%s",
                      node_index,
                      assessment.tool_name,
                      active_spec.version_hash,
                      run_id,
                  )

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
                  # TODO Step 13: emit_drift_span(node, active_spec, score, label)
                  logger.warning(
                      "drift_detected node=%d score=%.3f label=%s spec_version=%s run_id=%s",
                      node_index, assessment.score, assessment.label, active_spec.version_hash, run_id,
                  )
                  progress.total_drift_events += 1
                  if assessment.label == "VIOLATED":
                      progress.total_violations += 1

              # ── 5. Context window management ────────────────────────────
              full_window.append(node)
              if len(full_window) > active_spec.harness.context_window_size:
                  evicted = full_window.pop(0)
                  compact_history.append(
                      _compact_node(evicted, assessment.score, assessment.label, node_cost, verified)
                  )

              # ── 6. Checkpoint ───────────────────────────────────────────
              progress.completed_node_summaries.append(NodeSummary(
                  index=node_index,
                  tool_name=assessment.tool_name,
                  label=assessment.label,
                  drift_score=assessment.score,
                  cost_usd=node_cost,
                  verified=verified,
                  spec_hash=active_spec.version_hash,   # active hash — NOT dispatch hash
                  timestamp=datetime.now(timezone.utc).isoformat(),
              ))
  ```

  **Edit D — Update `tests/test_trajectory.py`: add `NodeAssessment` to the second import block.**

  Replace:
  ```python
  from ballast.core.trajectory import _compact_node, run_with_spec, score_drift
  ```
  With:
  ```python
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
  ```

  **Edit E — Update score_drift test assertions (6 tests): replace tuple-unpack with attribute access.**

  Replace:
  ```python
  def test_score_drift_irreversible_tool_returns_violated_irreversible():
      spec = _make_spec_with_irreversible()
      s, lbl, _ = score_drift(FakeToolNode("send_email"), [], spec)
      assert lbl == "VIOLATED_IRREVERSIBLE"
      assert s == 0.0


  def test_score_drift_forbidden_tool_returns_violated():
      spec = _make_spec_with_irreversible()
      s, lbl, _ = score_drift(FakeToolNode("forbidden"), [], spec)
      assert lbl == "VIOLATED"
      assert s == 0.0


  def test_score_drift_clean_node_returns_progressing():
      spec = _make_spec_with_irreversible()
      with patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0), \
           patch("ballast.core.trajectory.score_intent_alignment", return_value=0.9):
          s, lbl, rationale = score_drift(FakeTextNode("good output"), [], spec)
      assert lbl == "PROGRESSING"
      assert s == 0.9
      assert "intent=" in rationale


  def test_score_drift_borderline_returns_stalled():
      spec = _make_spec_with_irreversible()
      with patch("ballast.core.trajectory.score_constraint_violation", return_value=0.6), \
           patch("ballast.core.trajectory.score_intent_alignment", return_value=0.6):
          s, lbl, _ = score_drift(FakeTextNode("unclear"), [], spec)
      assert lbl == "STALLED"
      assert 0.25 < s < 0.85


  def test_score_drift_low_score_returns_violated():
      spec = _make_spec_with_irreversible()
      with patch("ballast.core.trajectory.score_constraint_violation", return_value=0.1), \
           patch("ballast.core.trajectory.score_intent_alignment", return_value=0.9):
          s, lbl, _ = score_drift(FakeTextNode("bad action"), [], spec)
      assert lbl == "VIOLATED"
      assert s <= 0.25


  def test_score_drift_empty_node_returns_progressing():
      spec = _make_spec_with_irreversible()
      s, lbl, _ = score_drift(FakeEmptyNode(), [], spec)
      assert lbl == "PROGRESSING"
      assert s == 1.0
  ```
  With:
  ```python
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
  ```

  **Edit F — Update run_with_spec mock return values (5 occurrences) in test_trajectory.py.**

  Replace all 5 occurrences of:
  ```python
      with patch("ballast.core.trajectory.score_drift", return_value=(1.0, "PROGRESSING", "")):
  ```
  With:
  ```python
      with patch("ballast.core.trajectory.score_drift", return_value=_A_PROGRESSING):
  ```

  Replace:
  ```python
      with patch("ballast.core.trajectory.score_drift", return_value=(0.3, "VIOLATED", "bad")):
  ```
  With:
  ```python
      with patch("ballast.core.trajectory.score_drift", return_value=_A_VIOLATED):
  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/core/trajectory.py tests/test_trajectory.py
  git commit -m "step 2: score_drift returns NodeAssessment; run_with_spec uses assessment fields"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate && python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  **Pass:** Test count equals pre-flight baseline (141). Zero failures.

  **Fail:**
  - `TypeError: cannot unpack non-iterable NodeAssessment` → a tuple-unpack was missed → `grep -n 'score_drift' tests/test_trajectory.py ballast/core/trajectory.py` to find remaining unpacks.
  - `AttributeError: 'tuple' object has no attribute 'score'` → mock return_value still a tuple → check Edit F was applied to all 5 occurrences.
  - Test count drops → an existing assertion was removed rather than updated → `git diff tests/test_trajectory.py`.

---

- [ ] 🟥 **Step 3: Update `TrajectoryChecker.check()` to use `_run_scorers()`** — *Critical: eliminates scorer duplication*

  **Step Architecture Thinking:**

  **Pattern applied:** DRY / Extract Helper. `_run_scorers` now owns the scorer contract. `TrajectoryChecker.check()` delegates to it instead of repeating the three calls.

  **Why this step exists here in the sequence:**
  `_run_scorers` must exist (Step 1). `score_drift` must already use it or not — this step is independent of Step 2 but Step 1 is a hard prerequisite.

  **Why this file is the right location:**
  `TrajectoryChecker.check()` lives in trajectory.py alongside `_run_scorers`. No import change needed.

  **Alternative approach considered and rejected:**
  Making `TrajectoryChecker.check()` call `score_drift()` and unwrap the `NodeAssessment`. Rejected: `score_drift` includes a heuristic gate and `full_window` parameter that `TrajectoryChecker` doesn't model. The `TrajectoryChecker` API has no gate — it scores every node. Calling `score_drift` would silently add gate semantics to `TrajectoryChecker` and change its behaviour.

  **What breaks if this step deviates:**
  If the `aggregate = min(...)` line is removed but not replaced, `TrajectoryChecker.check()` has no aggregate score and `DriftDetected` is never raised — all 17 TrajectoryChecker tests would fail.

  ---

  **Idempotent:** Yes — replacement of a unique block.

  **Pre-Read Gate:**
  - `grep -n 'score_tool_compliance\|score_constraint_violation\|score_intent_alignment' ballast/core/trajectory.py` — confirm the three scorer calls appear inside `TrajectoryChecker.check()` (should show lines ~540–542).
  - `grep -c '_run_scorers' ballast/core/trajectory.py` must return `1` (added in Step 1). If 0 → Step 1 not complete → STOP.
  - Confirm anchor uniqueness: `grep -n 'tool_score = score_tool_compliance(node, self.spec)' ballast/core/trajectory.py` must return exactly 1 match.

  **Edit A — Replace the three scorer calls inside `TrajectoryChecker.check()`.**

  Replace:
  ```python
          tool_score = score_tool_compliance(node, self.spec)
          constraint_score = score_constraint_violation(node, self.spec)
          intent_score = score_intent_alignment(node, self.spec)

          aggregate = min(tool_score, constraint_score, intent_score)
  ```
  With:
  ```python
          tool_score, constraint_score, intent_score = _run_scorers(node, self.spec)
          aggregate = min(tool_score, constraint_score, intent_score)
  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/core/trajectory.py
  git commit -m "step 3: TrajectoryChecker.check() delegates scorer calls to _run_scorers()"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate && python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  Also confirm the duplication is gone:
  ```bash
  grep -c 'score_tool_compliance\|score_constraint_violation\|score_intent_alignment' ballast/core/trajectory.py
  ```

  **Pass:** Test count equals pre-flight baseline (141). The grep count above returns `3` (one definition each, plus 3 calls inside `_run_scorers` — not duplicated elsewhere).

  **Fail:**
  - `DriftDetected` never raised in TrajectoryChecker tests → `aggregate` not computed → confirm `min(...)` line was not accidentally removed.
  - grep count > 6 → scorer calls still duplicated → check Edit A anchor matched correctly.

---

## Regression Guard

| System | Pre-change behaviour | Post-change verification |
|---|---|---|
| `TrajectoryChecker.check()` | Raises `DriftDetected` when aggregate < threshold | All 17 TrajectoryChecker tests pass |
| `score_drift()` | Returns `(score, label, rationale)` tuple | Returns `NodeAssessment`; same values accessible as `.score`, `.label`, `.rationale` |
| `run_with_spec()` | Writes checkpoint, injects correction, counts violations | 6 run_with_spec tests pass unchanged |

**Test count regression check:** Run `python -m pytest tests/ -m 'not integration' -q` after each step. Must remain ≥ 141.

---

## Success Criteria

| Feature | Target | Verification |
|---|---|---|
| `score_drift` return type | `NodeAssessment`, not tuple | `python -c "from ballast.core.trajectory import score_drift, NodeAssessment"` — no error |
| No second `_extract_node_info` in `run_with_spec` | `_extract_node_info` called only once per node | `grep -c '_extract_node_info' ballast/core/trajectory.py` returns `2` (definition + one call inside `score_drift`) |
| Scorer duplication eliminated | Three scorer calls appear only in `_run_scorers` | `grep -c 'score_tool_compliance' ballast/core/trajectory.py` returns `2` (definition + call in `_run_scorers`) |
| All tests pass | Count ≥ 141 | `python -m pytest tests/ -m 'not integration' -q` |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Do not proceed past a Human Gate without explicit human input.**
⚠️ **Steps 2 and 3 are independent after Step 1 — but Step 2 must complete before verifying the second `_extract_node_info` call is gone.**
⚠️ **Do not batch multiple steps into one git commit.**
