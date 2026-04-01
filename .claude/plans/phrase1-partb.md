# Step 3 — `trajectory.py`: Mid-Run Drift Detection

**Overall Progress:** `0%` (0 / 5 steps complete)

---

## TLDR

Replace `ballast/core/trajectory.py` (currently a thin post-run keyword matcher from parta Step 8) with a mid-run drift detector. The new design intercepts every agent action during execution — not after — so a drifted agent is caught at the first bad node, not after the full run cost has been spent.

Three capabilities: (1) `TrajectoryChecker` class initialised with a `LockedSpec`, called at every `on_tool_start` / `on_tool_end` / `on_chain_end` event from `astream_events` — these are the exact node-boundary events that map to `Agent.iter` without requiring a change to the streaming infrastructure; (2) three independent scoring dimensions — `score_tool_compliance` (rule-based: is the tool in `allowed_tools`?), `score_constraint_violation` (LLM: did it breach a hard constraint?), `score_intent_alignment` (LLM: is this action moving toward the goal?); (3) aggregate score as `min(intent, tool, constraint)` — any single failing dimension causes detection. `DriftDetected` exception carries the full `DriftResult` for escalation. `trajectory.py` never decides what to do about drift. `guardrails.py` decides.

After this plan: `TrajectoryChecker.check(event)` is callable from `observe.py`; a tool-compliance violation and an intent drift can both be triggered empirically; the OTel log line is visible per scored event.

---

## Architecture Overview

**The problem this plan solves:**

The existing `trajectory.py` (parta Step 8) checks the final agent output after the run using keyword matching against `success_criteria`. This is wrong in two ways: (1) it only runs after the run, so a drifted agent wastes its full run cost before detection; (2) keyword matching has high false-negative rate on paraphrased output. The correct interception point is `Agent.iter` — every node, mid-execution.

**Why mid-run not post-run:**

If you check only the final result, you have already paid for every bad step. The drift detector must fire at the first node that violates the spec so the caller can abort, retry with a corrected prompt, or escalate to a human — before the next tool call lands.

**`Agent.iter` → `astream_events` mapping:**

LangGraph's `Agent.iter()` (via `graph.stream()`) yields one state update per graph node. The existing `AGUIAdapter` uses `astream_events` instead. The equivalence is exact: filtering `astream_events` for `{on_tool_start, on_tool_end, on_chain_end}` produces one event per node boundary — the same interception points as `graph.stream()`. No change to `AGUIAdapter` is needed.

| `Agent.iter` node | `astream_events` equivalent | What it captures |
|-------------------|-----------------------------|------------------|
| Tool node entered | `on_tool_start` | Tool name + args — compliance check before execution |
| Tool node exited  | `on_tool_end` | Tool output — constraint check after execution |
| Agent node exited | `on_chain_end` | Model decision — intent alignment check |

**The patterns applied:**

| Pattern | Applied to | What breaks if violated |
|---------|-----------|------------------------|
| **Detector / Handler split** | `trajectory.py` detects; `guardrails.py` handles | If trajectory.py decides to retry or abort, it couples detection to policy — cannot change policy without touching the detector |
| **Bottleneck aggregate** | `score = min(intent, tool, constraint)` | If average is used, a 0.0 tool violation (wrong tool) can be averaged away by a 1.0 intent score — a spec-violating tool call passes |
| **Fail-safe per scorer** | Each scorer returns a neutral score on error | If a scorer raises, the check() call fails entirely — drift is never detected on that node |
| **Read-only spec** | `TrajectoryChecker` never mutates `LockedSpec` | If threshold is written back to spec, every checker instance changes the threshold for all downstream callers |
| **Single responsibility** | `score_tool_compliance` is rule-based only; never calls LLM | If tool compliance uses LLM, a network failure causes false-positive drift for a completely valid tool call |

**What changes from the existing `trajectory.py`:**

The existing file (`TrajectoryReport`, `validate_trajectory`, keyword matcher) is fully replaced. Nothing from the parta Step 8 implementation is preserved. The parta test file `tests/test_trajectory.py` (keyword matching tests) is also replaced.

**What stays unchanged:**

- `ballast/core/spec.py` — one new field added: `allowed_tools: list[str] = []` (append-only, backward-compatible default; all existing tests continue to pass)
- `ballast/core/memory.py` — not touched; `update_domain_threshold` is now `guardrails.py`'s responsibility, not trajectory's
- `ballast/adapters/agui.py` — not touched; `check()` is called from `observe.py`'s event loop, not inside the adapter

**What this plan adds:**

| File | Single responsibility |
|------|-----------------------|
| `ballast/core/spec.py` (append) | `allowed_tools: list[str]` field on `LockedSpec` — one line |
| `ballast/core/trajectory.py` (full replace) | `NodeSnapshot`, `DriftResult`, `DriftDetected`, three scorers, `TrajectoryChecker` |
| `tests/test_trajectory.py` (full replace) | Contract tests for all scorers and checker, no live LLM calls except integration test |
| `scripts/observe.py` (append) | `TrajectoryChecker` wired in event loop; catches `DriftDetected` and logs for demo |

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|----------|----------------------|--------------------------|
| `min(intent, tool, constraint)` aggregate | Weighted average | A 0.0 tool score means a forbidden tool was called — averaging this with a high intent score hides a hard spec violation. Min is the correct bottleneck. |
| `score_tool_compliance` rule-based only | LLM semantic check | Tool names are deterministic strings; LLM adds latency and failure modes for a check that needs no interpretation. Empty `allowed_tools` = all allowed (conservative default). |
| `score_constraint_violation` LLM | Regex/keyword on constraints text | Constraints are natural language ("do not modify production files") — regex cannot reliably match paraphrased violations. LLM is the correct evaluator for semantic constraints. |
| `score_intent_alignment` LLM with structured output | Heuristic cosine similarity | Embedding similarity requires a vector DB and doesn't handle multi-step reasoning ("this tool call is preparatory, not the final action"). LLM understands the causal chain. |
| Fail-safe for intent: 0.5 (neutral) | Fail-safe: 0.0 (block) | A network error on the intent scorer should not abort a valid agent run. 0.5 is neutral — it doesn't trigger drift detection alone. |
| Fail-safe for tool: 1.0 (pass) | Fail-safe: 0.5 | Tool compliance is rule-based and never errors. If it somehow fails, "all tools allowed" is safer than blocking. |
| Fail-safe for constraint: 0.5 (neutral) | Fail-safe: 0.0 | Constraint scoring errors (network, API) should not false-positive. 0.5 neutral prevents unnecessary DriftDetected on transient failures. |
| `DriftDetected` is always re-raised | Swallowed inside `check()` | `trajectory.py` must never decide what to do about drift. Swallowing the exception here would prevent `guardrails.py` from ever receiving it. |
| `spec_version` = `sha256(goal + criteria)[:8]` | Monotonic integer counter | A counter is session-local — resets between runs. The hash is stable across restarts and identifies which exact spec was in effect when drift was detected. |

**Known limitations:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| Intent alignment uses single LLM call per scored event | Week 2 goal is correctness; latency impact is observable in `observe.py` | Week 4: cache intent scores by `(spec_version, node_name, content_hash)`; skip re-scoring identical content |
| Constraint violation uses full LLM call even for simple constraints | Correct is more important than fast for Week 2 | Week 3: build a constraint rule registry that covers common constraint patterns (e.g. "do not write files" → check tool_name for write/edit); LLM as fallback only |
| `allowed_tools` is a flat list — no wildcard, no role-based permission | Week 2 agent has one tool (`get_word_count`); complex permissions not yet needed | Week 3: add `allowed_tool_patterns: list[str]` with glob matching |
| `DriftDetected` in `observe.py` is caught and logged (demo mode) | `guardrails.py` does not exist yet | Week 3: `guardrails.py` catches `DriftDetected`, decides to abort / retry / escalate; `observe.py` removes the catch |
| OTel span logging uses Python `logging` | OTel SDK not yet a dependency | Week 3: add `opentelemetry-sdk` and replace `logger.debug` with `span.set_attribute` calls using the same attribute names |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---------|----------|--------|----------|----------|
| Claude model string for scorer calls | Must match `_ANTHROPIC_MODEL` in `memory.py` | `grep _ANTHROPIC_MODEL ballast/core/memory.py` | Steps 2, 3 | ✅ `claude-sonnet-4-6` |
| `ANTHROPIC_API_KEY` available | Must be set for intent + constraint scorer calls | Environment | Steps 2, 3 (live) | ⬜ Confirm before integration test |
| `LockedSpec.allowed_tools` field name | Must be consistent between spec.py and trajectory.py | Decided here | Step 1 | ✅ `allowed_tools: list[str] = []` |
| Existing `trajectory.py` content | Must be fully read before overwrite | `cat ballast/core/trajectory.py` in pre-flight | Step 1 | ⬜ Pre-flight confirms |

---

## Agent Failure Protocol

1. A verification command fails → read the full error output.
2. Cause is unambiguous → make ONE targeted fix → re-run the same verification command.
3. If still failing after one fix → **STOP**. Output full contents of every modified file. Report: (a) command run, (b) full error verbatim, (c) fix attempted, (d) current state of each modified file, (e) why you cannot proceed.
4. Never attempt a second fix without human instruction.
5. Never modify files not named in the current step.

---

## Pre-Flight — Run Before Any Code Changes

```
Run the following — do not change anything. Show full output and wait.

(1) /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ -v --tb=short 2>&1 | tail -5
    Confirm: existing tests pass. Record exact count.
    Expected: ≥ 59 passed (if phrase1-parta was fully executed).
    If count is 24 → phrase1-parta has NOT been executed. STOP: phrase1-parta must be completed first.

(2) cat /Users/ngchenmeng/Ballast/ballast/core/trajectory.py
    Read the FULL file. This is the existing thin validator from parta Step 8 — it will be fully overwritten.
    Record: what classes/functions currently exist.

(3) ls /Users/ngchenmeng/Ballast/tests/test_trajectory.py 2>&1
    This file exists (from parta Step 8). It will be fully replaced in Step 4.
    Read it in full now so you know what tests existed.

(4) grep -n "_ANTHROPIC_MODEL" /Users/ngchenmeng/Ballast/ballast/core/memory.py
    Record: exact model string.

(5) grep -n "allowed_tools" /Users/ngchenmeng/Ballast/ballast/core/spec.py
    Must return nothing — confirms the field does not exist yet.

(6) grep -n "class LockedSpec" /Users/ngchenmeng/Ballast/ballast/core/spec.py
    Record: line number. This is the class we will append a field to in Step 1.

(7) grep -n "lock_spec\|validate_trajectory" /Users/ngchenmeng/Ballast/scripts/observe.py
    Record: current state of observe.py — confirms parta wiring is present.

(8) grep -n "tool.pytest.ini_options" /Users/ngchenmeng/Ballast/pyproject.toml
    Confirm: match found (parta Step 7 registers the integration marker).
    If no match → add before Step 4:
      [tool.pytest.ini_options]
      markers = ["integration: requires ANTHROPIC_API_KEY and live Anthropic API access"]

(9) echo "Pre-flight complete"
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan:               ____  (must be ≥ 59)
trajectory.py current classes:        ____  (TrajectoryReport, validate_trajectory — will be replaced)
test_trajectory.py current tests:     ____  (will be replaced)
_ANTHROPIC_MODEL value:               ____
allowed_tools in spec.py:             ____  (must be absent)
LockedSpec class at line:             ____
observe.py lock_spec present:         ____  (must be Yes)
pytest integration marker:            ____  (must be registered)
```

---

## Steps Analysis

```
Step 1 (allowed_tools → spec.py + full replace of trajectory.py models)  — Critical  — full code review  — Idempotent: Yes (overwrite)
Step 2 (three scoring functions)                                           — Critical  — full code review  — Idempotent: Yes (append)
Step 3 (TrajectoryChecker + check() + OTel log)                           — Critical  — full code review  — Idempotent: Yes (append)
Step 4 (tests/test_trajectory.py — full replace)                          — Critical  — full code review  — Idempotent: Yes (overwrite)
Step 5 (wire observe.py — append TrajectoryChecker in event loop)         — Non-critical — verification  — Idempotent: Yes (append)
```

---

## Tasks

### Phase 1 — Data Contracts

**Goal:** `NodeSnapshot`, `DriftResult`, `DriftDetected` are importable. `LockedSpec` has `allowed_tools`. No logic yet — contracts only.

---

- [ ] 🟥 **Step 1: `allowed_tools` → `LockedSpec` + full replace of `trajectory.py` models** — *Critical: every downstream component imports from both files; field names and exception shape defined here are the stable contract*

  **Step Architecture Thinking:**

  **Pattern applied:** **DTO (Data Transfer Object)** — `DriftResult` is a frozen, validated value object carrying the full context of a single mid-run scoring decision. `NodeSnapshot` is the structured representation of one agent action — it is extracted once from the raw event dict and passed to all three scorers. `DriftDetected` is the exception envelope that carries `DriftResult` to `guardrails.py`.

  **Why `allowed_tools` belongs on `LockedSpec`:**
  Tool compliance is a spec-level constraint — it is determined at lock time, not at scoring time. The agent knows at lock time which tools it is allowed to use. Putting `allowed_tools` on `LockedSpec` means `lock_spec()` can infer it from the goal during the grounding step (Week 3 upgrade), and it is part of the spec audit trail. Putting it on `TrajectoryChecker` would make it a runtime parameter that bypasses the grounding layer entirely.

  **Why `DriftResult` is a Pydantic model, not a dataclass:**
  `DriftResult` will be serialised into OTel span attributes and eventually into memory for run history. Pydantic gives `.model_dump()`, field validation (score ge=0.0 le=1.0), and schema export. A plain dataclass gives none of these.

  **What breaks if `DriftDetected` stores anything other than `DriftResult`:**
  `guardrails.py` will pattern-match on `e.result.failing_dimension` to decide escalation policy. If `DriftDetected` carries a string message instead of the structured result, `guardrails.py` must parse the message — fragile and non-extensible.

  ---

  **Idempotent:** Yes — writing the same file again produces the same result.

  **Pre-Read Gate:**
  - Run `grep -n "allowed_tools" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return nothing. If it exists → STOP.
  - Run `grep -n "model_config" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Record result — confirms `frozen=False` is set on LockedSpec.
  - Read the full current `ballast/core/trajectory.py` before overwriting. Record what is being replaced.

  **Part A — Append `allowed_tools` to `LockedSpec` in `ballast/core/spec.py`:**

  Find the line in `LockedSpec` that reads:
  ```python
      model_config = {"frozen": False}  # RunPhaseTracker mutates intent_signal.step_index
  ```
  Insert BEFORE that line:
  ```python
      allowed_tools: list[str] = Field(
          default_factory=list,
          description=(
              "Tool names the agent is permitted to call. "
              "Empty list = all tools allowed (default). "
              "Populated by lock_spec() from goal inference or user clarification. "
              "Used by trajectory.py score_tool_compliance()."
          )
      )
  ```

  **Part B — Write `/Users/ngchenmeng/Ballast/ballast/core/trajectory.py` (full overwrite):**

  ```python
  """ballast/core/trajectory.py — Mid-run drift detection.

  Public interface:
      TrajectoryChecker        — initialised with a LockedSpec; call .check(event) at every node
      TrajectoryChecker.check  — scores event across three dimensions; raises DriftDetected if below threshold
      DriftResult              — structured per-node scoring result
      DriftDetected            — exception raised when drift score < threshold; carries DriftResult

  Score dimensions:
      tool_compliance     — rule-based: is the tool in spec.allowed_tools? (never LLM)
      constraint_violation — LLM: did this action breach a hard constraint in spec.constraints?
      intent_alignment    — LLM: is this action moving toward spec.success_criteria?

  Aggregate: min(tool, constraint, intent) — any single failing dimension causes detection.
  Threshold: 0.7 default — below this raises DriftDetected.

  Interception point:
      Called on {on_tool_start, on_tool_end, on_chain_end} events from astream_events.
      These are the exact node-boundary events equivalent to Agent.iter() node-by-node output.
      on_tool_start  → tool node entered (compliance check before execution)
      on_tool_end    → tool node exited  (constraint check after execution)
      on_chain_end   → agent node exited (intent alignment check on model decision)

  Key invariant:
      trajectory.py never decides what to do about drift.
      It detects and reports. guardrails.py decides what happens next.
      DriftDetected is NEVER caught inside this module.

  OTel note:
      logger.debug() calls use structured keyword args that map 1:1 to OTel span attributes.
      Week 3 upgrade: replace logger.debug with span.set_attribute() — no logic change required.
  """
  from __future__ import annotations

  import hashlib
  import logging
  from typing import Optional

  from pydantic import BaseModel, Field

  from ballast.core.spec import LockedSpec

  logger = logging.getLogger(__name__)


  # ---------------------------------------------------------------------------
  # Node snapshot — extracted once per event, passed to all three scorers
  # ---------------------------------------------------------------------------

  class NodeSnapshot(BaseModel):
      """Structured representation of a single agent action extracted from a LangGraph event."""
      event_type: str = Field(description="LangGraph event type: on_tool_start | on_tool_end | on_chain_end")
      node_name: str = Field(description="Graph node name from event['name']")
      tool_name: str = Field(
          default="",
          description="Tool invoked — non-empty only for on_tool_start / on_tool_end"
      )
      tool_args: dict = Field(
          default_factory=dict,
          description="Arguments passed to the tool (on_tool_start only)"
      )
      tool_output: str = Field(
          default="",
          description="Tool return value as string (on_tool_end only)"
      )
      model_output: str = Field(
          default="",
          description="Agent message content (on_chain_end or on_chat_model_end)"
      )
      raw_content: str = Field(
          default="",
          description="First 1000 chars of any extractable content — passed to LLM scorers"
      )


  # ---------------------------------------------------------------------------
  # DriftResult — scored assessment of one node against the locked spec
  # ---------------------------------------------------------------------------

  class DriftResult(BaseModel):
      """Complete scoring result for a single agent action.

      Produced by TrajectoryChecker.check() on every scored event.
      Carried by DriftDetected when score < threshold.
      Consumed by guardrails.py for escalation policy decisions.
      """
      score: float = Field(
          ge=0.0, le=1.0,
          description=(
              "Aggregate drift score: min(intent, tool, constraint). "
              "0.0 = complete drift from spec, 1.0 = fully aligned."
          )
      )
      intent_score: float = Field(ge=0.0, le=1.0, description="score_intent_alignment output")
      tool_score: float = Field(ge=0.0, le=1.0, description="score_tool_compliance output")
      constraint_score: float = Field(ge=0.0, le=1.0, description="score_constraint_violation output")
      failing_dimension: str = Field(
          description=(
              "Which dimension caused the lowest score: 'tool' | 'constraint' | 'intent' | 'none'. "
              "Priority when scores are equal: tool > constraint > intent."
          )
      )
      node_snapshot: NodeSnapshot = Field(description="The event that was scored")
      spec_goal: str = Field(description="LockedSpec.goal — identifies which spec was active")
      spec_version: str = Field(
          description=(
              "sha256(spec.goal + spec.success_criteria)[:8] — stable 8-char spec identifier. "
              "Stable across restarts; identifies the exact spec in effect when drift was detected."
          )
      )
      raised_at_step: int = Field(description="Monotonic step counter from TrajectoryChecker (1-indexed)")
      threshold: float = Field(description="The threshold applied at this step")


  # ---------------------------------------------------------------------------
  # DriftDetected exception — carries full context for guardrails.py
  # ---------------------------------------------------------------------------

  class DriftDetected(Exception):
      """Raised by TrajectoryChecker.check() when drift score < threshold.

      Carries the full DriftResult so guardrails.py has complete context to decide:
        - abort the run (re-raise or return early)
        - retry with a corrected prompt (modify goal + re-run)
        - escalate to human (surface the DriftResult)
        - log and continue (demo/observe mode)

      trajectory.py raises this. trajectory.py never catches this.
      guardrails.py (or the caller in observe.py) decides what to do.
      """

      def __init__(self, result: DriftResult) -> None:
          self.result = result
          super().__init__(
              f"Drift at step {result.raised_at_step}: "
              f"score={result.score:.2f} failing={result.failing_dimension!r} "
              f"(intent={result.intent_score:.2f} "
              f"tool={result.tool_score:.2f} "
              f"constraint={result.constraint_score:.2f})"
          )
  ```

  **What it does:** Defines the three data contracts. `NodeSnapshot` extracts one event into a clean struct. `DriftResult` carries the full scoring context per node. `DriftDetected` is the exception boundary between trajectory.py and guardrails.py.

  **Assumptions:**
  - `pydantic>=2.0` installed (confirmed)
  - `ballast.core.spec.LockedSpec` has `allowed_tools` field (added in Part A of this step)

  **Risks:**
  - `allowed_tools` append to spec.py lands in wrong position → existing LockedSpec tests fail → pre-read gate confirms `model_config` line as anchor
  - Import of `LockedSpec` creates circular dependency? No — `spec.py` does not import `trajectory.py`. Dependency is one-way.

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/spec.py ballast/core/trajectory.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.1: add allowed_tools to LockedSpec; replace trajectory.py with mid-run drift detection contracts"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm `allowed_tools` not in spec.py; read existing trajectory.py in full
  - [ ] 🟥 Append `allowed_tools` field to `LockedSpec` in `spec.py`
  - [ ] 🟥 Write `ballast/core/trajectory.py` with exact content above (full overwrite)
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  from ballast.core.spec import LockedSpec, IntentSignal

  # Confirm allowed_tools field exists with correct default
  sig = LockedSpec.model_fields
  assert 'allowed_tools' in sig, 'allowed_tools field missing from LockedSpec'
  assert sig['allowed_tools'].default_factory is not None or sig['allowed_tools'].default == []

  # Confirm existing spec construction still works with no allowed_tools
  spec = LockedSpec(
      goal='test', domain='test',
      success_criteria='done', scope='', constraints=[],
      output_format='', inferred_assumptions=[],
      intent_signal=IntentSignal(
          latent_goal='test', action_type='READ', salient_entity_types=[]
      ),
      clarification_asked=False, threshold_used=0.60,
  )
  assert spec.allowed_tools == [], f'Expected [], got {spec.allowed_tools}'
  print('allowed_tools default OK')

  from ballast.core.trajectory import (
      NodeSnapshot, DriftResult, DriftDetected, LockedSpec
  )
  assert DriftDetected.__bases__ == (Exception,)

  snap = NodeSnapshot(event_type='on_tool_start', node_name='get_word_count',
                      tool_name='get_word_count', tool_args={'text': 'hello'})
  assert snap.model_output == ''

  result = DriftResult(
      score=0.5, intent_score=0.5, tool_score=1.0, constraint_score=0.5,
      failing_dimension='intent',
      node_snapshot=snap,
      spec_goal='test', spec_version='abc12345',
      raised_at_step=1, threshold=0.7,
  )
  exc = DriftDetected(result)
  assert exc.result.score == 0.5
  assert 'step 1' in str(exc)
  print('DriftResult + DriftDetected OK')
  print('Step 1 models OK')
  "
  ```

  **Expected:** `allowed_tools default OK`, `DriftResult + DriftDetected OK`, `Step 1 models OK`

  **Pass:** All three lines printed with exit code 0.

  **Fail:**
  - `allowed_tools field missing` → Part A append didn't land → re-read spec.py and check position
  - `ImportError` on trajectory → file not written → re-read trajectory.py
  - `AssertionError on allowed_tools == []` → default_factory not set → check field definition

---

### Phase 2 — Scoring Functions

**Goal:** Three scoring functions exist and are independently testable. `score_tool_compliance` is rule-based (never LLM). `score_constraint_violation` and `score_intent_alignment` use Claude with structured output and fail-safe returns. `_extract_node_snapshot` converts a raw event dict into a `NodeSnapshot`.

---

- [ ] 🟥 **Step 2: `_extract_node_snapshot` + three scoring functions** — *Critical: all scores flow through these; wrong output here corrupts every DriftResult*

  **Step Architecture Thinking:**

  **Pattern applied:** **Single Responsibility per scorer** — each function scores exactly one dimension and knows nothing about the others. `score_tool_compliance` is pure Python (no I/O). `score_intent_alignment` and `score_constraint_violation` each make one Claude call. None of them know about `DriftResult`, thresholds, or `DriftDetected` — that is `TrajectoryChecker`'s concern.

  **Why `score_tool_compliance` must never call LLM:**
  Tool names are deterministic strings. `spec.allowed_tools = ["get_word_count"]` and `snapshot.tool_name = "get_word_count"` is a string equality check. An LLM would add 200ms latency and a failure mode (network error, quota) to a check that is O(1). If the tool check uses LLM and the LLM quota is exhausted, every tool call passes — silently bypassing the spec constraint.

  **Why constraint scoring uses a separate LLM call from intent scoring:**
  Constraint violations are binary (violated/not violated) and require the full constraint text as context. Intent alignment is a continuous score that uses the `IntentSignal` from the spec. Combining them into one call means a constraint violation could be buried in an otherwise high intent score — the binary constraint violation must be evaluated independently.

  **Why fail-safe for intent is 0.5, not 0.0:**
  Intent scoring fails on transient errors (network, quota, API timeout). 0.0 would trigger `DriftDetected` every time the Anthropic API has a hiccup — false positives during network issues would undermine trust in the detector entirely. 0.5 is neutral — it doesn't trigger detection alone (threshold is 0.7), but it does lower the aggregate score enough that a combined tool or constraint failure will still trigger.

  ---

  **Idempotent:** Yes — appending to trajectory.py; Pre-Read Gate confirms anchor.

  **Pre-Read Gate:**
  - Run `grep -n "def _extract_node_snapshot" /Users/ngchenmeng/Ballast/ballast/core/trajectory.py`. Must return nothing.
  - Run `grep -n "class DriftDetected" /Users/ngchenmeng/Ballast/ballast/core/trajectory.py`. Must return exactly 1 match — confirms Step 1 succeeded.

  Append to `/Users/ngchenmeng/Ballast/ballast/core/trajectory.py`:

  ```python


  # ---------------------------------------------------------------------------
  # Anthropic client (lazy singleton)
  # ---------------------------------------------------------------------------

  import anthropic as _anthropic

  _judge_client: "_anthropic.Anthropic | None" = None
  _JUDGE_MODEL: str = "claude-sonnet-4-6"


  def _get_judge_client() -> "_anthropic.Anthropic":
      global _judge_client
      if _judge_client is None:
          _judge_client = _anthropic.Anthropic()
      return _judge_client


  # ---------------------------------------------------------------------------
  # Node snapshot extractor
  # ---------------------------------------------------------------------------

  def _extract_node_snapshot(event: dict) -> NodeSnapshot:
      """Extract a NodeSnapshot from a raw LangGraph event dict.

      Called once per event in TrajectoryChecker.check().
      Handles on_tool_start, on_tool_end, on_chain_end, on_chat_model_end.
      Returns an empty-content snapshot for unrecognised event types — these
      are skipped by check() before scoring.
      """
      if not isinstance(event, dict):
          return NodeSnapshot(event_type="", node_name="")

      event_type = event.get("event", "")
      node_name = event.get("name", "")
      data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}

      tool_name = ""
      tool_args: dict = {}
      tool_output = ""
      model_output = ""
      raw_content = ""

      if event_type == "on_tool_start":
          tool_name = node_name
          inputs = data.get("input", {})
          if isinstance(inputs, dict):
              tool_args = inputs
          raw_content = str(tool_args)[:1000]

      elif event_type == "on_tool_end":
          tool_name = node_name
          output = data.get("output", "")
          tool_output = str(output)[:1000]
          raw_content = tool_output

      elif event_type in ("on_chain_end", "on_chat_model_end"):
          output = data.get("output", {})
          if isinstance(output, str):
              model_output = output
          elif isinstance(output, dict):
              messages = output.get("messages", [])
              if messages:
                  last = messages[-1]
                  if isinstance(last, dict):
                      model_output = str(last.get("content", ""))
                  elif hasattr(last, "content"):
                      model_output = str(last.content)
              if not model_output:
                  model_output = str(output.get("output", ""))
          elif hasattr(output, "content"):
              content = output.content
              if isinstance(content, list) and content:
                  first = content[0]
                  model_output = getattr(first, "text", str(first))
              else:
                  model_output = str(content)
          raw_content = model_output[:1000]

      return NodeSnapshot(
          event_type=event_type,
          node_name=node_name,
          tool_name=tool_name,
          tool_args=tool_args,
          tool_output=tool_output,
          model_output=model_output,
          raw_content=raw_content,
      )


  # ---------------------------------------------------------------------------
  # Scorer 1 — tool compliance (rule-based, never LLM)
  # ---------------------------------------------------------------------------

  def score_tool_compliance(snapshot: NodeSnapshot, spec: LockedSpec) -> float:
      """Rule-based: is the tool used in spec.allowed_tools?

      Returns:
          1.0 — no tool used (non-tool event); or allowed_tools=[] (all allowed)
          1.0 — tool_name is in allowed_tools
          0.0 — tool_name is NOT in allowed_tools (hard spec violation)

      Never raises. Never calls LLM.
      """
      if not snapshot.tool_name:
          return 1.0  # Non-tool event — compliance check does not apply
      if not spec.allowed_tools:
          return 1.0  # Empty allowed_tools = all tools permitted
      return 1.0 if snapshot.tool_name in spec.allowed_tools else 0.0


  # ---------------------------------------------------------------------------
  # Scorer 2 — constraint violation (LLM, fail-safe 0.5)
  # ---------------------------------------------------------------------------

  _CONSTRAINT_SYSTEM = """You are a constraint enforcement monitor for an AI agent mid-run.
  Determine whether a single agent action violates any of the stated hard constraints.
  Be strict: if an action could plausibly violate a constraint, flag it.
  """

  _CONSTRAINT_TOOL = {
      "name": "constraint_check",
      "description": "Determine if the agent action violates any hard constraint.",
      "input_schema": {
          "type": "object",
          "properties": {
              "violation": {
                  "type": "boolean",
                  "description": "True if any hard constraint is breached"
              },
              "violated_constraint": {
                  "type": "string",
                  "description": "The exact constraint text that was breached, or empty string"
              },
              "rationale": {
                  "type": "string",
                  "description": "One sentence explaining the decision"
              },
          },
          "required": ["violation", "violated_constraint", "rationale"],
      },
  }


  def score_constraint_violation(snapshot: NodeSnapshot, spec: LockedSpec) -> float:
      """LLM-based: does this action breach a hard constraint in spec.constraints?

      Returns:
          1.0 — no constraints stated, or no violation detected
          0.0 — constraint violated (hard spec breach)
          0.5 — scoring error (fail-safe neutral — does not trigger DriftDetected alone)

      Never raises.
      """
      if not spec.constraints:
          return 1.0  # Nothing to violate

      # Build the content to check — prefer tool info over model output for constraint checking
      check_content = (
          f"Tool: {snapshot.tool_name or 'N/A'}\n"
          f"Args: {str(snapshot.tool_args)[:400]}\n"
          f"Output/content: {snapshot.raw_content[:600]}"
      )
      if not check_content.strip("Tool: N/A\nArgs: {}\nOutput/content: "):
          return 1.0  # Empty event — nothing to check

      constraints_text = "\n".join(f"- {c}" for c in spec.constraints)
      prompt = (
          f"Scope: {spec.scope or 'Unconstrained'}\n"
          f"Hard constraints:\n{constraints_text}\n\n"
          f"Agent action ({snapshot.event_type} / node={snapshot.node_name}):\n"
          f"{check_content}"
      )

      try:
          response = _get_judge_client().messages.create(
              model=_JUDGE_MODEL,
              max_tokens=200,
              system=_CONSTRAINT_SYSTEM,
              tools=[_CONSTRAINT_TOOL],
              tool_choice={"type": "tool", "name": "constraint_check"},
              messages=[{"role": "user", "content": prompt}],
          )
          for block in response.content:
              if block.type == "tool_use":
                  raw = block.input
                  if raw.get("violation", False):
                      return 0.0  # Hard constraint breached
                  return 1.0
      except Exception:
          pass
      return 0.5  # Fail-safe: neutral on scoring error


  # ---------------------------------------------------------------------------
  # Scorer 3 — intent alignment (LLM, fail-safe 0.5)
  # ---------------------------------------------------------------------------

  _INTENT_SYSTEM = """You are a mid-run process supervisor for an AI agent.
  Score whether a single agent action is moving toward the stated goal.

  Score 0.0 to 1.0:
    0.0 = actively working against the goal
    0.5 = neutral / tangential / unclear
    0.7 = relevant but indirect progress
    1.0 = directly advancing the goal

  Use the full range — do not cluster near 0.5.
  Be strict: unclear or tangential actions should score below 0.7.
  """

  _INTENT_TOOL = {
      "name": "score_intent",
      "description": "Score intent alignment of a single agent action.",
      "input_schema": {
          "type": "object",
          "properties": {
              "score": {
                  "type": "number",
                  "description": "0.0 to 1.0 — how aligned is this action with the goal?"
              },
              "rationale": {
                  "type": "string",
                  "description": "One sentence explaining the score"
              },
          },
          "required": ["score", "rationale"],
      },
  }


  def score_intent_alignment(snapshot: NodeSnapshot, spec: LockedSpec) -> float:
      """LLM-based: is this action moving toward the goal?

      Returns float in [0.0, 1.0].
      Fail-safe: 0.5 on any error — neutral, does not trigger DriftDetected alone.
      Never raises.
      """
      if not snapshot.raw_content:
          return 0.5  # Nothing to score — neutral

      prompt = (
          f"Goal: {spec.goal}\n"
          f"Success criteria: {spec.success_criteria}\n"
          f"Agent intent at lock time: "
          f"{spec.intent_signal.latent_goal} ({spec.intent_signal.action_type})\n\n"
          f"Agent action ({snapshot.event_type} / node={snapshot.node_name or snapshot.tool_name}):\n"
          f"{snapshot.raw_content[:800]}"
      )

      try:
          response = _get_judge_client().messages.create(
              model=_JUDGE_MODEL,
              max_tokens=200,
              system=_INTENT_SYSTEM,
              tools=[_INTENT_TOOL],
              tool_choice={"type": "tool", "name": "score_intent"},
              messages=[{"role": "user", "content": prompt}],
          )
          for block in response.content:
              if block.type == "tool_use":
                  raw = block.input
                  score = float(raw.get("score", 0.5))
                  return max(0.0, min(1.0, score))
      except Exception:
          pass
      return 0.5  # Fail-safe: neutral on scoring error
  ```

  **What it does:** Appends `_extract_node_snapshot`, the Anthropic singleton, and all three scoring functions. `score_tool_compliance` is pure Python — O(1) string membership check. `score_constraint_violation` and `score_intent_alignment` each make one Claude `tool_use` call with structured output and independent fail-safe returns.

  **Why `_extract_node_snapshot` is a standalone function:**
  It is tested independently from the scorers. The scorer tests can construct `NodeSnapshot` directly without going through the extractor. The extractor is tested against real event shapes. If the two are coupled, a LangGraph event shape change breaks the scorer tests.

  **Assumptions:**
  - `anthropic` installed and `ANTHROPIC_API_KEY` set for LLM scorers
  - Step 1 models (`NodeSnapshot`, `DriftResult`, `DriftDetected`, `LockedSpec`) defined earlier in same file

  **Risks:**
  - `score_constraint_violation` returns 0.0 when no constraints exist? No — the first guard `if not spec.constraints: return 1.0` prevents this.
  - `score_intent_alignment` returns score > 1.0 from Claude? `max(0.0, min(1.0, score))` clamps it.
  - `_extract_node_snapshot` called on `None`? `if not isinstance(event, dict)` returns empty snapshot.

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/trajectory.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.2: add node snapshot extractor and three scoring functions"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm `_extract_node_snapshot` does not exist; `DriftDetected` does
  - [ ] 🟥 Append scoring code to `trajectory.py`
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit (no API call — tests structure and rule-based scorer only)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  import inspect
  from ballast.core.trajectory import (
      _extract_node_snapshot, score_tool_compliance,
      score_intent_alignment, score_constraint_violation,
      NodeSnapshot,
  )
  from ballast.core.spec import LockedSpec, IntentSignal

  # Confirm function signatures
  assert 'snapshot' in inspect.signature(score_tool_compliance).parameters
  assert 'spec'     in inspect.signature(score_tool_compliance).parameters
  print('scorer signatures OK')

  # score_tool_compliance — rule-based, no API
  def make_spec(allowed):
      return LockedSpec(
          goal='test', domain='test', success_criteria='done', scope='',
          constraints=[], output_format='', inferred_assumptions=[],
          allowed_tools=allowed,
          intent_signal=IntentSignal(latent_goal='t', action_type='READ', salient_entity_types=[]),
          clarification_asked=False, threshold_used=0.6,
      )

  snap_tool = NodeSnapshot(event_type='on_tool_start', node_name='bad_tool',
                            tool_name='bad_tool', tool_args={})
  snap_no_tool = NodeSnapshot(event_type='on_chain_end', node_name='agent')

  # Empty allowed_tools → all allowed
  assert score_tool_compliance(snap_tool, make_spec([])) == 1.0
  print('empty allowed_tools → 1.0 OK')

  # Tool in allowed_tools → 1.0
  assert score_tool_compliance(snap_tool, make_spec(['bad_tool'])) == 1.0
  print('tool in allowed_tools → 1.0 OK')

  # Tool NOT in allowed_tools → 0.0
  assert score_tool_compliance(snap_tool, make_spec(['good_tool'])) == 0.0
  print('tool NOT in allowed_tools → 0.0 OK')

  # Non-tool event → 1.0 regardless
  assert score_tool_compliance(snap_no_tool, make_spec(['good_tool'])) == 1.0
  print('non-tool event → 1.0 OK')

  # _extract_node_snapshot on tool_start event
  event = {'event': 'on_tool_start', 'name': 'get_word_count',
           'data': {'input': {'text': 'hello world'}}}
  snap = _extract_node_snapshot(event)
  assert snap.tool_name == 'get_word_count'
  assert snap.tool_args == {'text': 'hello world'}
  assert 'hello' in snap.raw_content
  print('_extract_node_snapshot tool_start OK')

  # _extract_node_snapshot on chain_end event with messages
  chain_event = {
      'event': 'on_chain_end', 'name': 'agent',
      'data': {'output': {'messages': [{'content': 'task complete'}]}}
  }
  snap2 = _extract_node_snapshot(chain_event)
  assert snap2.model_output == 'task complete'
  assert snap2.raw_content == 'task complete'
  print('_extract_node_snapshot chain_end OK')

  # None / bad input → empty snapshot, no raise
  empty = _extract_node_snapshot(None)
  assert empty.event_type == ''
  print('_extract_node_snapshot None → empty OK')

  print('Step 2 scoring structure OK')
  "
  ```

  **Expected:** 8 OK lines followed by `Step 2 scoring structure OK`.

  **Pass:** All assertions pass with exit code 0.

  **Fail:**
  - `ImportError` → append failed → re-read `trajectory.py`
  - `AssertionError: empty allowed_tools → 1.0` → first guard wrong → check `if not spec.allowed_tools: return 1.0`
  - `AssertionError: tool NOT in allowed_tools → 0.0` → membership check inverted → re-read `score_tool_compliance`
  - `AssertionError on tool_name` → `_extract_node_snapshot` reading wrong field → check `tool_name = node_name` for `on_tool_start`

---

### Phase 3 — TrajectoryChecker

**Goal:** `TrajectoryChecker` class exists. `check(event)` orchestrates the three scorers, computes aggregate with `min()`, identifies `failing_dimension`, logs to OTel placeholder, raises `DriftDetected` if below threshold, returns `DriftResult` otherwise. Non-scoreable events return `None` immediately.

---

- [ ] 🟥 **Step 3: `TrajectoryChecker` class + `check()` + `_spec_version` + OTel log** — *Critical: this is the public interface; every call site interacts only with this class*

  **Step Architecture Thinking:**

  **Pattern applied:** **Facade** — `TrajectoryChecker.check()` is the single entry point that orchestrates `_extract_node_snapshot → score_tool_compliance → score_constraint_violation → score_intent_alignment → aggregate → log → raise-or-return`. Callers call one method and either receive a `DriftResult` or catch `DriftDetected`. They never call individual scorers.

  **Why `min()` not weighted average:**
  A forbidden tool call (`tool_score=0.0`) with high intent alignment (`intent_score=0.9`) should always trigger detection — the tool violation is a hard spec breach regardless of how well-intentioned the action was. Any weighted average that allows a 0.0 score to be diluted is unsuitable for a hard-constraint enforcement system.

  **Failing dimension priority (tool > constraint > intent):**
  Tool violations are the most unambiguous — they're determined by a string lookup, not an LLM. Constraint violations are the next most unambiguous — the LLM is evaluating a discrete yes/no question. Intent misalignment is the most subjective — it's a continuous score subject to LLM variance. The priority ensures the most reliable signal is surfaced first.

  **Why step counter starts at 1 (not 0):**
  `raised_at_step=1` means "this is the first scored event". A 1-indexed counter reads more naturally in logs: "Drift at step 3" means the third node was the first failure. 0-indexed would read as "Drift at step 2" for the third node, which is confusing.

  **Why non-scoreable events return `None` (not skip silently):**
  Returning `None` gives the caller the ability to distinguish "this event was checked and passed" (returns `DriftResult`) from "this event was not applicable for checking" (returns `None`). If `check()` returned nothing for non-scoreable events, callers couldn't tell the difference.

  ---

  **Idempotent:** Yes — appending; Pre-Read Gate confirms anchor.

  **Pre-Read Gate:**
  - Run `grep -n "class TrajectoryChecker" /Users/ngchenmeng/Ballast/ballast/core/trajectory.py`. Must return nothing.
  - Run `grep -n "def score_intent_alignment" /Users/ngchenmeng/Ballast/ballast/core/trajectory.py`. Must return exactly 1 match — confirms Step 2 succeeded.

  Append to `/Users/ngchenmeng/Ballast/ballast/core/trajectory.py`:

  ```python


  # ---------------------------------------------------------------------------
  # Internal helpers
  # ---------------------------------------------------------------------------

  def _spec_version(spec: LockedSpec) -> str:
      """Stable 8-char identifier for a spec.

      SHA256 of (goal + success_criteria) truncated to 8 hex chars.
      Stable across restarts — identifies which spec was in effect when drift was detected.
      Not a security hash — purely for log correlation.
      """
      raw = (spec.goal + spec.success_criteria).encode()
      return hashlib.sha256(raw).hexdigest()[:8]


  # Event types that correspond to node boundaries in the LangGraph ReAct agent.
  # These are the Agent.iter() equivalents in the astream_events stream:
  #   on_tool_start  → tool node entered (check compliance before execution)
  #   on_tool_end    → tool node exited  (check constraint after execution)
  #   on_chain_end   → agent node exited (check intent after model decision)
  _CHECKABLE_EVENTS: frozenset[str] = frozenset({
      "on_tool_start",
      "on_tool_end",
      "on_chain_end",
  })


  # ---------------------------------------------------------------------------
  # TrajectoryChecker — the public interface
  # ---------------------------------------------------------------------------

  class TrajectoryChecker:
      """Mid-run drift detector. Initialised with a LockedSpec; call check() at every node.

      Usage in observe.py event loop:
          checker = TrajectoryChecker(spec, threshold=0.7)
          async for event in adapter.stream(goal, spec=spec.model_dump()):
              try:
                  checker.check(event)
              except DriftDetected as e:
                  # escalate to guardrails.py or log for demo
                  pass
              yield event  # or collect

      Key invariants:
          - Never catches DriftDetected internally — always propagates to caller
          - Never modifies spec — read-only consumer
          - Never writes to memory — caller decides what to persist
          - Fails silent on individual scorer errors — fail-safes prevent false positives
          - Only raises DriftDetected when aggregate score is unambiguously below threshold
      """

      def __init__(self, spec: LockedSpec, threshold: float = 0.7) -> None:
          """
          Args:
              spec:      The locked spec this run operates against.
              threshold: Minimum acceptable aggregate drift score. Below this raises DriftDetected.
                         Default 0.7 — conservative: prefer catching drift over false negatives.
                         Lower = more permissive (fewer alerts).
                         Higher = stricter (more alerts, more false positives on ambiguous actions).
          """
          self.spec = spec
          self.threshold = threshold
          self._step: int = 0
          self._spec_ver: str = _spec_version(spec)

      def check(self, event: dict) -> Optional[DriftResult]:
          """Score a single LangGraph event against the locked spec.

          Called for every event in the astream_events loop.
          Non-scoreable events (not in _CHECKABLE_EVENTS) return None immediately.
          Events with no extractable content return None (nothing to score).

          Returns:
              DriftResult — event was scored and aggregate >= threshold (no drift)
              None        — event type is not scoreable

          Raises:
              DriftDetected — aggregate score < self.threshold (drift detected)

          Note: DriftDetected is NEVER caught here. It always propagates to the caller.
          """
          if not isinstance(event, dict):
              return None
          if event.get("event") not in _CHECKABLE_EVENTS:
              return None

          self._step += 1
          snapshot = _extract_node_snapshot(event)

          if not snapshot.raw_content and not snapshot.tool_name:
              # Event is scoreable by type but has no content to evaluate.
              # Decrement step counter — this was not a real scored event.
              self._step -= 1
              return None

          tool_score = score_tool_compliance(snapshot, self.spec)
          constraint_score = score_constraint_violation(snapshot, self.spec)
          intent_score = score_intent_alignment(snapshot, self.spec)

          aggregate = min(tool_score, constraint_score, intent_score)

          # Identify failing dimension — priority: tool > constraint > intent
          if tool_score == aggregate and tool_score < 1.0:
              failing = "tool"
          elif constraint_score == aggregate and constraint_score < intent_score:
              failing = "constraint"
          elif aggregate < 1.0:
              failing = "intent"
          else:
              failing = "none"

          result = DriftResult(
              score=round(aggregate, 4),
              intent_score=round(intent_score, 4),
              tool_score=round(tool_score, 4),
              constraint_score=round(constraint_score, 4),
              failing_dimension=failing,
              node_snapshot=snapshot,
              spec_goal=self.spec.goal,
              spec_version=self._spec_ver,
              raised_at_step=self._step,
              threshold=self.threshold,
          )

          # OTel placeholder — maps 1:1 to span.set_attribute() when OTel is wired
          # Attribute names: drift.step, drift.score, drift.intent, drift.tool,
          #                  drift.constraint, drift.failing, drift.spec_version
          logger.debug(
              "drift_check step=%d score=%.3f intent=%.3f tool=%.3f "
              "constraint=%.3f failing=%r spec=%s node=%s",
              self._step, aggregate, intent_score, tool_score,
              constraint_score, failing, self._spec_ver,
              snapshot.node_name or snapshot.tool_name,
          )

          if aggregate < self.threshold:
              raise DriftDetected(result)

          return result

      @property
      def step_count(self) -> int:
          """Number of events that were actually scored (excludes non-scoreable and empty events)."""
          return self._step
  ```

  **What it does:** `_spec_version` computes the stable spec identifier. `_CHECKABLE_EVENTS` declares the three node-boundary event types. `TrajectoryChecker` initialises with spec and threshold, calls all three scorers on each checkable event, aggregates with `min()`, logs structured debug output, and raises `DriftDetected` when below threshold.

  **Why `self._step -= 1` on empty content:**
  `step_count` should reflect the number of events that were actually scored with all three dimensions. A no-content event that passes through the type guard but has nothing to evaluate should not inflate the count — it would make `raised_at_step` misleading in the DriftResult.

  **Assumptions:**
  - `_extract_node_snapshot`, `score_tool_compliance`, `score_constraint_violation`, `score_intent_alignment` all defined earlier in the same file (Step 2)
  - `hashlib` is in the Python standard library (no import needed beyond what's at file top)
  - `DriftResult`, `DriftDetected`, `NodeSnapshot`, `LockedSpec` defined earlier in same file (Step 1)

  **Risks:**
  - `aggregate < self.threshold` uses float comparison — could fail on exact equality? No — `round(..., 4)` normalises both sides; and `aggregate == threshold` means the check passes (>= threshold), not fails.

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/trajectory.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.3: add TrajectoryChecker with check(), failing_dimension, and OTel log"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm `TrajectoryChecker` does not exist; `score_intent_alignment` exists
  - [ ] 🟥 Append `_spec_version`, `_CHECKABLE_EVENTS`, and `TrajectoryChecker` to `trajectory.py`
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit (no API call — mocks scorers)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  from unittest.mock import patch
  from ballast.core.trajectory import (
      TrajectoryChecker, DriftDetected, DriftResult, _spec_version,
  )
  from ballast.core.spec import LockedSpec, IntentSignal

  spec = LockedSpec(
      goal='count words', domain='test', success_criteria='word count returned',
      scope='', constraints=[], output_format='', inferred_assumptions=[],
      allowed_tools=['get_word_count'],
      intent_signal=IntentSignal(latent_goal='count', action_type='READ', salient_entity_types=[]),
      clarification_asked=False, threshold_used=0.6,
  )

  checker = TrajectoryChecker(spec, threshold=0.7)
  assert checker.step_count == 0
  assert len(checker._spec_ver) == 8

  # Non-scoreable event → None, no exception, step not incremented
  result = checker.check({'event': 'on_chain_start', 'data': {}})
  assert result is None
  assert checker.step_count == 0
  print('non-scoreable event → None OK')

  # Checkable event with all scores >= threshold → DriftResult returned, no raise
  tool_event = {'event': 'on_tool_start', 'name': 'get_word_count',
                'data': {'input': {'text': 'hello world'}}}
  with patch('ballast.core.trajectory.score_intent_alignment', return_value=0.9), \
       patch('ballast.core.trajectory.score_constraint_violation', return_value=1.0):
      result = checker.check(tool_event)
  assert isinstance(result, DriftResult)
  assert result.score >= 0.7
  assert result.tool_score == 1.0   # get_word_count is in allowed_tools
  assert result.failing_dimension == 'none'
  assert checker.step_count == 1
  print('passing check → DriftResult OK')

  # Tool NOT in allowed_tools → DriftDetected raised immediately
  bad_tool_event = {'event': 'on_tool_start', 'name': 'forbidden_tool',
                    'data': {'input': {'x': 1}}}
  try:
      with patch('ballast.core.trajectory.score_intent_alignment', return_value=1.0), \
           patch('ballast.core.trajectory.score_constraint_violation', return_value=1.0):
          checker.check(bad_tool_event)
      assert False, 'Expected DriftDetected to be raised'
  except DriftDetected as e:
      assert e.result.tool_score == 0.0
      assert e.result.failing_dimension == 'tool'
      assert e.result.score == 0.0
      assert e.result.raised_at_step == 2
  print('forbidden tool → DriftDetected(failing=tool) OK')

  # None input → None, no raise, step unchanged
  result = checker.check(None)
  assert result is None
  assert checker.step_count == 2  # unchanged
  print('None input → None OK')

  # _spec_version is stable
  v1 = _spec_version(spec)
  v2 = _spec_version(spec)
  assert v1 == v2 and len(v1) == 8
  print('_spec_version stable OK')

  print('Step 3 TrajectoryChecker OK')
  "
  ```

  **Expected:** 5 OK lines followed by `Step 3 TrajectoryChecker OK`.

  **Pass:** All assertions pass with exit code 0.

  **Fail:**
  - `ImportError: TrajectoryChecker` → append failed → re-read `trajectory.py`
  - `AssertionError: tool_score == 1.0` → `score_tool_compliance` not called with correct spec → check `allowed_tools` on spec construction
  - `DriftDetected not raised` → threshold check logic inverted → re-read `if aggregate < self.threshold`
  - `result.raised_at_step != 2` → step counter not incrementing correctly → re-read `self._step += 1` placement

---

### Phase 4 — Tests

**Goal:** `tests/test_trajectory.py` proves all contracts. All prior tests still pass. New test count ≥ 24 + new spec tests + 20 new trajectory unit tests.

---

- [ ] 🟥 **Step 4: `tests/test_trajectory.py`** — *Critical: proves scorer contracts, checker state machine, dimension priority, and OTel log without live LLM calls*

  **Step Architecture Thinking:**

  **Why mock `score_intent_alignment` and `score_constraint_violation` but not `score_tool_compliance`:**
  `score_tool_compliance` is pure Python with no I/O — it is directly testable without mocking. Mocking it would test the mock, not the function. `score_intent_alignment` and `score_constraint_violation` call Claude — mocking them isolates the orchestration logic from LLM nondeterminism.

  **Why test `_extract_node_snapshot` directly:**
  The extractor handles multiple LangGraph event shapes (dict messages, LangChain AIMessage objects, plain string outputs). These are real production shapes. Testing them directly confirms the extractor handles each shape before the scorer ever receives it.

  ---

  **Idempotent:** Yes — full overwrite of existing test file.

  **Pre-Read Gate:**
  - Read the full existing `tests/test_trajectory.py`. This file will be completely replaced.
  - Run `grep -n "def validate_trajectory" /Users/ngchenmeng/Ballast/ballast/core/trajectory.py`. Must return nothing — confirms the old parta API is gone.
  - Run `grep -n "class TrajectoryChecker" /Users/ngchenmeng/Ballast/ballast/core/trajectory.py`. Must return exactly 1 match.

  Write `/Users/ngchenmeng/Ballast/tests/test_trajectory.py`:

  ```python
  """Tests for ballast/core/trajectory.py — mid-run drift detection.

  All unit tests mock score_intent_alignment and score_constraint_violation.
  score_tool_compliance is tested directly (pure Python, no LLM).
  Integration test requires ANTHROPIC_API_KEY. Skip with: pytest -m 'not integration'
  """
  from unittest.mock import patch
  import pytest

  from ballast.core.spec import LockedSpec, IntentSignal
  from ballast.core.trajectory import (
      NodeSnapshot,
      DriftResult,
      DriftDetected,
      TrajectoryChecker,
      _extract_node_snapshot,
      _spec_version,
      score_tool_compliance,
      score_intent_alignment,
      score_constraint_violation,
  )


  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  def _make_spec(
      allowed_tools: list = None,
      constraints: list = None,
      scope: str = "",
  ) -> LockedSpec:
      return LockedSpec(
          goal="count words in readme",
          domain="test",
          success_criteria="word count is returned",
          scope=scope,
          constraints=constraints or [],
          output_format="",
          inferred_assumptions=[],
          allowed_tools=allowed_tools or [],
          intent_signal=IntentSignal(
              latent_goal="word count",
              action_type="READ",
              salient_entity_types=[],
          ),
          clarification_asked=False,
          threshold_used=0.6,
      )


  def _tool_event(tool_name: str, args: dict = None) -> dict:
      return {
          "event": "on_tool_start",
          "name": tool_name,
          "data": {"input": args or {"text": "hello"}},
      }


  def _chain_end_event(content: str = "done") -> dict:
      return {
          "event": "on_chain_end",
          "name": "agent",
          "data": {"output": {"messages": [{"content": content}]}},
      }


  # ---------------------------------------------------------------------------
  # _extract_node_snapshot
  # ---------------------------------------------------------------------------

  def test_extract_tool_start():
      event = {"event": "on_tool_start", "name": "my_tool",
               "data": {"input": {"query": "hello"}}}
      snap = _extract_node_snapshot(event)
      assert snap.tool_name == "my_tool"
      assert snap.tool_args == {"query": "hello"}
      assert "query" in snap.raw_content


  def test_extract_tool_end():
      event = {"event": "on_tool_end", "name": "my_tool",
               "data": {"output": "42"}}
      snap = _extract_node_snapshot(event)
      assert snap.tool_output == "42"
      assert snap.raw_content == "42"


  def test_extract_chain_end_dict_messages():
      event = _chain_end_event("word count is 4")
      snap = _extract_node_snapshot(event)
      assert snap.model_output == "word count is 4"
      assert snap.raw_content == "word count is 4"


  def test_extract_chain_end_langchain_message_object():
      """Real LangGraph output uses AIMessage objects, not dicts."""
      class FakeAIMessage:
          def __init__(self, content):
              self.content = content

      event = {
          "event": "on_chain_end", "name": "agent",
          "data": {"output": {"messages": [FakeAIMessage("word count is 7")]}},
      }
      snap = _extract_node_snapshot(event)
      assert snap.model_output == "word count is 7"


  def test_extract_unknown_event_returns_empty():
      snap = _extract_node_snapshot({"event": "on_something_new", "data": {}})
      assert snap.raw_content == ""
      assert snap.tool_name == ""


  def test_extract_none_returns_empty():
      snap = _extract_node_snapshot(None)
      assert snap.event_type == ""
      assert snap.raw_content == ""


  # ---------------------------------------------------------------------------
  # score_tool_compliance — pure Python, no mock needed
  # ---------------------------------------------------------------------------

  def test_tool_compliance_empty_allowed_all_permitted():
      snap = NodeSnapshot(event_type="on_tool_start", node_name="t", tool_name="any_tool")
      assert score_tool_compliance(snap, _make_spec(allowed_tools=[])) == 1.0


  def test_tool_compliance_tool_in_list():
      snap = NodeSnapshot(event_type="on_tool_start", node_name="t", tool_name="get_word_count")
      assert score_tool_compliance(snap, _make_spec(allowed_tools=["get_word_count"])) == 1.0


  def test_tool_compliance_tool_not_in_list():
      snap = NodeSnapshot(event_type="on_tool_start", node_name="t", tool_name="forbidden")
      assert score_tool_compliance(snap, _make_spec(allowed_tools=["get_word_count"])) == 0.0


  def test_tool_compliance_non_tool_event_always_passes():
      snap = NodeSnapshot(event_type="on_chain_end", node_name="agent")
      # tool_name is empty → not a tool event
      assert score_tool_compliance(snap, _make_spec(allowed_tools=["get_word_count"])) == 1.0


  # ---------------------------------------------------------------------------
  # _spec_version
  # ---------------------------------------------------------------------------

  def test_spec_version_is_8_chars():
      assert len(_spec_version(_make_spec())) == 8


  def test_spec_version_is_stable():
      spec = _make_spec()
      assert _spec_version(spec) == _spec_version(spec)


  def test_spec_version_differs_for_different_goals():
      spec1 = _make_spec()
      spec2 = LockedSpec(
          goal="different goal", domain="test",
          success_criteria="word count is returned",
          scope="", constraints=[], output_format="", inferred_assumptions=[],
          allowed_tools=[],
          intent_signal=IntentSignal(latent_goal="x", action_type="READ", salient_entity_types=[]),
          clarification_asked=False, threshold_used=0.6,
      )
      assert _spec_version(spec1) != _spec_version(spec2)


  # ---------------------------------------------------------------------------
  # TrajectoryChecker — non-scoreable events
  # ---------------------------------------------------------------------------

  def test_checker_non_scoreable_event_returns_none():
      checker = TrajectoryChecker(_make_spec())
      result = checker.check({"event": "on_chain_start", "data": {}})
      assert result is None
      assert checker.step_count == 0


  def test_checker_none_input_returns_none():
      checker = TrajectoryChecker(_make_spec())
      assert checker.check(None) is None
      assert checker.step_count == 0


  def test_checker_empty_content_event_returns_none():
      """on_chain_end with no extractable content is skipped."""
      checker = TrajectoryChecker(_make_spec())
      # on_chain_end with empty output
      result = checker.check({"event": "on_chain_end", "name": "agent",
                              "data": {"output": {}}})
      assert result is None
      assert checker.step_count == 0


  # ---------------------------------------------------------------------------
  # TrajectoryChecker — passing checks (score >= threshold)
  # ---------------------------------------------------------------------------

  def test_checker_passing_check_returns_drift_result():
      spec = _make_spec(allowed_tools=["get_word_count"])
      checker = TrajectoryChecker(spec, threshold=0.7)
      event = _tool_event("get_word_count")
      with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.9), \
           patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
          result = checker.check(event)
      assert isinstance(result, DriftResult)
      assert result.score >= 0.7
      assert result.tool_score == 1.0
      assert result.failing_dimension == "none"
      assert checker.step_count == 1


  def test_checker_step_count_increments_on_scored_events():
      checker = TrajectoryChecker(_make_spec())
      with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.8), \
           patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
          checker.check(_tool_event("any_tool"))
          checker.check(_chain_end_event("output"))
      assert checker.step_count == 2


  # ---------------------------------------------------------------------------
  # TrajectoryChecker — drift detected (score < threshold)
  # ---------------------------------------------------------------------------

  def test_checker_forbidden_tool_raises_drift_detected():
      spec = _make_spec(allowed_tools=["get_word_count"])
      checker = TrajectoryChecker(spec, threshold=0.7)
      with pytest.raises(DriftDetected) as exc_info:
          with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
               patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
              checker.check(_tool_event("forbidden_tool"))
      result = exc_info.value.result
      assert result.tool_score == 0.0
      assert result.score == 0.0
      assert result.failing_dimension == "tool"


  def test_checker_constraint_violation_raises_drift_detected():
      spec = _make_spec(constraints=["do not modify files"])
      checker = TrajectoryChecker(spec, threshold=0.7)
      with pytest.raises(DriftDetected) as exc_info:
          with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.9), \
               patch("ballast.core.trajectory.score_constraint_violation", return_value=0.0):
              checker.check(_chain_end_event("I modified the file"))
      result = exc_info.value.result
      assert result.constraint_score == 0.0
      assert result.failing_dimension == "constraint"


  def test_checker_intent_misalignment_raises_drift_detected():
      checker = TrajectoryChecker(_make_spec(), threshold=0.7)
      with pytest.raises(DriftDetected) as exc_info:
          with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.3), \
               patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
              checker.check(_chain_end_event("irrelevant output"))
      result = exc_info.value.result
      assert result.intent_score == 0.3
      assert result.failing_dimension == "intent"
      assert result.score == 0.3


  # ---------------------------------------------------------------------------
  # TrajectoryChecker — failing_dimension priority
  # ---------------------------------------------------------------------------

  def test_failing_dimension_tool_beats_constraint_when_equal():
      """When tool and constraint scores are equal and both below threshold,
      tool is the failing dimension (tool > constraint priority)."""
      spec = _make_spec(allowed_tools=["good_tool"], constraints=["do not do x"])
      checker = TrajectoryChecker(spec, threshold=0.7)
      with pytest.raises(DriftDetected) as exc_info:
          with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
               patch("ballast.core.trajectory.score_constraint_violation", return_value=0.0):
              checker.check(_tool_event("forbidden_tool"))
      result = exc_info.value.result
      assert result.tool_score == 0.0
      assert result.constraint_score == 0.0
      assert result.failing_dimension == "tool"


  def test_failing_dimension_none_when_all_pass():
      checker = TrajectoryChecker(_make_spec(), threshold=0.7)
      with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
           patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
          result = checker.check(_chain_end_event("on track"))
      assert result.failing_dimension == "none"
      assert result.score == 1.0


  # ---------------------------------------------------------------------------
  # DriftResult fields
  # ---------------------------------------------------------------------------

  def test_drift_result_raised_at_step_increments():
      checker = TrajectoryChecker(_make_spec(), threshold=0.0)  # threshold=0 → never raises
      with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
           patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
          r1 = checker.check(_chain_end_event("step 1"))
          r2 = checker.check(_chain_end_event("step 2"))
      assert r1.raised_at_step == 1
      assert r2.raised_at_step == 2


  def test_drift_result_spec_version_matches_spec():
      spec = _make_spec()
      checker = TrajectoryChecker(spec, threshold=0.0)
      with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
           patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
          result = checker.check(_chain_end_event("x"))
      assert result.spec_version == _spec_version(spec)


  def test_drift_detected_exception_message_contains_step_and_score():
      checker = TrajectoryChecker(_make_spec(allowed_tools=["safe"]), threshold=0.7)
      try:
          with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
               patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
              checker.check(_tool_event("forbidden"))
  except DriftDetected as e:
          assert "step 1" in str(e)
          assert "tool" in str(e)


  # ---------------------------------------------------------------------------
  # Integration test — requires ANTHROPIC_API_KEY
  # ---------------------------------------------------------------------------

  @pytest.mark.integration
  def test_checker_integration_real_llm():
      """Smoke test: real LLM calls for intent and constraint scoring.

      Requires ANTHROPIC_API_KEY. Skip with: pytest -m 'not integration'
      """
      import os
      if not os.environ.get("ANTHROPIC_API_KEY"):
          pytest.skip("ANTHROPIC_API_KEY not set")

      spec = _make_spec(
          allowed_tools=["get_word_count"],
          constraints=["do not modify any files"],
          scope="readme.md only",
      )
      checker = TrajectoryChecker(spec, threshold=0.5)

      # Valid tool call that matches spec — should not raise
      event = _tool_event("get_word_count", {"text": "the quick brown fox"})
      result = checker.check(event)
      assert isinstance(result, DriftResult), "Valid call should return DriftResult, not raise"
      assert result.tool_score == 1.0, "get_word_count is in allowed_tools"
      print(f"\nIntegration result: score={result.score:.2f} intent={result.intent_score:.2f} "
            f"tool={result.tool_score:.2f} constraint={result.constraint_score:.2f} "
            f"failing={result.failing_dimension}")
  ```

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add tests/test_trajectory.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.4: replace test_trajectory.py with mid-run drift detection contract tests"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: read existing test_trajectory.py in full; confirm `validate_trajectory` is absent from trajectory.py
  - [ ] 🟥 Write `tests/test_trajectory.py` (full overwrite)
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ -v -m "not integration" --tb=short 2>&1 | tail -10
  ```

  **Expected:** All prior tests still pass + all new trajectory unit tests pass. 0 failed. Integration test skipped.

  **Pass:** `0 failed`, `1 skipped` (integration test).

  **Fail:**
  - `ImportError: cannot import name 'TrajectoryChecker'` → Step 3 append failed → re-read `trajectory.py`
  - `ImportError: cannot import name 'score_tool_compliance'` → Step 2 append failed → re-read `trajectory.py`
  - Old spec tests failing → `allowed_tools` append to spec.py broke existing field order → re-read Step 1 Part A anchor
  - `test_checker_empty_content_event_returns_none` fails → step decrement on empty content not implemented → re-read Step 3 `self._step -= 1` logic

---

### Phase 5 — Integration

**Goal:** `observe.py` demonstrates `TrajectoryChecker` wired in the live event loop. `DriftDetected` is caught and logged. A compliant run (word count tool, no forbidden tools) produces per-event drift scores in the log output.

---

- [ ] 🟥 **Step 5: Wire `observe.py` — append `TrajectoryChecker` to the event collection loop** — *Non-critical: library is complete; this makes drift detection observable*

  **Pre-Read Gate:**
  - Run `grep -n "TrajectoryChecker\|DriftDetected" /Users/ngchenmeng/Ballast/scripts/observe.py`. Must return nothing. If match → STOP.
  - Run `grep -n "lock_spec" /Users/ngchenmeng/Ballast/scripts/observe.py`. Must return exactly 1 match — confirms phrase1-parta wiring is present.
  - Run `grep -n "events.append" /Users/ngchenmeng/Ballast/scripts/observe.py`. Record line number — the new checker call goes INSIDE the same loop, immediately before `events.append`.
  - Run `grep -n "print.*Done\|print.*Final intent" /Users/ngchenmeng/Ballast/scripts/observe.py`. Record the final print lines — the drift summary is added AFTER these.

  **Append inside `main()` in `/Users/ngchenmeng/Ballast/scripts/observe.py`** — two changes to the existing main() body only. Do NOT replace the entire function.

  **Change 1** — replace the import line at the top of main() to add the trajectory imports:
  ```python
  # Before:
      from ballast.core.spec import lock_spec, RunPhaseTracker
  # After:
      from ballast.core.spec import lock_spec, RunPhaseTracker
      from ballast.core.trajectory import TrajectoryChecker, DriftDetected
  ```

  **Change 2** — replace the event collection loop body to add the checker call:
  ```python
  # Before:
      events = []
      async for event in adapter.stream(OBSERVATION_GOAL, spec=spec.model_dump()):
          tracker.update(event)
          events.append(event)
  # After:
      checker = TrajectoryChecker(spec, threshold=0.7)
      drift_results = []
      events = []
      async for event in adapter.stream(OBSERVATION_GOAL, spec=spec.model_dump()):
          try:
              result = checker.check(event)
              if result is not None:
                  drift_results.append(result)
          except DriftDetected as e:
              # trajectory.py detected drift — log for demo; guardrails.py handles in production
              print(f"\n[observe.py] ⚠  DRIFT DETECTED at step {e.result.raised_at_step}")
              print(f"  score={e.result.score:.2f}  failing={e.result.failing_dimension}")
              print(f"  intent={e.result.intent_score:.2f}  tool={e.result.tool_score:.2f}  "
                    f"constraint={e.result.constraint_score:.2f}")
              drift_results.append(e.result)
              # Demo mode: continue the run; production guardrails.py would abort here
          tracker.update(event)
          events.append(event)
  ```

  **Change 3** — replace `print("[observe.py] Done.")` with the drift summary:
  ```python
      if drift_results:
          print(f"\n[observe.py] Drift summary ({len(drift_results)} scored events):")
          for r in drift_results:
              status = "DRIFT" if r.score < 0.7 else "OK"
              print(f"  [{r.raised_at_step}] {status}  score={r.score:.2f}  "
                    f"failing={r.failing_dimension}  node={r.node_snapshot.node_name or r.node_snapshot.tool_name}")
      print("[observe.py] Done.")
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm TrajectoryChecker not in observe.py; confirm lock_spec IS present
  - [ ] 🟥 Apply Change 1: add trajectory imports inside main()
  - [ ] 🟥 Apply Change 2: replace event collection loop to add checker
  - [ ] 🟥 Apply Change 3: replace final print with drift summary + Done
  - [ ] 🟥 Git checkpoint commit

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add scripts/observe.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.5: wire TrajectoryChecker in observe.py event loop"
  ```

  **✓ Verification Test:**

  **Type:** Import check (live run requires `ANTHROPIC_API_KEY`)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  from ballast.core.trajectory import (
      TrajectoryChecker, DriftDetected, DriftResult,
      NodeSnapshot, score_tool_compliance,
  )
  print('all public symbols importable OK')
  "

  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ -v -m "not integration" --tb=short 2>&1 | tail -5
  ```

  **Pass:** Import prints OK; all tests pass; 0 failed.

  **Fail:**
  - Import error → trajectory.py missing a step's append → check which symbol is missing
  - Any test now failing → observe.py edit broke an import → revert only the observe.py change

---

## Regression Guard

| System | Why it could be affected | Mitigation |
|--------|--------------------------|------------|
| `ballast/core/spec.py` | `allowed_tools` field appended to `LockedSpec` — if it lands after `model_config`, the class is broken | Pre-Read Gate confirms anchor line; all existing spec tests must still pass |
| `tests/test_spec.py` | Tests construct `LockedSpec` without `allowed_tools` — must still work with new default `[]` | `allowed_tools` has `default_factory=list` — no breaking change |
| `tests/test_memory.py` | Not touched | Run as part of final pytest |
| `tests/test_stream.py` | Not touched | Run as part of final pytest |
| `ballast/adapters/agui.py` | Not touched | `grep -n "TrajectoryChecker" agui.py` → no matches |

**Test count regression check:**
- Tests before plan: ≥ 59 passed (parta complete)
- Tests after plan (non-integration): ≥ 59 + 20 new trajectory tests = ≥ 79 passed
- Integration test: must show as `skipped`, not `failed`, when key not set

---

## Rollback Procedure

```bash
# Rollback Step 5 (observe.py)
git -C /Users/ngchenmeng/Ballast revert HEAD  # reverts step 3.5

# Rollback Step 4 (test_trajectory.py replacement)
git -C /Users/ngchenmeng/Ballast revert HEAD  # reverts step 3.4

# Rollback Step 3 (TrajectoryChecker)
git -C /Users/ngchenmeng/Ballast revert HEAD  # reverts step 3.3

# Rollback Step 2 (scoring functions)
git -C /Users/ngchenmeng/Ballast revert HEAD  # reverts step 3.2

# Rollback Step 1 (trajectory.py overwrite + spec.py allowed_tools)
git -C /Users/ngchenmeng/Ballast revert HEAD  # reverts step 3.1

# Verify rollback
cat /Users/ngchenmeng/Ballast/ballast/core/trajectory.py | head -5
# Must show the parta thin validator header
grep -n "allowed_tools" /Users/ngchenmeng/Ballast/ballast/core/spec.py
# Must return nothing
/Users/ngchenmeng/Ballast/venv/bin/pytest tests/ -v --tb=short | tail -3
# Must show: ≥ 59 passed
```

---

## Pre-Flight Checklist

| Phase | Check | How to Confirm | Status |
|-------|-------|----------------|--------|
| Pre-flight | ≥ 59 tests pass | `pytest tests/ -v \| tail -3` → ≥ 59 passed | ⬜ |
| Pre-flight | `allowed_tools` not in spec.py | `grep -n "allowed_tools" spec.py` → no output | ⬜ |
| Pre-flight | existing `trajectory.py` read | `cat ballast/core/trajectory.py` → shows parta thin validator | ⬜ |
| Phase 1 Step 1 | `allowed_tools` field default=[] | `python -c "from spec import LockedSpec; s=LockedSpec(...); assert s.allowed_tools == []"` | ⬜ |
| Phase 1 Step 1 | Models import cleanly | `from ballast.core.trajectory import DriftResult, DriftDetected, NodeSnapshot` | ⬜ |
| Phase 2 Step 2 | `score_tool_compliance` rule-based | `score_tool_compliance(snap_forbidden, spec_with_allowed) == 0.0` | ⬜ |
| Phase 2 Step 2 | `_extract_node_snapshot` handles all shapes | 8 extractor assertions pass | ⬜ |
| Phase 3 Step 3 | `TrajectoryChecker` importable | `from ballast.core.trajectory import TrajectoryChecker` | ⬜ |
| Phase 3 Step 3 | `DriftDetected` raised on forbidden tool | Step 3 verification test passes | ⬜ |
| Phase 4 Step 4 | ≥ 79 non-integration tests pass | `pytest -m 'not integration'` → ≥ 79 passed | ⬜ |
| Phase 4 Step 4 | Integration test skips without key | `ANTHROPIC_API_KEY=` pytest → 1 skipped | ⬜ |
| Phase 5 Step 5 | observe.py wired | `grep -n "TrajectoryChecker" scripts/observe.py` → 1 match | ⬜ |

---

## Success Criteria

| Feature | Target | Verification |
|---------|--------|--------------|
| `allowed_tools` on LockedSpec | Default `[]`, backward-compatible | All existing spec tests still pass |
| `score_tool_compliance` rule-based | `forbidden_tool → 0.0`, `allowed_tool → 1.0`, `empty_list → 1.0` | `test_tool_compliance_*` pass |
| `score_intent_alignment` LLM + fail-safe | Mocked: controlled float; error: 0.5 | Mock tests pass; integration smoke test passes |
| `score_constraint_violation` LLM + fail-safe | No constraints: 1.0; violation: 0.0; error: 0.5 | Mock tests pass |
| `TrajectoryChecker.check()` aggregates with min() | Score = min(intent, tool, constraint) | `test_checker_passing_check_returns_drift_result` passes |
| `DriftDetected` raised when score < threshold | Carries full `DriftResult` | `test_checker_forbidden_tool_raises_drift_detected` passes |
| `failing_dimension` priority | tool > constraint > intent | `test_failing_dimension_tool_beats_constraint_when_equal` passes |
| No regressions | All ≥ 59 prior tests still pass | `pytest tests/test_stream.py tests/test_memory.py tests/test_spec.py -v` passes |
| Total test count | ≥ 79 non-integration | `pytest -m 'not integration'` → ≥ 79 passed |
| trajectory.py never decides on drift | `check()` only raises — never catches | Code review: no `except DriftDetected` in trajectory.py |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Step 1 has two parts (spec.py append + trajectory.py overwrite) — both must succeed before the checkpoint commit.**
⚠️ **Steps 2 and 3 are appends to the same file — each Pre-Read Gate must confirm the previous step's anchor exists before appending.**
⚠️ **Step 4 is a full overwrite of test_trajectory.py — read the existing file before writing.**
⚠️ **Step 5 is three targeted changes inside main() — do NOT replace the entire function.**
⚠️ **trajectory.py must never contain `except DriftDetected` — that invariant must survive every edit.**
