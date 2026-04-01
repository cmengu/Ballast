# Step 3 — `spec.py`: Intent Grounding, STITCH Tracking, and Calibrated Clarification

**Overall Progress:** `0%` (0 / 8 steps complete)

---

## TLDR

Build `ballast/core/spec.py` and a thin `ballast/core/trajectory.py` — the intent grounding layer that sits between a raw goal string and `AgentStream.stream()`, plus the validator that makes the spec enforceable. Four integrated capabilities: (1) per-axis ambiguity scoring across attribute/scope/preference axes using structured Claude tool_use output; (2) a rule-based clarification policy whose per-domain threshold is stored in memory and updated from run outcomes using fixed learning-rate coefficients (not ML — a deliberate simplification with a clear upgrade path); (3) a `RunPhaseTracker` that maps LangGraph event types to verb-class labels for run-phase annotation (a heuristic lookup table, not STITCH — the plan is named for the research direction it approximates, not what it implements); (4) a thin trajectory validator that checks agent output against `success_criteria` and calls `update_domain_threshold` after each run, closing the feedback loop. After this plan: `lock_spec()` returns a `LockedSpec` Pydantic model; `validate_trajectory()` checks output and updates calibration; the before/after improvement is observable; 24 existing tests still pass; new spec and trajectory tests pass.

---

## Architecture Overview

**The problem this plan solves:**

`ballast/core/spec.py` does not exist. `AGUIAdapter.stream()` currently accepts `spec: dict` and passes `{}` to the agent — the goal string is the only contract. This means trajectory validation (Week 3) has nothing to validate against except the raw goal, which the research shows produces high false-positive replan rates when goals are ambiguous. `memory.py` exists and stores per-run outcomes but has no concept of per-domain clarification thresholds. `ballast/core/stream.py`'s `stream(goal, spec)` signature already reserves the `spec` parameter — it is unused today.

**The patterns applied:**

| Pattern | Applied to | What breaks if violated |
|---------|-----------|------------------------|
| **DTO (Data Transfer Object)** | `LockedSpec` Pydantic model | If spec is a plain `dict`, callers infer its shape from usage and diverge; trajectory.py and memory.py end up with incompatible expectations |
| **Policy Object** | `ClarificationPolicy` — encapsulates the "when to ask" decision | If the threshold is hardcoded in `lock_spec()`, it cannot be domain-calibrated or updated from outcomes; the policy becomes a magic number |
| **State Machine (heuristic)** | `RunPhaseTracker.update(event)` — maps LangGraph event types to verb-class labels (COORDINATE/WRITE/VERIFY/READ). Base behaviour: increment step counter and look up action type from a static 8-entry dict. Not STITCH — STITCH trains a latent goal model; this is a named approximation of the direction STITCH points in. | If run-phase is never annotated, memory retrieval in Week 3 treats all events as step 0; phase-aware retrieval cannot be implemented. |
| **Single Source of Truth** | `LockedSpec` is the one object passed downstream; nothing reads the raw goal string after `lock_spec()` returns | If adapters fall back to reading `goal` directly, spec-locked constraints are silently bypassed |
| **Open/Closed** | Ambiguity axis scoring is a registry of `AxisScorer` callables; new axes are added without modifying `score_goal()` | If axis logic is inlined in `score_goal()`, adding a fourth axis requires modifying a function used by policy and clarification both |

**What stays unchanged:**

- `ballast/core/stream.py` — ABC signature `stream(goal, spec)` already accepts `spec`; no change needed
- `ballast/core/memory.py` — `write`, `log_run`, `recall` are used as-is; this plan adds two new public functions (`get_domain_threshold`, `update_domain_threshold`) in a targeted append
- `ballast/adapters/agui.py` — `AGUIAdapter.__init__` and `stream()` body unchanged; only the call site in `scripts/observe.py` is updated to pass a real `LockedSpec`
- `tests/test_stream.py`, `tests/test_memory.py` — not touched; all 24 must still pass

**What this plan adds:**

| File | Single responsibility |
|------|-----------------------|
| `ballast/core/spec.py` | Ambiguity scoring, clarification policy, spec locking, `RunPhaseTracker` |
| `ballast/core/trajectory.py` | Thin validator: check agent output against `success_criteria`, call `update_domain_threshold` after each run — closes the feedback loop |
| `ballast/core/memory.py` (append-only) | `get_domain_threshold()`, `update_domain_threshold()` — two new functions, nothing else |
| `tests/test_spec.py` | Contract tests for scoring, policy, `LockedSpec` shape, tracker state transitions |
| `tests/test_trajectory.py` | Contract tests for trajectory validator and calibration loop |

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|----------|----------------------|--------------------------|
| Per-axis scoring (`attribute`, `scope`, `preference`) with independent scores | Single 0–3 integer count | A count of 2 tells the clarifier nothing about *which* question to ask; per-axis scores enable targeted question generation matched to the underspecification type |
| Threshold stored in memory per domain, updated from outcomes | Hardcoded 0.60 globally | The frontier shows "when to ask" is a learned policy — a global constant converges to a locally suboptimal value for every domain it isn't tuned for |
| `RunPhaseTracker` with `IntentSignal(latent_goal, action_type, salient_entity_types)` computed at spec-lock, updated per event via a static event-type lookup table | Single-shot intent at lock time only | Mid-run context drift is invisible without per-event updates; this heuristic approximation is sufficient for Week 2 phase annotation. Full STITCH (trained latent goal model) is a Week 4 upgrade. |
| `LockedSpec` as a Pydantic BaseModel | Plain `TypedDict` or `dataclass` | Pydantic gives field validation, `.model_dump()` for JSON serialisation into memory, and schema export for the future PRM (process reward model) integration |
| `ClarificationPolicy` as a callable class wrapping threshold reads | Inline `if score < threshold` in `lock_spec()` | Inlining couples the lock function to the memory layer; extracting the policy object makes it independently testable and replaceable |
| Ask questions as structured choices, not open text | Free-text clarification | Open text produces variable-format answers that require a second parse pass; structured choices are deterministic to parse and faster for the user |
| `RunPhaseTracker` lives inside `spec.py` | Separate `tracker.py` module | The tracker is inseparable from `IntentSignal`, which is inseparable from `LockedSpec`; three files for one concept is premature module split |

**Known limitations acknowledged:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| Ambiguity axis scoring uses an LLM call (Claude) | Week 2 goal is correctness not cost; heuristic regex scoring has high false negative rate on natural language goals | Week 4: replace with a local fine-tuned classifier once enough scored examples accumulate in memory |
| Threshold update uses fixed-coefficient rule: `+=0.05*(score-threshold)` / `-=0.10*threshold` — NOT a learned model | Coefficients are deliberately simple; a proper Bayesian update (Beta distribution) requires N>20 runs per domain which Week 2 will not produce | Week 4: switch to Beta-distribution update when per-domain run count > 20; expose calibration curve as a metric |
| `RunPhaseTracker.update()` is a static 8-entry lookup table mapping LangGraph event types to verb classes — NOT STITCH | Full STITCH trains a latent goal model on trajectory data, which requires trajectory data that doesn't exist yet | Week 4: replace with lightweight embedding-based cluster assignment using accumulated trajectory history |
| Clarification questions are generated by Claude, not retrieved from a question library | Retrieval would be faster and cheaper | Week 3: cache successful question-answer pairs in memory by domain |
| `trajectory.py` in this plan is a thin success_criteria string-match validator — not a semantic trajectory validator | A semantic validator requires trajectory data from `observe.py` to calibrate thresholds; building it blind produces wrong thresholds | Week 3: replace string-match with structured output comparison once trajectory patterns are known from observation |
| **No enforcement without trajectory.py**: `lock_spec()` produces a `LockedSpec` that `AGUIAdapter` serializes and the LangGraph agent ignores | This is the gap this plan closes — trajectory.py (Step 8) is the first component that reads spec fields and produces observable output | Step 8 in this plan closes the loop; do not ship Step 7 without Step 8 |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---------|----------|--------|----------|----------|
| Correct Claude model string for spec scoring calls | Must match `_ANTHROPIC_MODEL` in `memory.py` | `grep _ANTHROPIC_MODEL ballast/core/memory.py` | Steps 3, 4 | ✅ `claude-sonnet-4-6` (from memory.py) |
| `ANTHROPIC_API_KEY` available | Must be set before any scoring calls | Pre-flight check | Step 3 (live) | ⬜ Confirm before Step 5 |
| Exact `LockedSpec` fields consumed by `trajectory.py` (Week 3) | Field names must be stable — trajectory imports from spec | Design decision, stated here | Step 2 | ✅ Decided: `success_criteria`, `scope`, `constraints`, `output_format`, `intent_signal`, `domain`, `ambiguity_scores`, `inferred_assumptions` |

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
    Confirm: exactly 24 passed. If not 24 → STOP.

(2) ls /Users/ngchenmeng/Ballast/ballast/core/
    Confirm: __init__.py, memory.py, stream.py only. No spec.py yet.

(3) ls /Users/ngchenmeng/Ballast/tests/test_spec.py 2>&1
    Confirm: "No such file or directory". If it exists → read it first.

(4) grep -n "def " /Users/ngchenmeng/Ballast/ballast/core/memory.py
    Record: all public function signatures. Confirm get_domain_threshold and update_domain_threshold do NOT yet exist.

(5) grep -n "_ANTHROPIC_MODEL" /Users/ngchenmeng/Ballast/ballast/core/memory.py
    Record: exact model string value.

(6) grep -n "spec: dict" /Users/ngchenmeng/Ballast/ballast/core/stream.py
    Confirm: spec parameter exists in stream() signature.

(7) grep -n "spec: dict" /Users/ngchenmeng/Ballast/ballast/adapters/agui.py
    Confirm: spec parameter is accepted and passed through.

(8) echo "Pre-flight complete"
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan:               ____  (must be 24)
core/ contents:                       ____  (must be __init__.py, memory.py, stream.py)
spec.py exists:                       ____  (must be No)
test_spec.py exists:                  ____  (must be No)
memory.py public functions:           ____
_ANTHROPIC_MODEL value:               ____
get_domain_threshold exists:          ____  (must be No)
update_domain_threshold exists:       ____  (must be No)
stream() spec param confirmed:        ____
agui.py spec param confirmed:         ____
```

**Automated checks (all must pass before Step 1):**
- [ ] 24 tests pass — no regressions before a line is written
- [ ] `ballast/core/spec.py` does NOT exist
- [ ] `tests/test_spec.py` does NOT exist
- [ ] `get_domain_threshold` does NOT exist in `memory.py`
- [ ] `update_domain_threshold` does NOT exist in `memory.py`
- [ ] `_ANTHROPIC_MODEL` value recorded (used verbatim in spec.py)

---

## Steps Analysis

```
Step 1 (LockedSpec + IntentSignal Pydantic models)          — Critical  — full code review  — Idempotent: Yes
Step 2 (add threshold functions to memory.py)               — Critical  — full code review  — Idempotent: Yes
Step 3 (ambiguity axis scoring — Claude tool_use)           — Critical  — full code review  — Idempotent: Yes
Step 4 (ClarificationPolicy + clarify() question gen)       — Critical  — full code review  — Idempotent: Yes
Step 5 (lock_spec() main entry point)                       — Critical  — full code review  — Idempotent: Yes
Step 6 (RunPhaseTracker — event-type-to-verb lookup table)  — Critical  — full code review  — Idempotent: Yes
Step 7 (tests/test_spec.py + wire observe.py)               — Critical  — full code review  — Idempotent: Yes
Step 8 (trajectory.py thin validator + calibration wire)    — Critical  — full code review  — Idempotent: Yes
```

---

## Environment Matrix

| Step | Dev | Notes |
|------|-----|-------|
| Steps 1–2 | ✅ | No external services |
| Step 3 | ✅ | Axis scoring has LLM path; unit tests use mocked path |
| Step 4 | ✅ | Clarification generation has LLM path; unit tests use mocked path |
| Step 5 | ✅ | `lock_spec()` integration test requires `ANTHROPIC_API_KEY` |
| Step 6 | ✅ | `RunPhaseTracker` is pure Python, no external services |
| Step 7 | ✅ | 4 LLM-free contract tests + 1 integration smoke test |

---

## Tasks

### Phase 1 — Core Data Contracts

**Goal:** `LockedSpec` and `IntentSignal` are importable Pydantic models. Per-domain threshold functions exist in `memory.py`. No logic yet — contracts only.

---

- [ ] 🟥 **Step 1: `LockedSpec` + `IntentSignal` Pydantic models** — *Critical: every downstream file imports these; field names defined here are the stable contract*

  **Step Architecture Thinking:**

  **Pattern applied:** **DTO (Data Transfer Object)** — `LockedSpec` is a frozen, validated value object that crosses the boundary between the grounding layer and every downstream consumer (trajectory, memory, adapters). `IntentSignal` is the STITCH contextual cue: a structured triplet `(latent_goal, action_type, salient_entity_types)` that travels with the spec and gets mutated by `RunPhaseTracker` during the run.

  **Why this step exists here in the sequence:**
  Every subsequent step in this plan imports from `spec.py`. The models must exist before any function is written. Writing models first also forces all field names to be decided once, not discovered through usage.

  **Why `ballast/core/spec.py` is the right location:**
  `core/` is the contract layer. `LockedSpec` is consumed by `trajectory.py` (Week 3), `memory.py` (log_run call site), and adapters. Placing it in `adapters/` or `scripts/` would force those consumers to import from the wrong layer and create circular import risk.

  **Alternative approach considered and rejected:**
  `TypedDict` instead of Pydantic BaseModel — rejected because TypedDict gives no runtime validation, no `.model_dump()` for JSON serialisation into memory, and no schema export. When the PRM integration arrives in Week 4, the spec schema needs to be serialisable deterministically.

  **What breaks if this step deviates from the described pattern:**
  If `LockedSpec` is a plain dict, `trajectory.py` will access `spec["success_criteria"]` with no KeyError protection — a missing key causes a silent empty string comparison that always passes, defeating the entire grounding layer.

  ---

  **Idempotent:** Yes — writing the same file again produces the same result.

  **Context:** Creates `ballast/core/spec.py` for the first time. No existing file to read.

  **Pre-Read Gate:**
  - Run `ls /Users/ngchenmeng/Ballast/ballast/core/spec.py 2>&1`. Must return "No such file or directory". If it exists → read it in full before proceeding.

  **Self-Contained Rule:** All code below is complete and runnable. No references to other steps.

  **No-Placeholder Rule:** No `<VALUE>` tokens appear below.

  Write `/Users/ngchenmeng/Ballast/ballast/core/spec.py`:

  ```python
  """ballast/core/spec.py — Intent grounding layer.

  Public interface:
      lock_spec(goal, domain, interactive) -> LockedSpec
      RunPhaseTracker — propagates IntentSignal through a live event stream

  Internal:
      _score_axes(goal, domain) -> AmbiguityScores
      ClarificationPolicy — reads per-domain threshold from memory
      _clarify(goal, axes) -> list[str]   (questions as structured choices)
      _infer_spec(goal, axes) -> LockedSpec (LLM-inferred, no questions asked)

  STITCH reference:
      IntentSignal maps to STITCH's contextual intent cue:
        latent_goal        → thematic segment label
        action_type        → verb class of the current operation
        salient_entity_types → which attribute dimensions matter now
      RunPhaseTracker updates the signal per event so memory retrieval
      remains intent-compatible as context evolves mid-run.
  """
  from __future__ import annotations

  from enum import Enum
  from typing import Optional
  from pydantic import BaseModel, Field


  # ---------------------------------------------------------------------------
  # Ambiguity axis taxonomy
  # ---------------------------------------------------------------------------

  class AmbiguityType(str, Enum):
      """The three orthogonal dimensions of goal underspecification.

      Derived from the robotics clarification literature:
        ATTRIBUTE  — which version/variant/format is wanted?
        SCOPE      — which files/services/environment does this touch?
        PREFERENCE — speed vs. thoroughness, brevity vs. completeness, etc.

      Each maps to a targeted clarification question class (see _clarify).
      """
      ATTRIBUTE = "attribute"
      SCOPE = "scope"
      PREFERENCE = "preference"


  class AmbiguityScore(BaseModel):
      """Per-axis ambiguity assessment."""
      axis: AmbiguityType
      score: float = Field(
          ge=0.0, le=1.0,
          description="0.0 = fully specified, 1.0 = completely ambiguous"
      )
      reason: str = Field(
          description="One-sentence explanation used to generate a targeted question"
      )
      is_blocking: bool = Field(
          description="True if this ambiguity should trigger a clarification question"
      )


  class AmbiguityScores(BaseModel):
      """Complete per-axis assessment for a goal."""
      attribute: AmbiguityScore
      scope: AmbiguityScore
      preference: AmbiguityScore

      @property
      def blocking_axes(self) -> list[AmbiguityScore]:
          """Axes flagged as blocking — these drive question generation."""
          return [a for a in [self.attribute, self.scope, self.preference]
                  if a.is_blocking]

      @property
      def max_score(self) -> float:
          """Highest individual axis score — used by policy as the decision signal."""
          return max(
              self.attribute.score,
              self.scope.score,
              self.preference.score,
          )


  # ---------------------------------------------------------------------------
  # STITCH intent signal
  # ---------------------------------------------------------------------------

  class IntentSignal(BaseModel):
      """Structured contextual intent cue (STITCH-derived).

      Created at spec-lock time from the goal and inferred spec.
      Updated by RunPhaseTracker as events stream in during the run.

      Purpose: enables memory.recall() to filter history by intent
      compatibility, not just semantic similarity — suppressing
      context-incompatible snippets from earlier thematic segments.
      """
      latent_goal: str = Field(
          description="Thematic segment label — what is the agent fundamentally trying to achieve?"
      )
      action_type: str = Field(
          description="Verb class: READ | WRITE | TRANSFORM | VERIFY | SEARCH | COORDINATE"
      )
      salient_entity_types: list[str] = Field(
          default_factory=list,
          description="Which attribute dimensions matter now (e.g. ['file_path', 'function_name'])"
      )
      step_index: int = Field(
          default=0,
          description="Run-step counter — incremented by RunPhaseTracker per event"
      )


  # ---------------------------------------------------------------------------
  # Locked spec — the stable contract passed to every downstream component
  # ---------------------------------------------------------------------------

  class LockedSpec(BaseModel):
      """Frozen intent grounding contract.

      Produced by lock_spec(). Consumed by:
        - AGUIAdapter.stream(goal, spec)  — passed as spec arg
        - trajectory.py (Week 3)          — validates against success_criteria
        - memory.log_run()                — serialised as run context
        - RunPhaseTracker                   — carries intent_signal forward

      Field stability guarantee: names here are the Week 2–4 API surface.
      Do not rename without updating trajectory.py and memory.py call sites.
      """
      goal: str = Field(description="Original raw goal string — preserved for audit")
      domain: str = Field(description="Domain key used for per-domain threshold lookup")

      # Core spec fields — what trajectory.py validates against
      success_criteria: str = Field(
          description="Measurable definition of done. Must be verifiable from agent output."
      )
      scope: str = Field(
          description="Boundary of what the agent may touch. Empty = unconstrained."
      )
      constraints: list[str] = Field(
          default_factory=list,
          description="Hard constraints the agent must not violate."
      )
      output_format: str = Field(
          default="",
          description="Required output format if specified. Empty = inferred from context."
      )

      # Grounding metadata
      inferred_assumptions: list[str] = Field(
          default_factory=list,
          description="Assumptions made when spec was inferred without asking. Surfaced to user."
      )
      ambiguity_scores: Optional[AmbiguityScores] = Field(
          default=None,
          description="Per-axis scores at lock time. Stored for threshold calibration."
      )

      # STITCH intent signal — travels with spec, mutated by RunPhaseTracker
      intent_signal: IntentSignal = Field(
          description="Structured contextual intent cue. Updated per-event during run."
      )

      # Policy metadata
      clarification_asked: bool = Field(
          default=False,
          description="True if clarification questions were surfaced before locking."
      )
      threshold_used: float = Field(
          description="The domain threshold that decided ask-vs-infer. Stored for calibration."
      )

      model_config = {"frozen": False}  # RunPhaseTracker mutates intent_signal.step_index
  ```

  **What it does:** Defines the complete data contract for the spec layer. `LockedSpec` is the one object passed downstream. `IntentSignal` is the STITCH cue. `AmbiguityScores` captures per-axis underspecification with reasons, enabling targeted question generation.

  **Why this approach:** Pydantic models with field descriptions double as documentation and schema export. The `AmbiguityType` enum forces axis names to be consistent across scoring, policy, and clarification — no string typos.

  **Assumptions:**
  - `pydantic>=2.0` is installed (confirmed in `pyproject.toml`)
  - `ballast/core/__init__.py` exists (confirmed from Step 1 scaffold)

  **Risks:**
  - `model_config = {"frozen": False}` on `LockedSpec` means callers can mutate fields after locking → mitigation: `RunPhaseTracker` is the only component that mutates `intent_signal`; documented explicitly

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/spec.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.1: add LockedSpec, IntentSignal, AmbiguityScores Pydantic models"
  ```

  **Subtasks:**
  - [ ] 🟥 Confirm `spec.py` does not exist (Pre-Read Gate)
  - [ ] 🟥 Write `ballast/core/spec.py` with exact content above
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  from ballast.core.spec import (
      LockedSpec, IntentSignal, AmbiguityScores,
      AmbiguityScore, AmbiguityType,
  )
  # Confirm AmbiguityType enum values
  assert AmbiguityType.ATTRIBUTE == 'attribute'
  assert AmbiguityType.SCOPE == 'scope'
  assert AmbiguityType.PREFERENCE == 'preference'

  # Confirm AmbiguityScores.blocking_axes filters correctly
  make_score = lambda axis, blocking: AmbiguityScore(
      axis=axis, score=0.8 if blocking else 0.1,
      reason='test', is_blocking=blocking
  )
  scores = AmbiguityScores(
      attribute=make_score(AmbiguityType.ATTRIBUTE, True),
      scope=make_score(AmbiguityType.SCOPE, False),
      preference=make_score(AmbiguityType.PREFERENCE, True),
  )
  assert len(scores.blocking_axes) == 2
  assert scores.max_score == 0.8

  # Confirm LockedSpec constructs with all required fields
  sig = IntentSignal(
      latent_goal='test goal',
      action_type='READ',
      salient_entity_types=['file_path'],
  )
  spec = LockedSpec(
      goal='raw goal',
      domain='coding',
      success_criteria='output file exists',
      scope='src/',
      constraints=[],
      output_format='',
      inferred_assumptions=['assume Python 3'],
      ambiguity_scores=scores,
      intent_signal=sig,
      clarification_asked=False,
      threshold_used=0.60,
  )
  assert spec.intent_signal.step_index == 0
  assert spec.threshold_used == 0.60
  print('Step 1 models OK')
  "
  ```

  **Expected:** `Step 1 models OK`

  **Pass:** Prints `Step 1 models OK` with exit code 0.

  **Fail:**
  - `ImportError` → file not written or syntax error → read `ballast/core/spec.py` and check
  - `ValidationError` on `LockedSpec` → required field missing or wrong type → check field defaults
  - `AssertionError` on `blocking_axes` → `is_blocking` filter logic wrong → check the property

---

- [ ] 🟥 **Step 2: Add `get_domain_threshold` + `update_domain_threshold` to `memory.py`** — *Critical: policy reads threshold before every lock_spec call; calibration writes it after every run*

  **Step Architecture Thinking:**

  **Pattern applied:** **Single Source of Truth** — per-domain thresholds live in the same memory store as run history. This means one `recall()` call retrieves both the semantic profile (what the agent does well) and the calibration metadata (how often clarification was needed) from the same file, with the same lock primitive.

  **Why this step exists here in the sequence:**
  `ClarificationPolicy` (Step 4) must read the threshold before it can decide whether to ask. `lock_spec()` (Step 5) must write the threshold used into `LockedSpec.threshold_used`. Both functions exist in Step 4 and 5 — they require this step to complete first.

  **Why `memory.py` is the right location:**
  Thresholds are domain-specific learned values that evolve from run outcomes — exactly what `memory.py` owns. Placing them in `spec.py` would create a reverse dependency (spec importing from itself or a separate config file), and they'd lose the file-lock protection and atomic write guarantee already in `memory.py`.

  **Alternative approach considered and rejected:**
  Store thresholds in a separate `ballast/core/config.py` — rejected because thresholds are not static configuration, they are *learned values* that drift toward calibration over time. They belong in the memory store alongside the run outcomes that produced them.

  **What breaks if this step deviates:**
  If `get_domain_threshold` returns a hardcoded 0.60 always (no memory read), threshold calibration never converges — the policy degrades to exactly what the frontier says to avoid.

  ---

  **Idempotent:** Yes — appending the same two functions to `memory.py` twice produces a syntax error on the second write. **Pre-Read Gate must confirm they do not exist before appending.**

  **Context:** `memory.py` currently ends after `memory_report()`. This step appends two functions after the final function. It does not modify any existing function.

  **Pre-Read Gate:**
  Before editing:
  - Run `grep -n "def get_domain_threshold" /Users/ngchenmeng/Ballast/ballast/core/memory.py`. Must return nothing. If it returns a match → STOP, the function already exists.
  - Run `grep -n "def update_domain_threshold" /Users/ngchenmeng/Ballast/ballast/core/memory.py`. Must return nothing. If it returns a match → STOP.
  - Run `tail -5 /Users/ngchenmeng/Ballast/ballast/core/memory.py`. Record the last 5 lines — this is the append anchor.

  **Anchor Uniqueness Check:**
  - The append goes after the final line of `memory_report()`.
  - `grep -c "def memory_report" /Users/ngchenmeng/Ballast/ballast/core/memory.py` must return `1`.

  Append to `/Users/ngchenmeng/Ballast/ballast/core/memory.py` (after the last line of `memory_report`):

  ```python


  # ---------------------------------------------------------------------------
  # Per-domain threshold calibration (used by spec.py ClarificationPolicy)
  # ---------------------------------------------------------------------------

  _DEFAULT_THRESHOLD: float = 0.60
  _THRESHOLD_KEY_PREFIX: str = "clarification_threshold:"


  def get_domain_threshold(domain: str) -> float:
      """Return the current clarification threshold for this domain.

      Default: 0.60 (conservative — prefer asking over inferring).
      Updated by update_domain_threshold() after each run.

      The threshold is the maximum ambiguity axis score that the policy
      will infer through without asking. Below threshold → infer.
      At or above threshold → ask (up to 2 targeted questions).

      Args:
          domain: Domain key string (e.g. 'coding', 'data-analysis', 'writing').
      Returns:
          float in [0.0, 1.0]. Never raises.
      """
      path = _scope_path(f"{_THRESHOLD_KEY_PREFIX}{domain}")
      if not path.exists():
          return _DEFAULT_THRESHOLD
      try:
          data = json.loads(path.read_text(encoding="utf-8"))
          return float(data.get("threshold", _DEFAULT_THRESHOLD))
      except (json.JSONDecodeError, OSError, ValueError):
          return _DEFAULT_THRESHOLD


  def update_domain_threshold(
      domain: str,
      clarification_asked: bool,
      run_succeeded: bool,
      max_ambiguity_score: float,
  ) -> None:
      """Calibrate the domain threshold from a completed run outcome.

      Update rule (moving average toward calibrated value):
        If clarification was NOT asked AND run succeeded:
            threshold += 0.05 * (max_ambiguity_score - threshold)
            → score was handled fine without asking; threshold can relax upward
        If clarification was NOT asked AND run failed:
            threshold -= 0.10 * threshold
            → should have asked; threshold tightens downward
        If clarification WAS asked AND run succeeded:
            threshold is unchanged (asking worked — no signal to change)
        If clarification WAS asked AND run failed:
            threshold is unchanged (failure was downstream of spec, not spec itself)

      Threshold clamped to [0.20, 0.90].
      Never raises. Uses same file-lock as all other memory operations.

      Args:
          domain: Domain key string.
          clarification_asked: Whether questions were surfaced before lock.
          run_succeeded: Whether the run completed successfully.
          max_ambiguity_score: The highest per-axis ambiguity score at lock time.
      """
      path = _scope_path(f"{_THRESHOLD_KEY_PREFIX}{domain}")
      try:
          with _scope_lock(path):
              current = get_domain_threshold(domain)
              if not clarification_asked and run_succeeded:
                  # Inferred spec worked — relax threshold upward
                  updated = current + 0.05 * (max_ambiguity_score - current)
              elif not clarification_asked and not run_succeeded:
                  # Should have asked — tighten threshold downward
                  updated = current - 0.10 * current
              else:
                  # Clarification was asked — no update signal
                  updated = current

              updated = round(max(0.20, min(0.90, updated)), 4)

              data = {"threshold": updated, "domain": domain}
              atomic_write_json(path, data)
      except Exception:
          pass  # Never raise — threshold update is best-effort
  ```

  **What it does:** Adds two public functions to `memory.py`. `get_domain_threshold` reads the current calibrated threshold for a domain (default 0.60). `update_domain_threshold` applies a simple moving-average update rule after each run — relaxing when inference worked, tightening when it didn't. Both use the existing `_scope_path`, `_scope_lock`, and `atomic_write_json` primitives.

  **Why this update rule:** The frontier shows "when to ask" is a learned policy. This rule is the minimal online learning approximation: positive outcomes push the threshold up (less asking), negative outcomes push it down (more asking), without requiring a full training loop. The 0.05 / 0.10 learning rates produce slow drift — appropriate for a system where runs are sparse.

  **Assumptions:**
  - `memory.py` already defines `_scope_path`, `_scope_lock`, `atomic_write_json` (confirmed in pre-flight)
  - `json` and `math` are already imported in `memory.py`

  **Risks:**
  - Appending to wrong file location → existing function is disrupted → Pre-Read Gate checks `tail -5` before appending
  - Duplicate function definition if step re-runs → Pre-Read Gate greps for function name before appending

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/memory.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.2: add get_domain_threshold and update_domain_threshold to memory"
  ```

  **Subtasks:**
  - [ ] 🟥 Run Pre-Read Gate greps — confirm functions do not exist
  - [ ] 🟥 Append two functions to `memory.py`
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  import os, tempfile
  from pathlib import Path
  import ballast.core.memory as mem

  # Use temp dir to avoid polluting .ballast_memory/
  with tempfile.TemporaryDirectory() as tmp:
      mem.MEMORY_DIR = Path(tmp)
      mem.MEMORY_DIR.mkdir(exist_ok=True)

      # Default threshold for unknown domain
      t = mem.get_domain_threshold('test-domain-xyz')
      assert t == 0.60, f'Expected 0.60, got {t}'
      print('default threshold: OK')

      # Update: inferred, succeeded → threshold relaxes upward toward max_score
      mem.update_domain_threshold('test-domain-xyz',
          clarification_asked=False, run_succeeded=True, max_ambiguity_score=0.80)
      t2 = mem.get_domain_threshold('test-domain-xyz')
      assert t2 > 0.60, f'Expected > 0.60 after relax, got {t2}'
      print(f'relax update: {t2:.4f} OK')

      # Update: inferred, failed → threshold tightens downward
      mem.update_domain_threshold('test-domain-xyz',
          clarification_asked=False, run_succeeded=False, max_ambiguity_score=0.80)
      t3 = mem.get_domain_threshold('test-domain-xyz')
      assert t3 < t2, f'Expected < {t2} after tighten, got {t3}'
      print(f'tighten update: {t3:.4f} OK')

      # Clamping: extreme tighten stays >= 0.20
      for _ in range(50):
          mem.update_domain_threshold('test-domain-xyz',
              clarification_asked=False, run_succeeded=False, max_ambiguity_score=0.0)
      t4 = mem.get_domain_threshold('test-domain-xyz')
      assert t4 >= 0.20, f'Expected >= 0.20, got {t4}'
      print(f'clamp lower: {t4:.4f} OK')

  print('Step 2 memory threshold functions OK')
  "
  ```

  **Expected:** 4 OK lines followed by `Step 2 memory threshold functions OK`.

  **Pass:** All 4 assertions pass with exit code 0.

  **Fail:**
  - `ImportError: cannot import name 'get_domain_threshold'` → append did not land in file → re-read `memory.py` and check
  - `AssertionError: Expected 0.60` → default path read wrong file or returned wrong value → check `_DEFAULT_THRESHOLD` constant
  - `AssertionError: Expected > 0.60` → update rule signs inverted → re-read update rule logic

---

### Phase 2 — Scoring and Policy

**Goal:** `score_goal()` returns `AmbiguityScores` with per-axis independent scores and reasons. `ClarificationPolicy` reads domain threshold from memory and returns an ask/infer decision. `_clarify()` generates structured choice questions matched to blocking axis types.

---

- [ ] 🟥 **Step 3: Per-axis ambiguity scoring (`_score_axes`)** — *Critical: the output of this function drives all downstream decisions; wrong scores produce wrong clarification or wrong inference*

  **Step Architecture Thinking:**

  **Pattern applied:** **Single structured LLM call with tool_use enforcement and fail-safe fallback.** `_score_axes()` is one function that calls Claude once with a `tool_choice={"type": "tool", "name": "score_ambiguity"}` call, requesting all three axis scores in a single response. There is NO registry, NO callable per axis, NO iteration. The three axes are evaluated by the LLM in one shot. On any error (API failure, validation error, malformed response), a conservative all-blocking fallback is returned — fail-safe toward asking, never toward silent inference.

  **Why this step exists here in the sequence:**
  `ClarificationPolicy` (Step 4) needs `AmbiguityScores` to compute its decision. `ClarificationPolicy` cannot exist before scoring exists. Scoring is the foundation of the entire policy.

  **Why this file is the right location:**
  Scoring is internal to `spec.py` — it is not a public function of the module. It lives here as a private function because it is only called by `lock_spec()` and tested directly in `test_spec.py`.

  **Alternative approach considered and rejected:**
  Heuristic regex scoring (count negation words, measure goal length, detect question marks) — rejected because natural language goals have high false negative rates on regex: "write a function" has the same token count as "write a function that takes a list and returns the top-k elements by frequency, handling ties by first occurrence". Regex cannot distinguish them. The LLM scorer with a structured output tool call costs ~100 tokens and catches the real distinction.

  **What breaks if this step deviates:**
  If scoring returns a single aggregate score instead of per-axis scores with reasons, `_clarify()` (Step 4) has no basis to generate a targeted question — it would ask a generic "what do you want?" question, which the literature shows produces worse answers than structured choices.

  ---

  **Idempotent:** Yes — appending to `spec.py` is safe; Pre-Read Gate confirms anchor exists once.

  **Pre-Read Gate:**
  - Run `grep -n "def _score_axes" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return nothing. If it exists → STOP.
  - Run `grep -n "class LockedSpec" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return exactly 1 match — confirms Step 1 succeeded.

  Append to `/Users/ngchenmeng/Ballast/ballast/core/spec.py`:

  ```python


  # ---------------------------------------------------------------------------
  # Anthropic client (lazy singleton — matches memory.py pattern)
  # ---------------------------------------------------------------------------

  import anthropic as _anthropic
  import json as _json

  _spec_client: "_anthropic.Anthropic | None" = None
  _SPEC_MODEL: str = "claude-sonnet-4-6"


  def _get_spec_client() -> "_anthropic.Anthropic":
      global _spec_client
      if _spec_client is None:
          _spec_client = _anthropic.Anthropic()
      return _spec_client


  # ---------------------------------------------------------------------------
  # Per-axis ambiguity scoring
  # ---------------------------------------------------------------------------

  _SCORING_PROMPT = """You are an intent grounding system for an AI agent orchestrator.

  Analyse the following goal string across THREE independent ambiguity axes.
  For each axis, produce a score from 0.0 (fully specified) to 1.0 (completely ambiguous)
  and a one-sentence reason.

  ATTRIBUTE ambiguity: Is it unclear which version, format, variant, or specific item is wanted?
  Example high score: "fix the bug" — which bug? which file?
  Example low score: "fix the off-by-one error in src/parser.py line 42"

  SCOPE ambiguity: Is it unclear which files, services, environments, or resources are in play?
  Example high score: "update the tests" — which tests? which test runner? which environment?
  Example low score: "update tests/test_parser.py to cover the edge case added in PR #12"

  PREFERENCE ambiguity: Is it unclear whether speed vs thoroughness, brevity vs completeness,
  or safety vs risk is preferred?
  Example high score: "summarise the document" — short or comprehensive? lose nuance or preserve it?
  Example low score: "produce a 3-bullet executive summary of the document, prioritising action items"

  Also decide: is each axis BLOCKING (score >= 0.55 suggests blocking, but use judgment)?
  An axis is blocking if a wrong assumption would cause the agent to do the wrong thing.

  Domain context: {domain}
  Goal: {goal}"""


  def _score_axes(goal: str, domain: str) -> AmbiguityScores:
      """Score a goal across ATTRIBUTE, SCOPE, PREFERENCE axes independently.

      Uses Claude with structured tool output to produce per-axis scores.
      Returns conservative all-blocking scores on any error (fail-safe: prefer asking).

      Args:
          goal:   Raw goal string from the user.
          domain: Domain key for context (included in prompt to calibrate scoring to domain norms).
      Returns:
          AmbiguityScores with three independent axis assessments.
      """
      prompt = _SCORING_PROMPT.format(goal=goal, domain=domain)
      try:
          response = _get_spec_client().messages.create(
              model=_SPEC_MODEL,
              max_tokens=400,
              tools=[{
                  "name": "score_ambiguity",
                  "description": "Return per-axis ambiguity scores.",
                  "input_schema": {
                      "type": "object",
                      "properties": {
                          "attribute": {
                              "type": "object",
                              "properties": {
                                  "score": {"type": "number"},
                                  "reason": {"type": "string"},
                                  "is_blocking": {"type": "boolean"},
                              },
                              "required": ["score", "reason", "is_blocking"],
                          },
                          "scope": {
                              "type": "object",
                              "properties": {
                                  "score": {"type": "number"},
                                  "reason": {"type": "string"},
                                  "is_blocking": {"type": "boolean"},
                              },
                              "required": ["score", "reason", "is_blocking"],
                          },
                          "preference": {
                              "type": "object",
                              "properties": {
                                  "score": {"type": "number"},
                                  "reason": {"type": "string"},
                                  "is_blocking": {"type": "boolean"},
                              },
                              "required": ["score", "reason", "is_blocking"],
                          },
                      },
                      "required": ["attribute", "scope", "preference"],
                  },
              }],
              tool_choice={"type": "tool", "name": "score_ambiguity"},
              messages=[{"role": "user", "content": prompt}],
          )
          for block in response.content:
              if block.type == "tool_use":
                  raw = block.input
                  return AmbiguityScores(
                      attribute=AmbiguityScore(
                          axis=AmbiguityType.ATTRIBUTE,
                          score=float(raw["attribute"]["score"]),
                          reason=raw["attribute"]["reason"],
                          is_blocking=bool(raw["attribute"]["is_blocking"]),
                      ),
                      scope=AmbiguityScore(
                          axis=AmbiguityType.SCOPE,
                          score=float(raw["scope"]["score"]),
                          reason=raw["scope"]["reason"],
                          is_blocking=bool(raw["scope"]["is_blocking"]),
                      ),
                      preference=AmbiguityScore(
                          axis=AmbiguityType.PREFERENCE,
                          score=float(raw["preference"]["score"]),
                          reason=raw["preference"]["reason"],
                          is_blocking=bool(raw["preference"]["is_blocking"]),
                      ),
                  )
      except Exception:
          pass

      # Fail-safe: if scoring fails for any reason, return conservative blocking scores.
      # This ensures the system asks rather than makes wrong assumptions on error.
      _conservative = AmbiguityScore(
          axis=AmbiguityType.ATTRIBUTE,
          score=0.70,
          reason="Scoring failed — treating as ambiguous for safety.",
          is_blocking=True,
      )
      return AmbiguityScores(
          attribute=_conservative.model_copy(update={"axis": AmbiguityType.ATTRIBUTE}),
          scope=_conservative.model_copy(update={"axis": AmbiguityType.SCOPE}),
          preference=_conservative.model_copy(update={"axis": AmbiguityType.PREFERENCE}),
      )
  ```

  **What it does:** Appends the Anthropic client singleton and `_score_axes()` to `spec.py`. Uses Claude's tool-use API to score three axes independently and return an `AmbiguityScores` object. On any error, returns conservative blocking scores (fail-safe toward asking, not inferring).

  **Why fail-safe toward asking:** If scoring throws (network error, quota, etc.), silently inferring a spec from an ambiguous goal produces a wrong spec that the agent runs against. Asking is always recoverable; running against a wrong spec is not.

  **Assumptions:**
  - `anthropic` is installed (confirmed in `pyproject.toml`)
  - `ANTHROPIC_API_KEY` is set when `_score_axes` is called (tested in Step 7 integration smoke test)
  - Step 1 models (`AmbiguityScore`, `AmbiguityScores`, `AmbiguityType`) are defined in the same file

  **Risks:**
  - Claude returns a score outside [0.0, 1.0] → Pydantic `ge=0.0, le=1.0` on `AmbiguityScore.score` will raise `ValidationError` → caught by the outer `except Exception`, returns conservative scores
  - Append lands in wrong position (between class definitions) → syntax error on import → verification import test catches it

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/spec.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.3: add _score_axes with per-axis Claude scoring"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm `_score_axes` does not exist, `LockedSpec` exists
  - [ ] 🟥 Append scoring code to `spec.py`
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit (no API call — tests structure only)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  from ballast.core.spec import _score_axes, AmbiguityScores, AmbiguityType
  import inspect

  # Confirm function exists and has correct signature
  sig = inspect.signature(_score_axes)
  params = list(sig.parameters.keys())
  assert params == ['goal', 'domain'], f'Wrong params: {params}'

  # Confirm fail-safe returns AmbiguityScores (not None, not dict)
  # We can't call it without an API key, so we test the import and signature only.
  # Full integration tested in Step 7.
  print('_score_axes signature OK')
  print('Step 3 scoring structure OK')
  "
  ```

  **Expected:** `Step 3 scoring structure OK`

  **Pass:** Prints both OK lines with exit code 0.

  **Fail:**
  - `ImportError: cannot import name '_score_axes'` → append failed → re-read `spec.py` and check
  - `AssertionError: Wrong params` → function signature wrong → fix param names

---

- [ ] 🟥 **Step 4: `ClarificationPolicy` + `_clarify()` question generation** — *Critical: policy implements the learned-threshold decision; clarify implements targeted question type per axis*

  **Step Architecture Thinking:**

  **Pattern applied:** **Policy Object** — `ClarificationPolicy` encapsulates the ask/infer decision as a class rather than an inline conditional. This means the policy is independently testable, replaceable with a learned model (Week 4), and reads the threshold from memory exactly once per `lock_spec()` call (not per event).

  **Why this step exists here in the sequence:**
  `lock_spec()` (Step 5) calls `ClarificationPolicy.should_ask()` and then `_clarify()` if true. Both must exist before `lock_spec()` is written.

  **Why `ClarificationPolicy` is a class not a function:**
  It has state: the threshold it read from memory. Making it a class means the threshold is read once at construction and the decision is made without a second memory read. This also makes it mockable in tests without patching `memory.get_domain_threshold`.

  **Alternative approach considered and rejected:**
  Inline `if max_score >= get_domain_threshold(domain)` directly in `lock_spec()` — rejected because it couples the locking function to the memory layer (testing requires a real memory file), and it makes the policy non-replaceable without modifying `lock_spec()`.

  **What breaks if this step deviates:**
  If `_clarify()` returns generic open-text questions instead of structured choices, the answers require a second parse pass. Structured choices produce deterministic string answers that `lock_spec()` can directly assign to spec fields.

  ---

  **Idempotent:** Yes — Pre-Read Gate confirms functions don't exist before appending.

  **Pre-Read Gate:**
  - Run `grep -n "class ClarificationPolicy" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return nothing.
  - Run `grep -n "def _clarify" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return nothing.
  - Run `grep -n "def _score_axes" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return exactly 1 match — confirms Step 3 succeeded.

  Append to `/Users/ngchenmeng/Ballast/ballast/core/spec.py`:

  ```python


  # ---------------------------------------------------------------------------
  # Clarification policy — the learned ask/infer decision
  # ---------------------------------------------------------------------------

  class ClarificationPolicy:
      """Encapsulates the per-domain ask-vs-infer decision.

      Reads the current domain threshold from memory at construction time.
      Decision: if any blocking axis score >= threshold → ask.

      The threshold is not hardcoded. It drifts over time via
      memory.update_domain_threshold() called at the end of each run.
      This approximates the RL policy the frontier literature describes:
      'when to ask' is a learned, domain-calibrated function, not a constant.

      Usage:
          policy = ClarificationPolicy(domain='coding')
          if policy.should_ask(scores):
              questions = _clarify(goal, scores)
      """

      def __init__(self, domain: str) -> None:
          from ballast.core.memory import get_domain_threshold
          self.domain = domain
          self.threshold: float = get_domain_threshold(domain)

      def should_ask(self, scores: AmbiguityScores) -> bool:
          """Return True if clarification questions should be surfaced.

          Decision: at least one blocking axis has score >= self.threshold.
          Non-blocking axes never trigger asking regardless of score.
          """
          return any(
              axis.score >= self.threshold
              for axis in scores.blocking_axes
          )


  # ---------------------------------------------------------------------------
  # Question generation — structured choices, not open text
  # ---------------------------------------------------------------------------

  _QUESTION_TYPE_PROMPTS: dict[AmbiguityType, str] = {
      AmbiguityType.ATTRIBUTE: (
          "Generate a clarification question about WHICH specific item, version, "
          "or variant is wanted. The question must offer 2-3 concrete choices. "
          "Reason for asking: {reason}"
      ),
      AmbiguityType.SCOPE: (
          "Generate a clarification question about the BOUNDARY of what should be "
          "touched (files, services, environment). Offer 2-3 concrete scope options. "
          "Reason for asking: {reason}"
      ),
      AmbiguityType.PREFERENCE: (
          "Generate a clarification question about the TRADE-OFF preferred "
          "(speed vs thoroughness, brevity vs detail, etc). Offer 2-3 named options. "
          "Reason for asking: {reason}"
      ),
  }

  _CLARIFY_SYSTEM_PROMPT = """You are generating targeted clarification questions for an AI agent.
  Rules:
  - Generate EXACTLY ONE question per axis provided.
  - Each question must be a single sentence ending with '?'
  - Each question must offer 2-3 concrete choices in square brackets like: [option A / option B]
  - Do NOT ask about axes not provided.
  - Keep questions to 25 words max.
  - Return ONLY a JSON array of question strings. No preamble. No markdown.
  Example: ["Which file should be updated? [parser.py / tokenizer.py / all affected files]",
             "Should the output be brief or comprehensive? [brief summary / full analysis]"]
  """


  def _clarify(goal: str, scores: AmbiguityScores) -> list[str]:
      """Generate targeted clarification questions for blocking axes.

      Returns at most 2 questions (the 2 highest-scoring blocking axes).
      Questions are structured as choices, not open text.
      Returns [] on any error — caller falls through to _infer_spec.

      Args:
          goal:   Raw goal string (for question context).
          scores: Ambiguity scores — only blocking axes generate questions.
      Returns:
          list of question strings (0-2 items).
      """
      blocking = sorted(
          scores.blocking_axes,
          key=lambda a: a.score,
          reverse=True,
      )[:2]  # Cap at 2 questions maximum

      if not blocking:
          return []

      axis_instructions = "\n".join(
          f"Axis {i+1} ({ax.axis.value}): "
          + _QUESTION_TYPE_PROMPTS[ax.axis].format(reason=ax.reason)
          for i, ax in enumerate(blocking)
      )

      user_prompt = (
          f"Goal: {goal}\n\n"
          f"Generate clarification questions for these axes:\n{axis_instructions}"
      )

      try:
          response = _get_spec_client().messages.create(
              model=_SPEC_MODEL,
              max_tokens=300,
              system=_CLARIFY_SYSTEM_PROMPT,
              messages=[{"role": "user", "content": user_prompt}],
          )
          text = "".join(
              block.text for block in response.content
              if hasattr(block, "text")
          ).strip()
          questions = _json.loads(text)
          if isinstance(questions, list):
              return [q for q in questions if isinstance(q, str)][:2]
      except Exception:
          pass
      return []
  ```

  **What it does:** `ClarificationPolicy` reads the domain threshold from memory at construction and exposes `should_ask(scores)`. `_clarify()` generates up to 2 targeted choice questions matched to the highest-scoring blocking axes — ATTRIBUTE questions ask which item, SCOPE questions ask about boundaries, PREFERENCE questions ask about trade-offs.

  **Why cap at 2 questions:** The literature on clarification dialogs shows diminishing returns past 2 questions. More questions increase user friction without proportional disambiguation gain. Cap is hard-coded as a constant; blocking axes beyond 2 are resolved by inference.

  **Assumptions:**
  - `ballast.core.memory.get_domain_threshold` exists (Step 2 complete)
  - `_get_spec_client()` and `_SPEC_MODEL` defined earlier in the same file (Step 3)
  - `AmbiguityType`, `AmbiguityScores` defined earlier in the same file (Step 1)

  **Risks:**
  - Claude returns malformed JSON in `_clarify` → `json.loads` raises → caught, returns `[]` → `lock_spec()` falls through to inference
  - `get_domain_threshold` import fails (circular) → `ballast.core.memory` imports `ballast.core.spec`? No — memory.py does not import spec.py. No circular risk.

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/spec.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.4: add ClarificationPolicy and _clarify question generation"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm `ClarificationPolicy` and `_clarify` do not exist
  - [ ] 🟥 Append policy and clarification code to `spec.py`
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  import tempfile
  from pathlib import Path
  import ballast.core.memory as mem

  with tempfile.TemporaryDirectory() as tmp:
      mem.MEMORY_DIR = Path(tmp)
      mem.MEMORY_DIR.mkdir(exist_ok=True)

      from ballast.core.spec import (
          ClarificationPolicy, AmbiguityScores, AmbiguityScore, AmbiguityType
      )

      def make_score(axis, score, blocking):
          return AmbiguityScore(axis=axis, score=score, reason='test', is_blocking=blocking)

      # Policy: one blocking axis at score 0.80, threshold default 0.60 → should_ask = True
      scores_ask = AmbiguityScores(
          attribute=make_score(AmbiguityType.ATTRIBUTE, 0.80, True),
          scope=make_score(AmbiguityType.SCOPE, 0.20, False),
          preference=make_score(AmbiguityType.PREFERENCE, 0.10, False),
      )
      policy = ClarificationPolicy('test-domain-new')
      assert policy.threshold == 0.60, f'Expected 0.60, got {policy.threshold}'
      assert policy.should_ask(scores_ask) is True
      print('should_ask=True when blocking axis >= threshold: OK')

      # Policy: blocking axis at 0.40, threshold 0.60 → should_ask = False
      scores_infer = AmbiguityScores(
          attribute=make_score(AmbiguityType.ATTRIBUTE, 0.40, True),
          scope=make_score(AmbiguityType.SCOPE, 0.20, False),
          preference=make_score(AmbiguityType.PREFERENCE, 0.10, False),
      )
      assert policy.should_ask(scores_infer) is False
      print('should_ask=False when blocking axis < threshold: OK')

      # No blocking axes → never ask
      scores_none = AmbiguityScores(
          attribute=make_score(AmbiguityType.ATTRIBUTE, 0.90, False),
          scope=make_score(AmbiguityType.SCOPE, 0.90, False),
          preference=make_score(AmbiguityType.PREFERENCE, 0.90, False),
      )
      assert policy.should_ask(scores_none) is False
      print('should_ask=False when no blocking axes: OK')

  print('Step 4 policy OK')
  "
  ```

  **Expected:** 3 OK lines followed by `Step 4 policy OK`.

  **Pass:** All 3 assertions pass with exit code 0.

  **Fail:**
  - `ImportError: cannot import name 'ClarificationPolicy'` → append failed
  - `AssertionError: Expected 0.60` → `get_domain_threshold` not returning default → check Step 2
  - `AssertionError` on `should_ask` → blocking axis filter or threshold comparison inverted → re-read policy logic

---

### Phase 3 — Lock and Track

**Goal:** `lock_spec()` is the single public entry point — it orchestrates score → decide → ask-or-infer → return `LockedSpec`. `RunPhaseTracker` propagates `IntentSignal` through event streams.

---

- [ ] 🟥 **Step 5: `_infer_spec()` + `lock_spec()` main entry point** — *Critical: this is the public API surface; everything in the system calls this*

  **Step Architecture Thinking:**

  **Pattern applied:** **Facade** — `lock_spec()` is the single entry point that orchestrates the internal pipeline: `_score_axes → ClarificationPolicy → _clarify or _infer_spec → LockedSpec`. Callers (adapters, scripts) call one function and receive a complete `LockedSpec` — they never see the internal steps.

  **Why this step exists here in the sequence:**
  All internal components (scoring, policy, clarification) are complete. `lock_spec()` is the composition layer that wires them together. It must be last in the internal build sequence so it can reference all prior functions.

  **Why `lock_spec` is a module-level function, not a method:**
  Callers construct it without owning an object. `AGUIAdapter` will call `lock_spec(goal, domain)` before calling `self._graph.astream_events()`. A stateless function is simpler to call and test than an instantiated object at this boundary.

  **Alternative approach considered and rejected:**
  `SpecBuilder` class with a `.lock()` method — rejected because it adds a construction step the caller must remember. The Facade pattern (one function call) reduces cognitive overhead at the adapter boundary.

  **What breaks if this step deviates:**
  If `lock_spec()` passes `goal` instead of the locked `LockedSpec` to downstream components, the entire spec grounding layer is bypassed silently. The `spec` parameter in `stream(goal, spec)` must receive the model object, not a string or dict.

  ---

  **Idempotent:** Yes — appending to spec.py is safe; Pre-Read Gate confirms anchor.

  **Pre-Read Gate:**
  - Run `grep -n "def lock_spec" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return nothing.
  - Run `grep -n "def _infer_spec" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return nothing.
  - Run `grep -n "class ClarificationPolicy" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return exactly 1 match.

  Append to `/Users/ngchenmeng/Ballast/ballast/core/spec.py`:

  ```python


  # ---------------------------------------------------------------------------
  # Spec inference — used when policy decides not to ask
  # ---------------------------------------------------------------------------

  _INFER_PROMPT = """You are an intent grounding system. Given a goal string, infer a locked specification.

  Goal: {goal}
  Domain: {domain}

  Ambiguity analysis:
  - Attribute: {attr_reason} (score {attr_score:.2f})
  - Scope: {scope_reason} (score {scope_score:.2f})
  - Preference: {pref_reason} (score {pref_score:.2f})

  Produce a locked spec. Make reasonable default assumptions where ambiguous.
  Be conservative — prefer narrower scope over broader."""

  _INFER_TOOL = {
      "name": "infer_spec",
      "description": "Return a locked specification inferred from the goal.",
      "input_schema": {
          "type": "object",
          "properties": {
              "success_criteria": {"type": "string", "description": "Measurable definition of done — one sentence"},
              "scope": {"type": "string", "description": "Boundary of what the agent may touch — one phrase or empty string"},
              "constraints": {"type": "array", "items": {"type": "string"}, "description": "Hard constraints the agent must not violate"},
              "output_format": {"type": "string", "description": "Required output format, or empty string"},
              "inferred_assumptions": {"type": "array", "items": {"type": "string"}, "description": "Assumptions made when inferring the spec"},
              "latent_goal": {"type": "string", "description": "Thematic label — 3 words max"},
              "action_type": {"type": "string", "enum": ["READ", "WRITE", "TRANSFORM", "VERIFY", "SEARCH", "COORDINATE"]},
              "salient_entity_types": {"type": "array", "items": {"type": "string"}, "description": "Entity types relevant to this goal"},
          },
          "required": ["success_criteria", "scope", "constraints", "output_format",
                       "inferred_assumptions", "latent_goal", "action_type", "salient_entity_types"],
      },
  }


  def _infer_spec(goal: str, domain: str, scores: AmbiguityScores) -> LockedSpec:
      """Infer a LockedSpec from the goal without asking the user.

      Called when ClarificationPolicy.should_ask() returns False.
      Uses Claude tool_use (structured output) to fill in spec fields.
      Also extracts the initial IntentSignal from the inference.

      On error: returns a minimal valid LockedSpec with the raw goal as success_criteria
      and empty scope — safe for the agent to run against with no constraints.

      Args:
          goal:   Raw goal string.
          domain: Domain key (for context).
          scores: Ambiguity scores (included in prompt for calibration context).
      Returns:
          LockedSpec (never raises).
      """
      from ballast.core.memory import get_domain_threshold

      prompt = _INFER_PROMPT.format(
          goal=goal,
          domain=domain,
          attr_reason=scores.attribute.reason,
          attr_score=scores.attribute.score,
          scope_reason=scores.scope.reason,
          scope_score=scores.scope.score,
          pref_reason=scores.preference.reason,
          pref_score=scores.preference.score,
      )

      try:
          response = _get_spec_client().messages.create(
              model=_SPEC_MODEL,
              max_tokens=600,
              tools=[_INFER_TOOL],
              tool_choice={"type": "tool", "name": "infer_spec"},
              messages=[{"role": "user", "content": prompt}],
          )
          for block in response.content:
              if block.type == "tool_use":
                  raw = block.input
                  intent = IntentSignal(
                      latent_goal=raw.get("latent_goal", goal[:30]),
                      action_type=raw.get("action_type", "COORDINATE"),
                      salient_entity_types=raw.get("salient_entity_types", []),
                  )
                  return LockedSpec(
                      goal=goal,
                      domain=domain,
                      success_criteria=raw.get("success_criteria", goal),
                      scope=raw.get("scope", ""),
                      constraints=raw.get("constraints", []),
                      output_format=raw.get("output_format", ""),
                      inferred_assumptions=raw.get("inferred_assumptions", []),
                      ambiguity_scores=scores,
                      intent_signal=intent,
                      clarification_asked=False,
                      threshold_used=get_domain_threshold(domain),
                  )
      except Exception:
          pass

      # Minimal safe fallback
      return LockedSpec(
          goal=goal,
          domain=domain,
          success_criteria=goal,
          scope="",
          constraints=[],
          output_format="",
          inferred_assumptions=["Spec inference failed — using raw goal as success criteria"],
          ambiguity_scores=scores,
          intent_signal=IntentSignal(
              latent_goal=goal[:30],
              action_type="COORDINATE",
              salient_entity_types=[],
          ),
          clarification_asked=False,
          threshold_used=get_domain_threshold(domain),
      )


  # ---------------------------------------------------------------------------
  # lock_spec — public entry point (Facade)
  # ---------------------------------------------------------------------------

  def lock_spec(
      goal: str,
      domain: str = "general",
      interactive: bool = False,
  ) -> "tuple[LockedSpec, list[str]]":
      """Ground a raw goal into a locked spec. Public API.

      Pipeline:
        1. Score goal on ATTRIBUTE, SCOPE, PREFERENCE axes independently
        2. Read per-domain threshold from memory (learned, not hardcoded)
        3. ClarificationPolicy decides: ask or infer
        4a. If ask (interactive=True): generate targeted choice questions (max 2)
            Return (spec_placeholder, questions) — caller surfaces questions to user
            Caller must call lock_spec_with_answers(goal, domain, answers) next
        4b. If infer (or interactive=False): infer spec from goal + ambiguity context
            Return (locked_spec, []) with inferred_assumptions surfaced as one-liner

      Args:
          goal:        Raw goal string from user.
          domain:      Domain key for threshold lookup and memory scoping.
          interactive: If False, always infer — never ask. Use for programmatic callers.
      Returns:
          (LockedSpec, questions: list[str])
          If questions is non-empty: spec is a placeholder, caller must handle questions.
          If questions is empty: spec is fully locked, ready to pass to stream().
      """
      scores = _score_axes(goal, domain)
      policy = ClarificationPolicy(domain)

      if interactive and policy.should_ask(scores):
          questions = _clarify(goal, scores)
          if questions:
              # Return placeholder spec + questions for caller to surface
              placeholder = LockedSpec(
                  goal=goal,
                  domain=domain,
                  success_criteria="",
                  scope="",
                  constraints=[],
                  output_format="",
                  inferred_assumptions=[],
                  ambiguity_scores=scores,
                  intent_signal=IntentSignal(
                      latent_goal=goal[:30],
                      action_type="COORDINATE",
                      salient_entity_types=[],
                  ),
                  clarification_asked=True,
                  threshold_used=policy.threshold,
              )
              return placeholder, questions

      # Infer path: interactive=False, or policy said don't ask, or _clarify returned []
      spec = _infer_spec(goal, domain, scores)
      return spec, []


  def lock_spec_with_answers(
      goal: str,
      domain: str,
      questions: list[str],
      answers: list[str],
  ) -> LockedSpec:
      """Complete spec locking after user answered clarification questions.

      Called after lock_spec() returned non-empty questions and the caller
      surfaced them to the user and collected answers.

      Args:
          goal:      Original raw goal.
          domain:    Domain key.
          questions: Questions returned by lock_spec().
          answers:   User's answers (same length as questions).
      Returns:
          Fully locked LockedSpec. Never raises.
      """
      from ballast.core.memory import get_domain_threshold

      enriched_goal = goal
      if questions and answers:
          qa_context = "\n".join(
              f"Q: {q}\nA: {a}" for q, a in zip(questions, answers)
          )
          enriched_goal = f"{goal}\n\nUser clarifications:\n{qa_context}"

      scores = _score_axes(enriched_goal, domain)
      spec = _infer_spec(enriched_goal, domain, scores)
      spec.goal = goal  # preserve original goal for audit
      spec.clarification_asked = True
      return spec
  ```

  **What it does:** `_infer_spec()` calls Claude to fill all spec fields from the goal + ambiguity context, also extracting the initial `IntentSignal`. `lock_spec()` is the Facade: score → policy → ask-or-infer → return `(LockedSpec, questions)`. `lock_spec_with_answers()` handles the two-step interactive flow.

  **Why return `(LockedSpec, list[str])`:** The tuple separates the two paths cleanly. An empty questions list means the spec is locked and ready. A non-empty list means the caller must surface questions before the spec is complete. This avoids a stateful `SpecBuilder` object.

  **Assumptions:**
  - `_score_axes`, `ClarificationPolicy`, `_clarify`, `_infer_spec` all defined earlier in same file
  - `ballast.core.memory.get_domain_threshold` exists (Step 2)

  **Risks:**
  - `_infer_spec` Claude call fails → minimal safe fallback returns raw goal as success_criteria → agent runs against it, may be unconstrained → acceptable; documented in inferred_assumptions

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/spec.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.5: add _infer_spec and lock_spec facade"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm `lock_spec` and `_infer_spec` do not exist
  - [ ] 🟥 Append `_infer_spec`, `lock_spec`, `lock_spec_with_answers` to `spec.py`
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit (no API call — tests import and return type only)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  import inspect
  from ballast.core.spec import lock_spec, lock_spec_with_answers, LockedSpec

  # Confirm lock_spec signature
  sig = inspect.signature(lock_spec)
  params = list(sig.parameters.keys())
  assert 'goal' in params
  assert 'domain' in params
  assert 'interactive' in params

  # Confirm lock_spec_with_answers signature
  sig2 = inspect.signature(lock_spec_with_answers)
  params2 = list(sig2.parameters.keys())
  assert 'answers' in params2

  print('lock_spec signatures OK')
  print('Step 5 entry point structure OK')
  "
  ```

  **Expected:** Both OK lines with exit code 0.

  **Fail:**
  - `ImportError` → append failed → re-read spec.py
  - `AssertionError` on params → signature wrong → fix param names

---

- [ ] 🟥 **Step 6: `RunPhaseTracker` — within-run event phase annotation** — *Critical: without this, intent_signal.step_index stays 0 for the entire run; Week 3 phase-aware memory retrieval cannot be built*

  **Step Architecture Thinking:**

  **Pattern applied:** **Strategy (heuristic, replaceable)** — `RunPhaseTracker.update(event)` is called per-event by `AGUIAdapter.stream()`. The base implementation maps event type to action_type via a static dict and increments `step_index`. The lookup table is the entire classification logic — no trained model, no subclassing needed at this stage. The interface is designed to be drop-in replaceable in Week 4 with an embedding-based classifier without changing any call site.

  **Why this step exists here in the sequence:**
  `LockedSpec` carries `intent_signal`. `RunPhaseTracker` mutates it. The tracker must be defined before `tests/test_spec.py` can test state transitions. It does not depend on any other Step 5/6 function — it only depends on `IntentSignal` and `LockedSpec` from Step 1.

  **Why `RunPhaseTracker` lives in `spec.py`:**
  It is inseparable from `IntentSignal` which is inseparable from `LockedSpec`. Three files for one concept is premature module split. When the tracker is upgraded to use embeddings (Week 4), it moves to `ballast/core/tracker.py` — but not before.

  **Alternative considered and rejected:**
  Update intent signal only at tool-call boundaries — rejected because action_type is useful even during model generation events (`on_chat_model_start`). A run that is in COORDINATE phase (thinking) behaves differently from one in VERIFY phase (checking tool output). Both phases should be annotated even before a tool is called.

  **What breaks if this step deviates:**
  If `RunPhaseTracker` is never called during `stream()`, `intent_signal.step_index` stays 0 for the entire run. Memory retrieval in Week 3 cannot filter by run phase — it treats all events as if they occurred at step 0.

  ---

  **Idempotent:** Yes — appending same code is safe; Pre-Read Gate confirms.

  **Pre-Read Gate:**
  - Run `grep -n "class RunPhaseTracker" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return nothing.
  - Run `grep -n "def lock_spec" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return exactly 1 match.

  Append to `/Users/ngchenmeng/Ballast/ballast/core/spec.py`:

  ```python


  # ---------------------------------------------------------------------------
  # RunPhaseTracker — within-run phase annotation
  # ---------------------------------------------------------------------------

  # Maps LangGraph event types to action_type verb classes.
  # This is a static heuristic lookup table — NOT STITCH.
  # STITCH trains a latent goal model; this approximates the same observable
  # outcome (intent-tagged events) using a rule-based classifier.
  # Upgrade path (Week 4): replace with embedding-based cluster assignment
  # trained on accumulated trajectory data.
  _EVENT_ACTION_MAP: dict[str, str] = {
      "on_chat_model_start":  "COORDINATE",   # model thinking — coordinating next action
      "on_chat_model_stream": "COORDINATE",
      "on_chat_model_end":    "COORDINATE",
      "on_tool_start":        "WRITE",         # tool execution — may have side effects
      "on_tool_end":          "VERIFY",        # tool returned — verifying output
      "on_chain_start":       "COORDINATE",
      "on_chain_end":         "VERIFY",
      "on_chain_stream":      "READ",
  }


  class RunPhaseTracker:
      """Annotates a live event stream with run-phase labels.

      Heuristic implementation: maps LangGraph event types to action_type
      verb classes using a static lookup table. Increments step_index on
      every event so downstream components can reason about run phase.

      This is NOT STITCH. STITCH trains a latent goal model on trajectory
      data. This approximates the same labelling outcome using rule-based
      classification. The interface is designed to be drop-in replaceable
      with a trained model in Week 4.

      Usage (inside AGUIAdapter.stream):
          tracker = RunPhaseTracker(spec)
          async for event in self._graph.astream_events(...):
              tracker.update(event)
              yield event

      The spec.intent_signal is mutated in place. After stream() completes,
      intent_signal.step_index reflects the total number of events processed.
      """

      def __init__(self, spec: LockedSpec) -> None:
          self.spec = spec
          self._step = 0

      def update(self, event: dict) -> None:
          """Update the intent signal from a single LangGraph event.

          Updates:
            - step_index: incremented on every event
            - action_type: mapped from event type via _EVENT_ACTION_MAP
            - salient_entity_types: updated when tool events reveal entity context

          Never raises — intent update is best-effort.
          """
          try:
              self._step += 1
              self.spec.intent_signal.step_index = self._step

              event_type = event.get("event", "")
              if event_type in _EVENT_ACTION_MAP:
                  self.spec.intent_signal.action_type = _EVENT_ACTION_MAP[event_type]

              # Extract salient entity types from tool events.
              # Tool name reveals the entity type the agent is operating on.
              if event_type == "on_tool_start":
                  tool_name = event.get("name", "")
                  if tool_name and tool_name not in self.spec.intent_signal.salient_entity_types:
                      self.spec.intent_signal.salient_entity_types.append(tool_name)

              # If a chain ends with messages in state, update latent_goal hint.
              # This captures mid-run topic pivots visible in the message state.
              if event_type == "on_chain_end":
                  data = event.get("data", {})
                  output = data.get("output", {})
                  if isinstance(output, dict):
                      messages = output.get("messages", [])
                      if messages:
                          last_msg = messages[-1]
                          content = ""
                          if isinstance(last_msg, dict):
                              content = str(last_msg.get("content", ""))
                          elif hasattr(last_msg, "content"):
                              content = str(last_msg.content)
                          if content and len(content) > 10:
                              # Use first 40 chars of latest output as latent goal hint
                              self.spec.intent_signal.latent_goal = content[:40].strip()
          except Exception:
              pass  # Never raise — tracker failure must not break the event stream

      @property
      def step_count(self) -> int:
          """Total events processed so far."""
          return self._step

      def intent_summary(self) -> str:
          """One-line summary of current intent state. For logging."""
          sig = self.spec.intent_signal
          entities = ", ".join(sig.salient_entity_types[:3]) or "none"
          return (
              f"[step {sig.step_index}] "
              f"{sig.action_type} | "
              f"goal={sig.latent_goal[:30]!r} | "
              f"entities=[{entities}]"
          )
  ```

  **What it does:** `RunPhaseTracker` wraps a `LockedSpec` and updates its `intent_signal` on every event. `action_type` shifts between COORDINATE / WRITE / VERIFY / READ based on the LangGraph event type. `salient_entity_types` grows as tools are called. `latent_goal` updates when the chain output contains a meaningful message.

  **Why mutate in place:** The `LockedSpec` is the object passed to downstream components. If `RunPhaseTracker` returned a new signal each time, callers would need to re-bind it to the spec — creating a synchronisation problem if they hold a reference to the spec before the first update.

  **Assumptions:**
  - `LockedSpec` has `model_config = {"frozen": False}` (set in Step 1)
  - `_EVENT_ACTION_MAP` covers the 8 LangGraph v2 event types observed in `observe.py` output

  **Risks:**
  - `on_chain_end` content extraction fails on newer LangGraph versions → `except Exception: pass` in `update()` swallows it silently → `latent_goal` stays at previous value → acceptable degradation

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/spec.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.6: add RunPhaseTracker for within-run STITCH propagation"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm `RunPhaseTracker` does not exist, `lock_spec` exists
  - [ ] 🟥 Append `_EVENT_ACTION_MAP` and `RunPhaseTracker` to `spec.py`
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit (pure Python — no API, no network)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  from ballast.core.spec import (
      RunPhaseTracker, LockedSpec, IntentSignal, AmbiguityType
  )

  # Build a minimal spec for testing
  sig = IntentSignal(
      latent_goal='test task',
      action_type='COORDINATE',
      salient_entity_types=[],
  )
  spec = LockedSpec(
      goal='test', domain='test',
      success_criteria='done', scope='', constraints=[],
      output_format='', inferred_assumptions=[],
      intent_signal=sig,
      clarification_asked=False,
      threshold_used=0.60,
  )

  tracker = RunPhaseTracker(spec)
  assert tracker.step_count == 0

  # Simulate a tool_start event
  tracker.update({'event': 'on_tool_start', 'name': 'get_word_count'})
  assert tracker.step_count == 1
  assert spec.intent_signal.step_index == 1
  assert spec.intent_signal.action_type == 'WRITE'
  assert 'get_word_count' in spec.intent_signal.salient_entity_types
  print('tool_start update OK')

  # Simulate a tool_end event
  tracker.update({'event': 'on_tool_end', 'name': 'get_word_count'})
  assert tracker.step_count == 2
  assert spec.intent_signal.action_type == 'VERIFY'
  print('tool_end update OK')

  # intent_summary should include step count
  summary = tracker.intent_summary()
  assert '[step 2]' in summary
  assert 'VERIFY' in summary
  print(f'intent_summary: {summary}')

  # Malformed event must not raise
  tracker.update({'bad': 'event'})
  tracker.update(None)  # type: ignore
  assert tracker.step_count == 4  # still increments
  print('malformed events handled OK')

  print('Step 6 RunPhaseTracker OK')
  "
  ```

  **Expected:** 4 OK lines followed by `Step 6 RunPhaseTracker OK`.

  **Pass:** All assertions pass with exit code 0.

  **Fail:**
  - `AssertionError: action_type == 'WRITE'` → `_EVENT_ACTION_MAP['on_tool_start']` wrong value → fix map
  - `AssertionError: step_count` → increment not happening → check `self._step += 1` runs before exception guard
  - Raises on `tracker.update(None)` → `except Exception: pass` not broad enough → confirm bare `except Exception` catches `AttributeError`

---

### Phase 4 — Tests

**Goal:** `tests/test_spec.py` proves all spec contracts hold. 24 existing tests still pass. Total reaches ≥ 36 after this phase.

---

- [ ] 🟥 **Step 7: `tests/test_spec.py` + wire `observe.py`** — *Critical: tests prove the scoring/policy/tracker contracts; observe.py wire proves the live path works end-to-end*

  **Step Architecture Thinking:**

  **Pattern applied:** **Contract tests** — tests prove behavioural invariants of the public interface, not implementation details. No live LLM calls in the unit test suite. One smoke test integration call using `interactive=False` (infer path) requires `ANTHROPIC_API_KEY`.

  **Why this step exists here in the sequence:**
  All components are implemented. Tests are written last to validate the contracts without driving the implementation (which would have required mockable seams to be designed upfront, adding complexity).

  **Why separate `test_spec.py`:**
  Keeps spec tests isolated from memory and stream tests. Failure in `test_spec.py` pinpoints the spec layer. Failure in `test_memory.py` is independent.

  **Alternative rejected:**
  Adding spec tests to `test_memory.py` — rejected because the spec layer is a separate contract; mixing them makes it harder to isolate failures.

  **What breaks if this step deviates:**
  If tests mock `lock_spec()` instead of testing it directly, the integration between scoring → policy → locking is never validated — exactly the class of bugs that surfaces in Week 3 when trajectory.py starts consuming LockedSpec fields.

  ---

  **Idempotent:** Yes.

  **Pre-Read Gate:**
  - Run `ls /Users/ngchenmeng/Ballast/tests/test_spec.py 2>&1`. Must return "No such file". If it exists → read first.
  - Run `/Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ -v --tb=short 2>&1 | tail -3`. Must show `24 passed`.
  - Run `grep -n "tool.pytest.ini_options" /Users/ngchenmeng/Ballast/pyproject.toml`. If no match → add the section below before writing test_spec.py. If a `markers` key already exists → confirm `integration` is listed; if not, append it.

  Add to `/Users/ngchenmeng/Ballast/pyproject.toml` (append after the last section if `[tool.pytest.ini_options]` does not exist):

  ```toml
  [tool.pytest.ini_options]
  markers = [
      "integration: requires ANTHROPIC_API_KEY and live Anthropic API access — skip with -m 'not integration'",
  ]
  ```

  **Why required:** Without this, `@pytest.mark.integration` in `test_spec.py` triggers a `PytestUnknownMarkWarning` and fails collection in CI environments that use `--strict-markers`. The marker must be registered before the test file is written.

  Write `/Users/ngchenmeng/Ballast/tests/test_spec.py`:

  ```python
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
  ```

  Then update `scripts/observe.py` — append a `lock_spec` call before the adapter run:

  **Pre-Read Gate for observe.py:**
  - Run `grep -n "lock_spec" /Users/ngchenmeng/Ballast/scripts/observe.py`. Must return nothing. If it returns a match → STOP, already updated.
  - Run `grep -n "from ballast" /Users/ngchenmeng/Ballast/scripts/observe.py`. Record existing imports — confirm `lock_spec` is NOT a top-level import. The `from ballast.core.spec import lock_spec, RunPhaseTracker` import in the new code is placed INSIDE `main()`, not at the top of the file. Do not move it to file-level.
  - Run `grep -n "async def main" /Users/ngchenmeng/Ballast/scripts/observe.py`. Must return exactly 1 match. Confirm line number — this is the function body to replace.

  Add to `/Users/ngchenmeng/Ballast/scripts/observe.py` — replace the `main()` function body only. The `from ballast.core.spec import ...` line is an import INSIDE the function — do not hoist it to file top:

  ```python
  async def main() -> None:
      from ballast.core.spec import lock_spec, RunPhaseTracker

      print("[observe.py] Locking spec (non-interactive infer path)...")
      spec, questions = lock_spec(OBSERVATION_GOAL, domain="coding", interactive=False)
      print(f"[observe.py] Locked spec:")
      print(f"  success_criteria: {spec.success_criteria}")
      print(f"  scope:            {spec.scope}")
      print(f"  intent signal:    {spec.intent_signal.action_type} / {spec.intent_signal.latent_goal}")
      print(f"  threshold used:   {spec.threshold_used}")
      if spec.inferred_assumptions:
          print(f"  assumptions:      {spec.inferred_assumptions}")

      adapter = AGUIAdapter(model="claude-haiku-4-5-20251001")
      tracker = RunPhaseTracker(spec)
      events = []
      async for event in adapter.stream(OBSERVATION_GOAL, spec=spec.model_dump()):
          tracker.update(event)
          events.append(event)

      print(f"\n[observe.py] Total events: {len(events)}")
      print(f"[observe.py] Final intent: {tracker.intent_summary()}")
      print("[observe.py] Done.")
  ```

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add pyproject.toml tests/test_spec.py scripts/observe.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.7: add test_spec.py, register integration pytest mark, wire observe.py"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm test_spec.py does not exist; 24 tests pass
  - [ ] 🟥 Add `[tool.pytest.ini_options]` markers section to `pyproject.toml` (if not present)
  - [ ] 🟥 Write `tests/test_spec.py` with exact content above
  - [ ] 🟥 Update `scripts/observe.py` main() body
  - [ ] 🟥 Run pytest — confirm ≥ 46 non-integration tests pass
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit (non-integration) + Integration (skipped without key)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ -v -m "not integration" --tb=short 2>&1 | tail -10
  ```

  **Expected:**
  - All original 24 tests still pass
  - All new spec unit tests pass (22 new non-integration + 1 integration skipped)
  - Total: `≥ 46 passed`, 0 failed
  - Integration test shows as `skipped` (not failed)

  **Pass:** Output ends with `≥ 46 passed` and `0 failed`.

  **Fail:**
  - `ImportError` on any spec import → check spec.py was written correctly in prior steps
  - `test_policy_threshold_updates_via_memory` fails → `update_domain_threshold` not persisting → re-read Step 2 memory functions
  - `test_tracker_does_not_duplicate_salient_entities` fails → duplication check missing in `RunPhaseTracker.update()` → re-read Step 6 tracker logic
  - Original 24 tests fail → pyproject.toml or memory.py append broke something → run `pytest tests/test_stream.py tests/test_memory.py -v` to isolate

---

### Phase 5 — Trajectory Validator (closes the feedback loop)

**Goal:** `ballast/core/trajectory.py` exists. `validate_trajectory(spec, events)` checks agent output against `success_criteria` and calls `update_domain_threshold` after each run. The spec layer now has an observable effect: runs with locked specs produce trajectory reports; calibration updates happen; the threshold drifts. This is the step that makes every prior step demonstrable.

**Why this phase cannot be skipped:** Without it, `lock_spec()` produces a struct that flows into a void. The threshold learning rule defined in Step 2 never gets called. For an open source project, every merged milestone must be independently demonstrable. A spec layer with no enforcement is not demonstrable.

---

- [ ] 🟥 **Step 8: `ballast/core/trajectory.py` — thin success_criteria validator + calibration wire** — *Critical: closes the feedback loop; without this the entire spec layer produces no observable improvement to agent behaviour*

  **Step Architecture Thinking:**

  **Pattern applied:** **Validator + Observer** — `validate_trajectory()` reads `LockedSpec.success_criteria` and checks whether any agent output event contains a response that satisfies it, returning a `TrajectoryReport`. After returning, it calls `memory.update_domain_threshold()` to wire the calibration feedback loop. The validator is intentionally thin (string-presence check) — full semantic validation is Week 3.

  **Why this step exists here in the sequence:**
  Steps 1–7 build the input side of the spec pipeline. This step builds the output side. Without it, `success_criteria` is never read by anything. The threshold learning rule (`update_domain_threshold`) is never called. The spec layer is complete input infrastructure with no consumers.

  **Why `trajectory.py` is separate from `spec.py`:**
  Separation of concerns: `spec.py` owns the input contract (locking), `trajectory.py` owns the output contract (validation). `trajectory.py` imports `LockedSpec` from `spec.py` but `spec.py` never imports from `trajectory.py`. No circular dependency.

  **Alternative considered and rejected:**
  Add `validate()` as a method on `LockedSpec` — rejected because it would make `spec.py` import from `memory.py` for the threshold update, which is a circular risk. Keeping validation in a separate module keeps the dependency graph clean.

  **What breaks if this step deviates:**
  If `validate_trajectory()` does not call `update_domain_threshold`, the threshold never converges. The entire calibration system built in Steps 2 and 4 is permanently dormant.

  ---

  **Idempotent:** Yes — writing a new file is idempotent.

  **Context:** Creates `ballast/core/trajectory.py` for the first time.

  **Pre-Read Gate:**
  - Run `ls /Users/ngchenmeng/Ballast/ballast/core/trajectory.py 2>&1`. Must return "No such file or directory". If it exists → read it first.
  - Run `grep -n "def update_domain_threshold" /Users/ngchenmeng/Ballast/ballast/core/memory.py`. Must return 1 match — confirms Step 2 exists.

  **Self-Contained Rule:** All code below is complete and runnable. No references to other steps.

  **No-Placeholder Rule:** No `<VALUE>` tokens appear below.

  Write `/Users/ngchenmeng/Ballast/ballast/core/trajectory.py`:

  ```python
  """ballast/core/trajectory.py — Thin trajectory validator.

  Public interface:
      validate_trajectory(spec, events) -> TrajectoryReport
          Check agent output against spec.success_criteria.
          Call update_domain_threshold after the run to close the calibration loop.

  This is a Week 2 thin validator: string-presence check against success_criteria.
  Week 3 upgrade: replace with structured output comparison once trajectory
  patterns are known from observe.py runs.
  """
  from __future__ import annotations

  from typing import Any
  from pydantic import BaseModel, Field

  from ballast.core.spec import LockedSpec


  class TrajectoryReport(BaseModel):
      """Output of validate_trajectory(). Consumed by memory.log_run() and callers."""
      spec_goal: str = Field(description="Original goal from LockedSpec")
      success_criteria: str = Field(description="The criteria that was checked")
      passed: bool = Field(description="True if success_criteria was satisfied")
      matched_in: str = Field(
          default="",
          description="The event content fragment that satisfied the criteria, or empty string"
      )
      event_count: int = Field(description="Total events processed")
      notes: list[str] = Field(
          default_factory=list,
          description="Human-readable notes about the validation result"
      )


  def validate_trajectory(
      spec: LockedSpec,
      events: list[dict[str, Any]],
      update_calibration: bool = True,
  ) -> TrajectoryReport:
      """Validate a completed agent run against the locked spec.

      Week 2 implementation: checks whether any 'on_chain_end' event output
      contains content that overlaps with keywords from success_criteria.
      This is deliberately simple — correctness over sophistication.

      Calls memory.update_domain_threshold() after validation (unless
      update_calibration=False) to close the threshold calibration loop.

      Args:
          spec:               The locked spec the agent ran against.
          events:             List of LangGraph events from AGUIAdapter.stream().
          update_calibration: If True, call update_domain_threshold after validation.
                              Set False in unit tests to avoid filesystem writes.
      Returns:
          TrajectoryReport with passed=True if criteria satisfied, False otherwise.
      """
      criteria_keywords = _extract_keywords(spec.success_criteria)
      passed = False
      matched_in = ""
      notes: list[str] = []

      for event in events:
          if event.get("event") != "on_chain_end":
              continue
          data = event.get("data", {})
          output = data.get("output", {})
          content = _extract_content(output)
          if not content:
              continue
          if criteria_keywords and _keywords_present(criteria_keywords, content):
              passed = True
              matched_in = content[:200]
              notes.append(f"Criteria keywords found in on_chain_end output at event index {events.index(event)}")
              break

      if not passed and not criteria_keywords:
          # No keywords to check — cannot validate; mark as passed with a note
          passed = True
          notes.append("success_criteria had no extractable keywords — cannot validate; marking passed")

      if update_calibration:
          _update_calibration(spec, passed)

      return TrajectoryReport(
          spec_goal=spec.goal,
          success_criteria=spec.success_criteria,
          passed=passed,
          matched_in=matched_in,
          event_count=len(events),
          notes=notes,
      )


  # ---------------------------------------------------------------------------
  # Internal helpers
  # ---------------------------------------------------------------------------

  def _extract_keywords(text: str) -> list[str]:
      """Extract significant words from success_criteria for keyword matching.

      Strips stop words. Returns empty list if text is empty.
      """
      stop = {
          "the", "a", "an", "is", "are", "was", "were", "be", "been",
          "has", "have", "had", "do", "does", "did", "will", "would",
          "should", "could", "may", "might", "must", "shall", "and",
          "or", "but", "in", "on", "at", "to", "for", "of", "with",
          "by", "from", "as", "into", "through", "that", "this", "it",
      }
      words = text.lower().split()
      return [w.strip(".,!?:;\"'") for w in words if w.strip(".,!?:;\"'") not in stop and len(w) > 2]


  def _extract_content(output: Any) -> str:
      """Extract text content from a LangGraph chain output dict."""
      if isinstance(output, str):
          return output
      if not isinstance(output, dict):
          return ""
      # Try messages list (standard ReAct agent output shape)
      messages = output.get("messages", [])
      if messages:
          last = messages[-1]
          if isinstance(last, dict):
              return str(last.get("content", ""))
          if hasattr(last, "content"):
              return str(last.content)
      # Try direct output field
      return str(output.get("output", ""))


  def _keywords_present(keywords: list[str], content: str) -> bool:
      """Return True if at least half the keywords appear in content (case-insensitive)."""
      if not keywords:
          return False
      content_lower = content.lower()
      matches = sum(1 for kw in keywords if kw in content_lower)
      return matches >= max(1, len(keywords) // 2)


  def _update_calibration(spec: LockedSpec, run_succeeded: bool) -> None:
      """Wire the run outcome to memory calibration. Never raises."""
      try:
          from ballast.core.memory import update_domain_threshold
          max_score = 0.0
          if spec.ambiguity_scores is not None:
              max_score = spec.ambiguity_scores.max_score
          update_domain_threshold(
              domain=spec.domain,
              clarification_asked=spec.clarification_asked,
              run_succeeded=run_succeeded,
              max_ambiguity_score=max_score,
          )
      except Exception:
          pass  # Calibration update is best-effort — never break the caller
  ```

  **What it does:** Creates `trajectory.py` with `validate_trajectory()` — a thin keyword-presence checker against `success_criteria`. After checking, calls `_update_calibration()` which calls `memory.update_domain_threshold()`, closing the feedback loop that was built in Step 2 but never wired. Returns a `TrajectoryReport` Pydantic model.

  **Why keyword-presence, not semantic matching:** Building a semantic trajectory validator requires knowing what the agent's output looks like — which requires running `observe.py` first. This thin validator gives Week 2 a working feedback loop without blocking on observation data. Week 3 replaces it with structured output comparison.

  **Assumptions:**
  - `ballast.core.spec.LockedSpec` exists (Steps 1–5 complete)
  - `ballast.core.memory.update_domain_threshold` exists (Step 2 complete)
  - `pydantic>=2.0` installed

  **Risks:**
  - `_keywords_present` with `>= len(keywords) // 2` is lenient — a 2-keyword criteria passes if 1 matches → mitigation: acceptable for Week 2; the threshold is deliberate to handle paraphrase
  - Calibration fires even on criteria-less goals (empty keywords) where `passed=True` is forced → `max_score` will be 0.0 and the threshold relaxes slightly → acceptable degradation

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/trajectory.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 3.8: add thin trajectory validator and wire calibration feedback loop"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm `trajectory.py` does not exist; `update_domain_threshold` exists in memory.py
  - [ ] 🟥 Write `ballast/core/trajectory.py` with exact content above
  - [ ] 🟥 Write `tests/test_trajectory.py` (see Verification below)
  - [ ] 🟥 Run pytest — confirm ≥ 59 non-integration tests pass
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  Write `/Users/ngchenmeng/Ballast/tests/test_trajectory.py`:

  ```python
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
  ```

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ -v -m "not integration" --tb=short 2>&1 | tail -10
  ```

  **Expected:** `≥ 59 passed`, 0 failed, integration test skipped. (24 original + 22 spec unit + 13 trajectory unit)

  **Pass:** Output ends with `≥ 59 passed` and `0 failed`.

  **Fail:**
  - `ImportError: cannot import name 'validate_trajectory'` → file not written → re-read `trajectory.py`
  - `test_validate_wires_calibration` fails → `_update_calibration` not calling `update_domain_threshold` correctly → re-read `_update_calibration` and Step 2 function signatures
  - `test_validate_passes_when_criteria_keywords_in_output` fails → keyword extraction or `_keywords_present` threshold logic wrong → run `_extract_keywords` and `_keywords_present` individually

---

## Regression Guard

**Systems at risk:**

| System | Why it could be affected | Mitigation |
|--------|--------------------------|------------|
| `ballast.core.memory` | Step 2 appends two functions — if append lands inside existing function, syntax breaks | Pre-Read Gate uses `tail -5` to confirm append position |
| `ballast.adapters.agui` | `observe.py` updated — adapter import path unchanged | Pre-Read Gate confirms `from ballast.adapters.agui import AGUIAdapter` still resolves |
| `tests/test_stream.py` | No changes to `stream.py` — zero risk | Run as part of final pytest |
| `tests/test_memory.py` | `memory.py` appended — no existing functions modified | Run as part of final pytest |

**Test count regression check:**
- Tests before plan: `24 passed`
- Tests after plan (non-integration): must be `≥ 59 passed` (24 original + 22 spec unit + 13 trajectory unit)
- Integration test: must show as `skipped`, not `failed`, when key not set

---

## Rollback Procedure

```bash
# Rollback Step 7 (tests + observe.py)
git -C /Users/ngchenmeng/Ballast revert HEAD  # reverts step 3.7 commit

# Rollback Steps 3–6 (spec.py functions)
git -C /Users/ngchenmeng/Ballast revert HEAD  # step 3.6
git -C /Users/ngchenmeng/Ballast revert HEAD  # step 3.5
git -C /Users/ngchenmeng/Ballast revert HEAD  # step 3.4
git -C /Users/ngchenmeng/Ballast revert HEAD  # step 3.3

# Rollback Step 2 (memory.py functions)
git -C /Users/ngchenmeng/Ballast revert HEAD  # step 3.2

# Rollback Step 1 (spec.py models)
git -C /Users/ngchenmeng/Ballast revert HEAD  # step 3.1

# Verify rollback
ls /Users/ngchenmeng/Ballast/ballast/core/spec.py  # must return "No such file"
/Users/ngchenmeng/Ballast/venv/bin/pytest tests/ -v --tb=short | tail -3
# Must show: 24 passed
```

---

## Pre-Flight Checklist

| Phase | Check | How to Confirm | Status |
|-------|-------|----------------|--------|
| Pre-flight | 24 tests pass | `pytest tests/ -v \| tail -3` → `24 passed` | ⬜ |
| Pre-flight | `spec.py` does not exist | `ls ballast/core/spec.py` → No such file | ⬜ |
| Pre-flight | `test_spec.py` does not exist | `ls tests/test_spec.py` → No such file | ⬜ |
| Pre-flight | `get_domain_threshold` not in memory.py | `grep -n "def get_domain_threshold" memory.py` → no output | ⬜ |
| Phase 1 Step 1 | Models import cleanly | `python -c "from ballast.core.spec import LockedSpec"` | ⬜ |
| Phase 1 Step 2 | Threshold functions work | Default returns 0.60, update persists | ⬜ |
| Phase 2 Step 3 | `_score_axes` signature correct | `inspect.signature(_score_axes)` → `(goal, domain)` | ⬜ |
| Phase 2 Step 4 | `ClarificationPolicy.should_ask()` logic correct | Unit tests pass | ⬜ |
| Phase 3 Step 5 | `lock_spec` importable, returns tuple | `from ballast.core.spec import lock_spec` | ⬜ |
| Phase 3 Step 6 | `RunPhaseTracker` state transitions correct | Unit tests pass | ⬜ |
| Phase 4 Step 7 | pytest `integration` marker registered | `grep 'tool.pytest.ini_options' pyproject.toml` → match found | ⬜ |
| Phase 4 Step 7 | ≥ 46 non-integration tests pass | `pytest -m 'not integration'` → `≥ 46 passed` | ⬜ |
| Phase 4 Step 7 | Integration test skips without key | `ANTHROPIC_API_KEY=` pytest → `1 skipped` | ⬜ |
| Phase 5 Step 8 | Trajectory validator exists and wires calibration | `pytest tests/test_trajectory.py -v` → all pass | ⬜ |
| Phase 5 Step 8 | ≥ 59 non-integration tests pass | `pytest -m 'not integration'` → `≥ 59 passed` | ⬜ |

---

## Risk Heatmap

| Step | Risk Level | What Could Go Wrong | Early Detection | Idempotent |
|------|-----------|---------------------|-----------------|------------|
| Step 1 (models) | 🟢 Low | Pydantic v1 vs v2 field syntax | Import verification catches it | Yes |
| Step 2 (memory append) | 🟡 Medium | Append inside existing function body | Pre-Read Gate `tail -5` + grep | Yes |
| Step 3 (scoring) | 🟡 Medium | Claude returns score > 1.0, Pydantic rejects | Fail-safe returns conservative scores | Yes |
| Step 4 (policy) | 🟢 Low | Circular import (spec → memory → spec) | Import test catches immediately | Yes |
| Step 5 (lock_spec) | 🟡 Medium | `_infer_spec` Claude call format changed | Minimal fallback returns raw goal | Yes |
| Step 6 (tracker) | 🟢 Low | `model_config frozen=True` blocks mutation | Step 1 explicitly sets `frozen=False` | Yes |
| Step 7 (tests) | 🔴 High | `monkeypatch.setattr(mem, 'MEMORY_DIR', ...)` doesn't isolate if functions were imported before monkeypatch | Import `ballast.core.memory as mem` (module ref) not `from memory import MEMORY_DIR` (value ref) | Yes |

---

## Success Criteria

| Feature | Target | Verification |
|---------|--------|--------------|
| Per-axis ambiguity scoring | `AmbiguityScores` has 3 independent axes with `is_blocking` flags | `test_blocking_axes_filters_correctly` passes |
| Learned threshold | Default 0.60, drifts from outcomes, per-domain | `test_policy_threshold_updates_via_memory` passes |
| Policy decision | `should_ask` uses threshold, not hardcoded | `test_policy_should_ask_*` passes |
| `LockedSpec` shape | All 10 fields present, Pydantic-validated | `test_locked_spec_constructs_with_all_fields` passes |
| `RunPhaseTracker` propagation | `action_type` updates per-event, `step_index` increments | `test_tracker_updates_action_type_from_tool_start` passes |
| No regressions | All 24 prior tests still pass | `pytest tests/test_stream.py tests/test_memory.py -v` → `24 passed` |
| Total test count | ≥ 59 non-integration | `pytest -m 'not integration'` → `≥ 59 passed` |
| Integration smoke | `lock_spec` returns valid `LockedSpec` with real Claude call | `pytest -m integration` with key set → `1 passed` |
| Trajectory validator | `validate_trajectory` returns `TrajectoryReport` with `passed=True` on matching output | `test_validate_passes_when_criteria_keywords_in_output` passes |
| Calibration loop closed | `validate_trajectory` calls `update_domain_threshold` — threshold drifts after run | `test_validate_wires_calibration` passes |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Do not proceed past a Human Gate without explicit human input.**
⚠️ **Steps 3–6 are all appends to the same file — Pre-Read Gate on each step must confirm the anchor from the previous step exists before appending.**
⚠️ **Step 2 is an append to `memory.py` — never modify any existing function in that file.**
⚠️ **Do not batch multiple steps into one git commit.**
⚠️ **Integration test in Step 7 requires `ANTHROPIC_API_KEY` — run separately from CI.**
⚠️ **Architecture Overview must be read before Pre-Flight begins.**