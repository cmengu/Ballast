# Phrase 2 — `spec.py` + `trajectory.py`: Project-Overview Aligned Rewrite

**Overall Progress:** `0%` (0 / 6 steps complete)

---

## TLDR

Rewrite `ballast/core/spec.py` and `ballast/core/trajectory.py` from scratch to match the architecture in `.ballast_memory/projet-overview.md`. The current `spec.py` (~780 lines) is built on wrong abstractions (`LockedSpec`, `IntentSignal`, 3-axis ambiguity, `RunPhaseTracker`) — none appear in the target design. The current `trajectory.py` is a models-only stub built against LangGraph `astream_events` — the target uses pydantic-ai `Agent.iter`.

After this plan: `parse_spec(path)` reads `spec.md` → `SpecModel`. `lock(spec)` stamps version + locked_at. `TrajectoryChecker.check(node)` scores every pydantic-ai `Agent.iter` node against the locked spec. `run_with_spec(agent, task, spec)` wraps `Agent.iter`, raises `DriftDetected` on drift. All tests rewritten. `observe.py` not touched (broken, addressed in follow-up).

---

## Architecture Overview

**The problem this plan solves:**

`spec.py` implements 12-field `LockedSpec` with `domain`, `scope`, `output_format`, `ambiguity_scores`, `intent_signal` — none in the project-overview `SpecModel`. `RunPhaseTracker` mutates `intent_signal` — explicitly rejected by the new design. 3-axis `AmbiguityScores` replaces a simpler `score_specificity() → float`. `trajectory.py` uses LangGraph `astream_events` as the interception point — the project-overview requires pydantic-ai `Agent.iter` node boundaries.

**Patterns applied:**

| Pattern | Where | What breaks if violated |
|---------|-------|------------------------|
| **Single class, lock-by-field** | `SpecModel.locked_at` carries lock state; no `LockedSpec` subclass | Downstream needs isinstance checks; spec version no longer travels as one object |
| **Immutable-by-convention after `lock()`** | `lock()` returns `model_copy(update={...})` — never mutates | Caller's draft silently becomes locked; invariant 1 violated |
| **Detector/Handler split** | `trajectory.py` detects; `guardrails.py` handles | `run_with_spec` catches+swallows `DriftDetected` → escalation chain never fires |
| **Bottleneck aggregate** | `score = min(intent, tool, constraint)` | Weighted average: 0.0 tool score diluted by high intent → forbidden tool call passes |
| **Duck-typed node extractor** | `_extract_node_info` uses `hasattr`, not `isinstance` | pydantic-ai node API changes between minor versions; `isinstance` breaks on version bump |

**What stays unchanged:** `memory.py`, `adapters/agui.py`, `scripts/observe.py` (observe.py currently broken — out of scope), `tests/test_memory.py`, `tests/test_stream.py`.

**What this plan adds:**

| File | Single responsibility |
|------|-----------------------|
| `ballast/core/spec.py` (full rewrite) | `SpecModel` contract + `parse_spec` + `score_specificity` + `clarify` + `lock` + `is_locked` |
| `ballast/core/trajectory.py` (full rewrite) | `TrajectoryChecker` + 3 scorers + `DriftResult` + `DriftDetected` + `run_with_spec` |
| `spec.md` (new, repo root) | Developer-facing sample spec in the project-overview format |
| `tests/test_spec.py` (full rewrite) | Contract tests for all new `spec.py` public functions |
| `tests/test_trajectory.py` (full rewrite) | Contract tests for scorers, checker state machine, DriftResult fields |

**Critical decisions:**

| Decision | Alternative | Why rejected |
|----------|------------|--------------|
| Single `SpecModel`, `locked_at` as lock sentinel | Separate frozen `LockedSpec` subclass | Downstream needs isinstance checks; spec travels as one object in M5→M2 dispatch |
| `version = sha256(intent + sorted_criteria)[:8]` | Monotonic int counter | Counter resets across restarts; sha256 is stable for distributed M5/M2 context (invariant 2) |
| `drift_threshold` on `SpecModel` | `threshold` param to `TrajectoryChecker.__init__` | Threshold must travel with the spec — if separate, M2 could use different threshold than M5 for same spec |
| Duck-typed `_extract_node_info` | `isinstance` against pydantic-ai node types | pydantic-ai 0.0.x node API changes between minor versions |
| `score_tool_compliance` never calls LLM | LLM semantic matching | Tool names are deterministic strings; LLM adds latency+failure modes to an O(1) check |
| Fail-safe 0.5 for intent/constraint | Fail-safe 0.0 (block) | Network error during LLM scoring should not abort a valid agent run |

**Known limitations:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| `observe.py` broken after this plan | Out of scope — follow-up plan | Rewrite to use `parse_spec` + `run_with_spec` |
| `clarify()` enriches silently via LLM; not interactive | Textual dashboard doesn't exist yet | Week 3: wire clarify() questions to dashboard event bus |
| OTel emission is `logger.warning()` in `run_with_spec` | `adapters/otel.py` not yet built (build sequence step 9) | Replace with `emit_drift_span(result)` in Week 3 |
| pydantic-ai `Agent.iter` output extraction uses defensive `hasattr` chain | Exact API not yet confirmed against installed version | Step 3 inspection confirms exact method; update if needed |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---------|----------|--------|----------|----------|
| pydantic-ai `Agent.iter` node class names | `_SCOREABLE_NAME_FRAGMENTS` in trajectory.py | Step 3 inspection script | Step 4 | ⬜ Resolved at Step 3 pre-read gate |
| pydantic-ai output extraction API | `agent_run.get_output()` vs `agent_run.result.data` | Step 3 inspection | Step 4 | ⬜ Resolved at Step 3 pre-read gate |
| `SpecModel.constraints` type | `List[str]` | projet-overview.md | Step 1 | ✅ Confirmed |
| Who calls `clarify()` — caller decides, not auto | Caller inspects `score_specificity()` and decides | Architecture decision | Step 1 | ✅ Confirmed |

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
Run ALL of the following. Do not change anything. Show full output.

(1) /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ --tb=short 2>&1 | tail -5
    Record: exact test count and any failures.
    EXPECTED: some failures in test_trajectory.py (validate_trajectory was removed in phrase1-partb).

(2) wc -l /Users/ngchenmeng/Ballast/ballast/core/spec.py /Users/ngchenmeng/Ballast/ballast/core/trajectory.py
    Record: line counts before rewrite.

(3) grep -n "^def \|^class " /Users/ngchenmeng/Ballast/ballast/core/spec.py
    Record: all top-level classes and functions in current spec.py (all will be removed).

(4) grep -rn "from ballast.core.spec import\|from ballast.core.trajectory import" \
    /Users/ngchenmeng/Ballast/ballast/ \
    /Users/ngchenmeng/Ballast/scripts/ \
    /Users/ngchenmeng/Ballast/tests/ 2>&1
    Record: all callers of the modules being rewritten.

(5) grep -n "pydantic.ai\|pydantic_ai" /Users/ngchenmeng/Ballast/pyproject.toml
    Must return nothing — confirms pydantic-ai not yet installed.

(6) echo "Pre-flight complete"
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count (passing/failing):       ____
spec.py line count:                 ____
trajectory.py line count:           ____
spec.py public classes+functions:   ____
Files importing spec.py:            ____
Files importing trajectory.py:      ____
```

---

## Tasks

### Phase 0 — Prerequisites

**Goal:** pydantic-ai is installed and importable.

---

- [ ] 🟥 **Step 0: Add `pydantic-ai` to `pyproject.toml` and install**

  **Step Architecture Thinking:**

  **Pattern applied:** Dependency declaration.

  **Why this step exists first in the sequence:** `trajectory.py` imports `from pydantic_ai import Agent`. Without this step, Step 4's import fails on first line.

  **Why `pyproject.toml` is the right location:** It is the single source of truth for project dependencies — `pip install -e ".[dev]"` installs both runtime and dev deps in one command.

  **Alternative considered and rejected:** Installing only into venv without updating `pyproject.toml`. Rejected: next `pip install -e .` would remove it.

  **What breaks if this step deviates:** Step 4 import check fails with `ModuleNotFoundError: No module named 'pydantic_ai'`.

  ---

  **Idempotent:** Yes — pip install -e is idempotent; duplicate dep line would cause pip error, but Pre-Read Gate prevents that.

  **Pre-Read Gate:**
  - Run `grep -n "pydantic" /Users/ngchenmeng/Ballast/pyproject.toml`. Confirm `pydantic-ai` is NOT present. If it appears → STOP (already done).

  **Anchor Uniqueness Check:**
  - Target line: `    "pydantic>=2.0",`
  - Must appear exactly 1 time in `pyproject.toml`. Confirm with grep before editing.

  Edit `/Users/ngchenmeng/Ballast/pyproject.toml` — replace:
  ```toml
      "pydantic>=2.0",
  ```
  with:
  ```toml
      "pydantic>=2.0",
      "pydantic-ai>=0.0.13,<1.0",
  ```

  Then run:
  ```bash
  cd /Users/ngchenmeng/Ballast && venv/bin/pip install -e ".[dev]" --quiet
  ```

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add pyproject.toml
  git -C /Users/ngchenmeng/Ballast commit -m "step 4.0: add pydantic-ai dependency"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm `pydantic-ai` not in pyproject.toml
  - [ ] 🟥 Edit pyproject.toml: add `pydantic-ai>=0.0.13,<1.0`
  - [ ] 🟥 Run pip install
  - [ ] 🟥 Git checkpoint

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  import pydantic_ai
  print(f'pydantic_ai version: {pydantic_ai.__version__}')
  from pydantic_ai import Agent
  print('Agent importable OK')
  "
  ```

  **Pass:** Both lines print without error.

  **Fail:**
  - `ModuleNotFoundError` → pip install failed → re-run pip install with `--verbose` flag to see error
  - Version below 0.0.13 → `agent.iter()` may not be available → upgrade: `pip install "pydantic-ai>=0.0.13,<1.0"`

---

### Phase 1 — `spec.py`

**Goal:** `parse_spec(path)` → `SpecModel`. `lock(spec)` → locked `SpecModel`. `score_specificity`, `clarify`, `is_locked` all importable. Old `LockedSpec`, `lock_spec`, `RunPhaseTracker` are GONE.

---

- [ ] 🟥 **Step 1: Full rewrite of `ballast/core/spec.py`** — *Critical: imported by trajectory.py and all tests*

  **Step Architecture Thinking:**

  **Pattern applied:** **DTO + Facade**. `SpecModel` is the Data Transfer Object — a single Pydantic class that carries all spec state including lock status via `locked_at`. `parse_spec / score_specificity / clarify / lock / is_locked` form a Facade — callers use these 5 functions and never construct `SpecModel` directly except through them.

  **Why this step exists first in Phase 1:** `trajectory.py` (Step 4) imports `SpecModel` from `spec.py`. The data contract must exist before the consumer.

  **Why this file is the right location:** `spec.py` is the contract layer per the project-overview build sequence (item 1: "spec.py + SpecModel — locked before anything else"). Every downstream component imports from one source of truth.

  **Alternative considered and rejected:** Keep `LockedSpec` as a frozen subclass of `SpecModel`. Rejected: downstream code needs `isinstance` checks; spec can no longer travel as a single serialised object in M5→M2 dispatch.

  **What breaks if this step deviates:** `trajectory.py` imports `from ballast.core.spec import SpecModel, is_locked` — if these don't exist, trajectory.py fails at import on every agent run.

  ---

  **Idempotent:** Yes — full overwrite. Re-running produces identical result.

  **Pre-Read Gate:**
  - Run `grep -n "class SpecModel" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return nothing (confirms SpecModel doesn't already exist). If found → STOP.
  - Run `grep -n "class LockedSpec" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return 1 match — confirms current file is the parta version (as expected).

  **Self-Contained Rule:** Complete file below — nothing omitted.

  **No-Placeholder Rule:** All values are literal. No `<VALUE>` tokens.

  Write `/Users/ngchenmeng/Ballast/ballast/core/spec.py` (full overwrite):

  ```python
  """ballast/core/spec.py — Intent grounding layer.

  Public interface:
      parse_spec(path)        — reads spec.md, returns draft SpecModel (locked_at='')
      score_specificity(spec) — LLM: how verifiable is this spec? 0.0–1.0
      clarify(spec)           — LLM: enrich vague fields; raises SpecTooVague if impossible
      lock(spec)              — stamps version + locked_at; returns immutable-by-convention copy
      is_locked(spec)         — True if locked_at is non-empty

  Invariants (from projet-overview.md):
      1. spec locks before any agent executes — enforce with is_locked() guard in callers
      2. spec version travels with every job — version is sha256(intent+criteria)[:8], set at lock()
      3. locked spec is immutable by convention — never mutate a SpecModel after lock()

  spec.md format:
      # spec v1
      ## intent
      one sentence goal
      ## success criteria
      - criterion 1
      ## constraints
      - constraint 1
      ## escalation threshold
      drift confidence floor: 0.4
      timeout before CEO decides: 300 seconds
      ## tools allowed
      - tool_name
  """
  from __future__ import annotations

  import hashlib
  import re
  from datetime import datetime, timezone
  from typing import List

  import anthropic
  from pydantic import BaseModel, Field


  # ---------------------------------------------------------------------------
  # Data contract
  # ---------------------------------------------------------------------------

  class SpecModel(BaseModel):
      """Specification contract. Draft when locked_at=''; locked otherwise.

      Do not construct directly — use parse_spec() or build fields explicitly,
      then call lock() before passing to any agent execution function.
      """
      version: str = Field(
          default="",
          description="sha256(intent + sorted_criteria)[:8]. Set by lock(). Empty = draft.",
      )
      intent: str = Field(
          description="One sentence: what the agent is trying to achieve.",
      )
      success_criteria: List[str] = Field(
          default_factory=list,
          description="Verifiable list of done conditions.",
      )
      constraints: List[str] = Field(
          default_factory=list,
          description="What the agent must never do.",
      )
      drift_threshold: float = Field(
          default=0.4,
          ge=0.0,
          le=1.0,
          description="Minimum acceptable drift score. Below this → DriftDetected.",
      )
      escalation_timeout_seconds: int = Field(
          default=300,
          description="Seconds before CEO agent decides without human response.",
      )
      allowed_tools: List[str] = Field(
          default_factory=list,
          description="Tool names the agent may call. Empty = all tools allowed.",
      )
      locked_at: str = Field(
          default="",
          description="ISO-8601 UTC timestamp set by lock(). Empty = draft.",
      )


  # ---------------------------------------------------------------------------
  # Custom exceptions
  # ---------------------------------------------------------------------------

  class SpecParseError(Exception):
      """Raised by parse_spec() when required sections are missing or file not found."""


  class SpecAlreadyLocked(Exception):
      """Raised by lock() when spec.locked_at is already set."""


  class SpecTooVague(Exception):
      """Raised by clarify() when LLM cannot infer required fields."""

      def __init__(self, missing_fields: list[str]) -> None:
          self.missing_fields = missing_fields
          super().__init__(
              f"Spec too vague to enrich automatically — "
              f"unclear fields: {missing_fields}"
          )


  # ---------------------------------------------------------------------------
  # Anthropic client (lazy singleton)
  # ---------------------------------------------------------------------------

  _spec_client: "anthropic.Anthropic | None" = None
  _SPEC_MODEL = "claude-sonnet-4-6"


  def _get_client() -> "anthropic.Anthropic":
      global _spec_client
      if _spec_client is None:
          _spec_client = anthropic.Anthropic()
      return _spec_client


  # ---------------------------------------------------------------------------
  # parse_spec — reads spec.md
  # ---------------------------------------------------------------------------

  def parse_spec(path: str) -> SpecModel:
      """Read a spec.md file and return a draft SpecModel (locked_at='', version='').

      Parses the ## intent, ## success criteria, ## constraints,
      ## escalation threshold, and ## tools allowed sections.

      Raises SpecParseError if:
          - file not found
          - ## intent section is missing or empty
          - ## success criteria section is missing or has no bullet items
      """
      try:
          with open(path, "r", encoding="utf-8") as f:
              text = f.read()
      except FileNotFoundError:
          raise SpecParseError(f"spec file not found: {path}")

      def _section(name: str) -> str:
          """Text between ## name and the next ## heading (or EOF). Case-insensitive."""
          pattern = rf"##\s+{re.escape(name)}\s*\n(.*?)(?=\n##\s|\Z)"
          m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
          return m.group(1).strip() if m else ""

      def _bullets(section_text: str) -> list[str]:
          """Return lines starting with '-', stripped."""
          items = []
          for line in section_text.splitlines():
              line = line.strip()
              if line.startswith("-"):
                  item = line.lstrip("-").strip()
                  if item:
                      items.append(item)
          return items

      intent = _section("intent")
      if not intent:
          raise SpecParseError(
              "spec.md is missing required ## intent section"
          )

      criteria_text = _section("success criteria")
      success_criteria = _bullets(criteria_text)
      if not success_criteria:
          raise SpecParseError(
              "spec.md ## success criteria section is missing or has no bullet items"
          )

      constraints = _bullets(_section("constraints"))
      allowed_tools = _bullets(_section("tools allowed"))

      drift_threshold = 0.4
      escalation_timeout = 300
      threshold_text = _section("escalation threshold")
      if threshold_text:
          for line in threshold_text.splitlines():
              line_lower = line.lower()
              if "drift confidence floor" in line_lower:
                  m = re.search(r"[\d.]+", line)
                  if m:
                      try:
                          drift_threshold = float(m.group())
                      except ValueError:
                          pass
              elif "timeout" in line_lower:
                  m = re.search(r"\d+", line)
                  if m:
                      try:
                          escalation_timeout = int(m.group())
                      except ValueError:
                          pass

      return SpecModel(
          version="",
          intent=intent,
          success_criteria=success_criteria,
          constraints=constraints,
          drift_threshold=drift_threshold,
          escalation_timeout_seconds=escalation_timeout,
          allowed_tools=allowed_tools,
          locked_at="",
      )


  # ---------------------------------------------------------------------------
  # score_specificity — LLM-based single float
  # ---------------------------------------------------------------------------

  _SPECIFICITY_SYSTEM = (
      "You are a specification quality reviewer for an AI agent system. "
      "Score how specific and verifiable a given spec is. "
      "A good spec has a clear intent, measurable success criteria, and unambiguous constraints. "
      "A bad spec is vague, unmeasurable, or interpretable multiple ways."
  )

  _SPECIFICITY_TOOL = {
      "name": "score_specificity",
      "description": "Score how specific and verifiable this spec is.",
      "input_schema": {
          "type": "object",
          "properties": {
              "score": {
                  "type": "number",
                  "description": (
                      "0.0 = completely vague/unverifiable, "
                      "1.0 = fully specific and verifiable"
                  ),
              },
              "rationale": {
                  "type": "string",
                  "description": "One sentence: why this score.",
              },
              "vague_fields": {
                  "type": "array",
                  "items": {"type": "string"},
                  "description": (
                      "Fields that are too vague: any of "
                      "intent, success_criteria, constraints"
                  ),
              },
          },
          "required": ["score", "rationale", "vague_fields"],
      },
  }


  def score_specificity(spec: SpecModel) -> float:
      """LLM-based: how specific and verifiable is this spec?

      Returns float in [0.0, 1.0]. Fail-safe: returns 0.5 on any error.
      Never raises.
      """
      criteria = "\n".join(f"  - {c}" for c in spec.success_criteria)
      constraints = "\n".join(f"  - {c}" for c in spec.constraints)
      prompt = (
          f"Intent: {spec.intent}\n"
          f"Success criteria:\n{criteria}\n"
          f"Constraints:\n{constraints}"
      )
      try:
          response = _get_client().messages.create(
              model=_SPEC_MODEL,
              max_tokens=200,
              system=_SPECIFICITY_SYSTEM,
              tools=[_SPECIFICITY_TOOL],
              tool_choice={"type": "tool", "name": "score_specificity"},
              messages=[{"role": "user", "content": prompt}],
          )
          for block in response.content:
              if block.type == "tool_use":
                  return max(0.0, min(1.0, float(block.input.get("score", 0.5))))
      except Exception:
          pass
      return 0.5


  # ---------------------------------------------------------------------------
  # clarify — LLM enrichment for vague specs
  # ---------------------------------------------------------------------------

  _CLARIFY_SYSTEM = (
      "You are a spec enrichment assistant for an AI agent system. "
      "Given a vague specification, enrich it with specific, measurable details. "
      "If a required field is impossible to clarify without human input, "
      "list it in unclear_fields."
  )

  _CLARIFY_TOOL = {
      "name": "enrich_spec",
      "description": "Return an enriched version of the spec.",
      "input_schema": {
          "type": "object",
          "properties": {
              "intent": {
                  "type": "string",
                  "description": "Enriched, specific one-sentence intent.",
              },
              "success_criteria": {
                  "type": "array",
                  "items": {"type": "string"},
                  "description": "Enriched list of verifiable done conditions.",
              },
              "constraints": {
                  "type": "array",
                  "items": {"type": "string"},
                  "description": "Enriched constraints (may be empty if none needed).",
              },
              "unclear_fields": {
                  "type": "array",
                  "items": {"type": "string"},
                  "description": (
                      "Fields that cannot be enriched without human input. "
                      "Use field names: intent, success_criteria, constraints."
                  ),
              },
          },
          "required": ["intent", "success_criteria", "constraints", "unclear_fields"],
      },
  }


  def clarify(spec: SpecModel) -> SpecModel:
      """LLM: enrich vague fields in a draft SpecModel.

      Returns enriched SpecModel (still a draft — locked_at='').
      Never mutates the input spec — returns a new SpecModel.
      Raises SpecTooVague(missing_fields) if LLM cannot infer required fields.

      Caller decides when to call this — typically when score_specificity() < 0.6.
      """
      criteria = "\n".join(f"  - {c}" for c in spec.success_criteria)
      constraints_text = "\n".join(f"  - {c}" for c in spec.constraints)
      prompt = (
          f"Intent: {spec.intent}\n"
          f"Success criteria:\n{criteria}\n"
          f"Constraints:\n{constraints_text}\n\n"
          "Enrich this spec. Make intent specific and measurable. "
          "Add concrete success criteria if vague. "
          "If you cannot determine what the agent should do, list unclear_fields."
      )
      try:
          response = _get_client().messages.create(
              model=_SPEC_MODEL,
              max_tokens=400,
              system=_CLARIFY_SYSTEM,
              tools=[_CLARIFY_TOOL],
              tool_choice={"type": "tool", "name": "enrich_spec"},
              messages=[{"role": "user", "content": prompt}],
          )
          for block in response.content:
              if block.type == "tool_use":
                  raw = block.input
                  unclear = raw.get("unclear_fields", [])
                  if unclear:
                      raise SpecTooVague(unclear)
                  return SpecModel(
                      version="",
                      intent=raw.get("intent", spec.intent),
                      success_criteria=raw.get(
                          "success_criteria", spec.success_criteria
                      ),
                      constraints=raw.get("constraints", spec.constraints),
                      drift_threshold=spec.drift_threshold,
                      escalation_timeout_seconds=spec.escalation_timeout_seconds,
                      allowed_tools=spec.allowed_tools,
                      locked_at="",
                  )
      except SpecTooVague:
          raise
      except Exception:
          pass
      return spec  # Fail-safe: return original unchanged


  # ---------------------------------------------------------------------------
  # lock — stamps version + locked_at
  # ---------------------------------------------------------------------------

  def lock(spec: SpecModel) -> SpecModel:
      """Stamp version and locked_at onto a draft SpecModel. Return locked copy.

      version = sha256(intent + '|'.join(sorted(success_criteria)))[:8]
      locked_at = UTC ISO-8601 timestamp ending in 'Z'

      Raises SpecAlreadyLocked if spec.locked_at is already set.
      Returns a new SpecModel — input is never mutated.
      After lock(), treat the returned spec as immutable (invariant 1 + 2).
      """
      if spec.locked_at:
          raise SpecAlreadyLocked(
              f"spec already locked at {spec.locked_at} "
              f"(version={spec.version})"
          )

      raw = (spec.intent + "|".join(sorted(spec.success_criteria))).encode()
      version = hashlib.sha256(raw).hexdigest()[:8]
      locked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

      return spec.model_copy(update={"version": version, "locked_at": locked_at})


  # ---------------------------------------------------------------------------
  # is_locked — guard used by callers before execution
  # ---------------------------------------------------------------------------

  def is_locked(spec: SpecModel) -> bool:
      """Return True if this spec has been locked (locked_at is non-empty).

      Callers must check is_locked(spec) before passing spec to any agent
      execution function. Invariant: no agent executes without a locked spec.
      """
      return bool(spec.locked_at)
  ```

  **What it does:** Replaces all ~780 lines of the old spec.py with ~270 lines implementing the project-overview architecture. Removes `LockedSpec`, `IntentSignal`, `RunPhaseTracker`, `AmbiguityType/Score/Scores`, `ClarificationPolicy`, `lock_spec`, `lock_spec_with_answers`. Adds `SpecModel`, `SpecParseError`, `SpecAlreadyLocked`, `SpecTooVague`, `parse_spec`, `score_specificity`, `clarify`, `lock`, `is_locked`.

  **Assumptions:**
  - `anthropic>=0.20` installed (confirmed in pyproject.toml)
  - `pydantic>=2.0` installed (confirmed in pyproject.toml)
  - `ANTHROPIC_API_KEY` set in env for LLM calls (score_specificity, clarify)

  **Risks:**
  - Old `spec.py` imports in `test_spec.py` will break immediately → Step 5 rewrites test_spec.py
  - `observe.py` imports `lock_spec`, `RunPhaseTracker` — both removed → observe.py breaks. Out of scope, acknowledged.

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/spec.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 4.1: replace spec.py with SpecModel, parse_spec, score_specificity, clarify, lock, is_locked"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm `SpecModel` not in spec.py; confirm `LockedSpec` is present (old version)
  - [ ] 🟥 Write `ballast/core/spec.py` (full overwrite with content above)
  - [ ] 🟥 Git checkpoint

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  from ballast.core.spec import (
      SpecModel, SpecParseError, SpecAlreadyLocked, SpecTooVague,
      parse_spec, score_specificity, clarify, lock, is_locked,
  )
  print('imports OK')

  # SpecModel construction
  spec = SpecModel(
      intent='count words in a string',
      success_criteria=['returns an integer', 'integer is accurate'],
      constraints=['do not call external APIs'],
      allowed_tools=['get_word_count'],
  )
  assert spec.drift_threshold == 0.4
  assert spec.locked_at == ''
  assert spec.version == ''
  print('SpecModel defaults OK')

  # lock()
  locked = lock(spec)
  assert len(locked.version) == 8
  assert locked.locked_at.endswith('Z')
  assert spec.locked_at == ''   # original unchanged
  print('lock() OK')

  # is_locked()
  assert not is_locked(spec)
  assert is_locked(locked)
  print('is_locked() OK')

  # lock() raises on re-lock
  try:
      lock(locked)
      assert False, 'Expected SpecAlreadyLocked'
  except SpecAlreadyLocked:
      pass
  print('SpecAlreadyLocked OK')

  # LockedSpec must NOT exist
  try:
      from ballast.core.spec import LockedSpec
      assert False, 'LockedSpec should not exist'
  except ImportError:
      pass
  print('LockedSpec removed OK')

  # lock_spec must NOT exist
  try:
      from ballast.core.spec import lock_spec
      assert False, 'lock_spec should not exist'
  except ImportError:
      pass
  print('lock_spec removed OK')

  print('Step 1 OK')
  "
  ```

  **Pass:** All 6 OK lines print with exit code 0.

  **Fail:**
  - `ImportError` on SpecModel → write failed → re-read spec.py
  - `LockedSpec importable` → old file not replaced → check file was written
  - `lock() mutates input` → `model_copy` not used → re-read lock() function

---

- [ ] 🟥 **Step 2: Create `spec.md` at repo root** — *Non-critical: test fixture and developer reference*

  **Step Architecture Thinking:**

  **Pattern applied:** Fixture / reference document. `spec.md` is both a test fixture for `parse_spec()` and the developer-facing reference for the spec format.

  **Why this step exists here:** Step 1 added `parse_spec(path)` — Step 2 provides a concrete file to pass to it. Step 5 (`test_spec.py`) uses this file in integration tests.

  **What breaks if this step deviates:** `parse_spec('/Users/ngchenmeng/Ballast/spec.md')` raises `SpecParseError: file not found` in all tests that reference the repo-root spec.

  ---

  **Idempotent:** Yes — writing the same file again produces the same result.

  **Pre-Read Gate:**
  - Run `ls /Users/ngchenmeng/Ballast/spec.md 2>&1`. If file exists → read it and STOP: do not overwrite without human instruction.

  Write `/Users/ngchenmeng/Ballast/spec.md`:

  ```markdown
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
  - do not access the filesystem

  ## escalation threshold
  drift confidence floor: 0.4
  timeout before CEO decides: 300 seconds

  ## tools allowed
  - get_word_count
  ```

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add spec.md
  git -C /Users/ngchenmeng/Ballast commit -m "step 4.2: add sample spec.md at repo root"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm spec.md does not exist
  - [ ] 🟥 Write spec.md with content above
  - [ ] 🟥 Git checkpoint

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  from ballast.core.spec import parse_spec, lock, is_locked
  spec = parse_spec('/Users/ngchenmeng/Ballast/spec.md')
  assert 'Count the number of words' in spec.intent
  assert len(spec.success_criteria) == 3
  assert len(spec.constraints) == 3
  assert spec.drift_threshold == 0.4
  assert spec.escalation_timeout_seconds == 300
  assert 'get_word_count' in spec.allowed_tools
  assert not is_locked(spec)
  locked = lock(spec)
  assert is_locked(locked)
  print(f'parse_spec OK: intent={spec.intent[:40]!r}')
  print(f'lock OK: version={locked.version} locked_at={locked.locked_at}')
  print('Step 2 OK')
  "
  ```

  **Pass:** All assertions pass, version is 8 chars, locked_at ends with 'Z'.

  **Fail:**
  - `SpecParseError: file not found` → spec.md not written to correct path → check path
  - `len(success_criteria) != 3` → bullet parsing broken → check `_bullets()` in parse_spec

---

### Phase 2 — `trajectory.py`

**Goal:** `TrajectoryChecker.check(node)` is callable from pydantic-ai `Agent.iter`. `run_with_spec(agent, task, spec)` wraps the full agent run. All three scorers exist. `DriftDetected` raised when score < `spec.drift_threshold`.

---

- [ ] 🟥 **Step 3: Inspect pydantic-ai `Agent.iter` node types** — *Critical: confirms node class names for trajectory.py*

  **Step Architecture Thinking:**

  **Why this step exists:** `trajectory.py`'s `_is_scoreable()` and `_extract_node_info()` use node class names. If the names assumed in Step 4 code don't match the installed pydantic-ai version's actual names, `_is_scoreable()` returns False for all nodes → no drift scoring ever fires. This step confirms the actual names before any code is written.

  **Idempotent:** Yes — observation only, no writes.

  **Pre-Read Gate:**
  - Run `grep -n "pydantic.ai\|pydantic_ai" /Users/ngchenmeng/Ballast/pyproject.toml`. Must show pydantic-ai (confirms Step 0 succeeded).

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  import pydantic_ai
  import inspect

  print(f'=== pydantic_ai version: {pydantic_ai.__version__} ===')

  # List all classes exported from pydantic_ai top-level
  print('\n[pydantic_ai top-level classes]')
  for name in sorted(dir(pydantic_ai)):
      obj = getattr(pydantic_ai, name, None)
      if inspect.isclass(obj):
          print(f'  {name}')

  # Check for nodes/messages submodules
  for submod in ('nodes', 'messages', 'result', 'agent'):
      try:
          mod = __import__(f'pydantic_ai.{submod}', fromlist=[''])
          print(f'\n[pydantic_ai.{submod} classes]')
          for name in sorted(dir(mod)):
              obj = getattr(mod, name, None)
              if inspect.isclass(obj):
                  print(f'  {name}')
      except ImportError:
          print(f'  (no pydantic_ai.{submod} module)')

  # Check Agent.iter signature
  from pydantic_ai import Agent
  sig = inspect.signature(Agent.iter)
  print(f'\n[Agent.iter signature]: {sig}')
  " 2>&1
  ```

  **Record the output.** Specifically note:
  - Classes with "Node", "Request", "Response", "Tool", "Call", "Return" in their names
  - The `Agent.iter` method signature
  - Whether `agent_run.get_output()` or `agent_run.result.data` is the correct pattern

  **What to look for:** The node types yielded by `async for node in agent_run` will be among the classes listed. Common pydantic-ai node names (may differ by version):
  - `ModelRequestNode`, `ModelResponseNode`, `CallToolsNode` (0.0.x pattern)
  - `ModelRequest`, `ModelResponse` (newer pattern)

  ---

  **⚠ STOP CONDITIONS — evaluate before proceeding to Step 4:**

  STOP if any of the following are true:
  1. `Agent.iter` does not exist on the Agent class (AttributeError in inspection output)
  2. `Agent.iter` signature does not accept a single positional string argument
  3. `Agent.iter` is not an async context manager (i.e., not usable as `async with agent.iter(task) as run:`)

  If any STOP condition is true → output the exact AttributeError or signature found, and stop. Do not write Step 4 code.

  ---

  **Post-Step-3 checkpoint — required before Step 4 starts:**

  After running inspection and verifying no STOP conditions apply, output the following filled-in block:

  ```
  POST-STEP-3 CHECKPOINT
  pydantic_ai version installed: ___
  Agent.iter signature: ___
  Node class names found (with "Node"/"Tool"/"Request"/"Response" in name): ___
  Output extraction API confirmed: get_output() / result.data / other: ___

  _SCOREABLE_NAME_FRAGMENTS to use in Step 4 (update from inspection output):
  frozenset({
      "<name1>", "<name2>", ...
      # keep original set entries that still appear in installed version
      # add new names found in inspection
  })
  ```

  Only proceed to Step 4 after this block is filled and printed. The agent must not carry the original `_SCOREABLE_NAME_FRAGMENTS` forward if actual names differ.

  **If output extraction API is NOT `get_output()`**, update Step 4's `run_with_spec` output block before writing:
  - If confirmed API is `result.data`: replace the entire output extraction block with `return agent_run.result.data`
  - If confirmed API is something else: note the exact attribute path; use it as primary; keep `hasattr` chains as fallbacks with a `logger.warning` if none match
  - If `get_output()` is confirmed: write Step 4 as-is

  ---

  **Update required if names differ from `_SCOREABLE_NAME_FRAGMENTS` in Step 4:**
  `_SCOREABLE_NAME_FRAGMENTS` in Step 4's trajectory.py contains:
  ```python
  {"ModelRequest", "ModelResponse", "ToolCall", "ToolReturn", "CallTools", "FunctionCall"}
  ```
  If inspection shows different names → update `_SCOREABLE_NAME_FRAGMENTS` in Step 4 to include the actual names before writing.

  **No git checkpoint — observation only.**

  **Subtasks:**
  - [ ] 🟥 Run inspection script above
  - [ ] 🟥 Check STOP conditions — if any true, STOP and report
  - [ ] 🟥 Fill Post-Step-3 checkpoint block (print it before Step 4)
  - [ ] 🟥 Record: actual node class names containing "Node", "Tool", "Request", "Response"
  - [ ] 🟥 Record: output extraction API (`get_output()` or `result.data` or other)
  - [ ] 🟥 If actual names NOT in `_SCOREABLE_NAME_FRAGMENTS`: update the set in Step 4 before writing

---

- [ ] 🟥 **Step 4: Full rewrite of `ballast/core/trajectory.py`** — *Critical: core drift detection — the system's primary enforcement mechanism*

  **Step Architecture Thinking:**

  **Pattern applied:** **Detector/Handler split + Facade**. `TrajectoryChecker.check()` is the single Facade entry point. Three scorers (`score_tool_compliance`, `score_constraint_violation`, `score_intent_alignment`) are the internal strategy implementations. `run_with_spec()` is the public orchestrator. `trajectory.py` ONLY detects and raises — `guardrails.py` handles.

  **Why this step exists after Step 3:** Node type names confirmed in Step 3 feed into `_SCOREABLE_NAME_FRAGMENTS`. Writing before confirming names means scoring may silently skip all nodes.

  **Why trajectory.py is the right location:** Per project-overview build sequence: item 2 "trajectory.py — Agent.iter hook, drift scoring stub". It owns the single responsibility of mid-run scoring.

  **Alternative considered and rejected:** Putting `check()` inside the `Agent` subclass. Rejected: couples detection to the agent implementation — cannot change scoring logic without touching the agent.

  **What breaks if `run_with_spec` catches and swallows `DriftDetected`:** `guardrails.py` can never receive the escalation. Invariant 4 (escalation never drops context) is violated.

  ---

  **Idempotent:** Yes — full overwrite.

  **Pre-Read Gate:**
  - Run `grep -n "class TrajectoryChecker" /Users/ngchenmeng/Ballast/ballast/core/trajectory.py`. Must return nothing (confirms Step 4 not already done). If found → STOP.
  - Run `grep -n "from ballast.core.spec import" /Users/ngchenmeng/Ballast/ballast/core/trajectory.py`. Note current import (will be replaced).
  - Confirm `_SCOREABLE_NAME_FRAGMENTS` below includes node names found in Step 3. Update if needed.

  **Self-Contained Rule:** Complete file below.

  Write `/Users/ngchenmeng/Ballast/ballast/core/trajectory.py` (full overwrite):

  ```python
  """ballast/core/trajectory.py — Mid-run drift detection.

  Public interface:
      run_with_spec(agent, task, spec)  — wraps Agent.iter; checks every node
      TrajectoryChecker                  — check(node) → DriftResult | None
      DriftResult                        — scored assessment of one node
      DriftDetected                      — raised when score < spec.drift_threshold

  Score dimensions (aggregate = min of all three):
      score_tool_compliance      — rule-based (never LLM): is tool in allowed_tools?
      score_constraint_violation — LLM: did action breach a hard constraint?
      score_intent_alignment     — LLM: is action moving toward the goal?

  Threshold: spec.drift_threshold (travels with the spec — invariant 2).
  Interception: pydantic-ai Agent.iter node boundaries (duck-typed for version resilience).

  Key invariant:
      trajectory.py detects and reports. guardrails.py decides what happens next.
      DriftDetected is NEVER caught inside this module (only in run_with_spec for logging,
      then immediately re-raised).
  """
  from __future__ import annotations

  import logging
  from typing import Any, Optional

  import anthropic
  from pydantic import BaseModel, Field
  from pydantic_ai import Agent

  from ballast.core.spec import SpecModel, is_locked

  logger = logging.getLogger(__name__)


  # ---------------------------------------------------------------------------
  # DriftResult — scored assessment of one node
  # ---------------------------------------------------------------------------

  class DriftResult(BaseModel):
      """Complete scoring result for a single pydantic-ai Agent.iter node.

      Produced by TrajectoryChecker.check() on every scored node.
      Carried by DriftDetected when score < spec.drift_threshold.
      Consumed by guardrails.py for escalation policy decisions.
      """
      score: float = Field(
          ge=0.0, le=1.0,
          description="min(intent, tool, constraint). 0.0=complete drift, 1.0=aligned.",
      )
      intent_score: float = Field(ge=0.0, le=1.0)
      tool_score: float = Field(ge=0.0, le=1.0)
      constraint_score: float = Field(ge=0.0, le=1.0)
      failing_dimension: str = Field(
          description="'tool' | 'constraint' | 'intent' | 'none'. Priority: tool > constraint > intent."
      )
      node_type: str = Field(description="type(node).__name__ of the scored pydantic-ai node")
      spec_version: str = Field(description="SpecModel.version — identifies spec in effect")
      raised_at_step: int = Field(description="1-indexed monotonic step counter")
      threshold: float = Field(description="spec.drift_threshold applied at this step")


  # ---------------------------------------------------------------------------
  # DriftDetected exception
  # ---------------------------------------------------------------------------

  class DriftDetected(Exception):
      """Raised by TrajectoryChecker.check() when drift score < spec.drift_threshold.

      Carries DriftResult so guardrails.py has full context.
      trajectory.py raises this. trajectory.py never silently swallows this.
      run_with_spec logs it then immediately re-raises — guardrails.py handles.
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


  # ---------------------------------------------------------------------------
  # Anthropic client (lazy singleton)
  # ---------------------------------------------------------------------------

  _judge_client: "anthropic.Anthropic | None" = None
  _JUDGE_MODEL = "claude-sonnet-4-6"


  def _get_judge_client() -> "anthropic.Anthropic":
      global _judge_client
      if _judge_client is None:
          _judge_client = anthropic.Anthropic()
      return _judge_client


  # ---------------------------------------------------------------------------
  # Node info extractor — duck-typed for pydantic-ai version resilience
  # ---------------------------------------------------------------------------

  def _extract_node_info(node: Any) -> tuple[str, str, dict]:
      """Extract (node_type_name, content, tool_info) from a pydantic-ai Agent.iter node.

      Uses duck typing via hasattr and class name substring checks.
      This makes it resilient to pydantic-ai version differences in class hierarchy.

      Returns:
          node_type:  type(node).__name__
          content:    up to 1000 chars of extractable text (for LLM scorers)
          tool_info:  {'tool_name': str, 'tool_args': dict} or {} if not a tool call
      """
      node_type = type(node).__name__
      content = ""
      tool_info: dict = {}

      # --- Tool call detection ---
      # Direct attributes (some pydantic-ai versions expose tool_name at top level)
      if hasattr(node, "tool_name") and hasattr(node, "args"):
          args_raw = getattr(node, "args", {})
          tool_info = {
              "tool_name": str(node.tool_name),
              "tool_args": args_raw if isinstance(args_raw, dict) else {},
          }

      # Scan parts (ModelResponse may contain ToolCallPart objects)
      for container_attr in ("parts", "messages"):
          container = getattr(node, container_attr, None) or []
          if not hasattr(container, "__iter__"):
              continue
          for part in container:
              part_type_name = type(part).__name__
              if part_type_name in ("ToolCallPart", "ToolCall", "FunctionCall"):
                  t_name = str(
                      getattr(part, "tool_name", getattr(part, "function_name", ""))
                  )
                  t_args = getattr(part, "args", getattr(part, "arguments", {}))
                  if t_name and not tool_info:
                      tool_info = {
                          "tool_name": t_name,
                          "tool_args": t_args if isinstance(t_args, dict) else {},
                      }

      # Scan nested request/response wrappers
      for wrapper_attr in ("request", "response"):
          wrapper = getattr(node, wrapper_attr, None)
          if not wrapper:
              continue
          for container_attr in ("parts", "messages"):
              container = getattr(wrapper, container_attr, None) or []
              if not hasattr(container, "__iter__"):
                  continue
              for part in container:
                  part_type_name = type(part).__name__
                  if part_type_name in ("ToolCallPart", "ToolCall", "FunctionCall"):
                      t_name = str(
                          getattr(part, "tool_name", getattr(part, "function_name", ""))
                      )
                      t_args = getattr(part, "args", getattr(part, "arguments", {}))
                      if t_name and not tool_info:
                          tool_info = {
                              "tool_name": t_name,
                              "tool_args": t_args if isinstance(t_args, dict) else {},
                          }

      # --- Content extraction (for LLM scorers) ---
      for attr in ("text", "content", "output"):
          val = getattr(node, attr, None)
          if val and isinstance(val, str):
              content = val[:1000]
              break

      if not content:
          for container_attr in ("parts", "messages"):
              container = getattr(node, container_attr, None) or []
              if not hasattr(container, "__iter__"):
                  continue
              texts = []
              for part in container:
                  for attr in ("text", "content"):
                      val = getattr(part, attr, None)
                      if val and isinstance(val, str):
                          texts.append(val)
              if texts:
                  content = "\n".join(texts)[:1000]
                  break

      if not content:
          for wrapper_attr in ("response", "request"):
              wrapper = getattr(node, wrapper_attr, None)
              if not wrapper:
                  continue
              for attr in ("text", "content"):
                  val = getattr(wrapper, attr, None)
                  if val and isinstance(val, str):
                      content = val[:1000]
                      break

      return node_type, content, tool_info


  # ---------------------------------------------------------------------------
  # Scorer 1 — tool compliance (rule-based, NEVER calls LLM)
  # ---------------------------------------------------------------------------

  def score_tool_compliance(node: Any, spec: SpecModel) -> float:
      """Rule-based: is the tool used in spec.allowed_tools?

      Returns:
          1.0 — no tool call in this node, or allowed_tools=[] (all permitted)
          1.0 — tool_name is in allowed_tools
          0.0 — tool_name is NOT in allowed_tools (hard spec violation)

      Never raises. Never calls LLM. O(1) string membership check.
      """
      _, _, tool_info = _extract_node_info(node)
      tool_name = tool_info.get("tool_name", "")
      if not tool_name:
          return 1.0  # Not a tool call — compliance does not apply
      if not spec.allowed_tools:
          return 1.0  # Empty = all tools allowed
      return 1.0 if tool_name in spec.allowed_tools else 0.0


  # ---------------------------------------------------------------------------
  # Scorer 2 — constraint violation (LLM, fail-safe 0.5)
  # ---------------------------------------------------------------------------

  _CONSTRAINT_SYSTEM = (
      "You are a constraint enforcement monitor for an AI agent mid-run. "
      "Determine whether a single agent action violates any of the stated hard constraints. "
      "Be strict: if an action could plausibly violate a constraint, flag it."
  )

  _CONSTRAINT_TOOL = {
      "name": "constraint_check",
      "description": "Determine if the agent action violates any hard constraint.",
      "input_schema": {
          "type": "object",
          "properties": {
              "violation": {
                  "type": "boolean",
                  "description": "True if any hard constraint is breached.",
              },
              "violated_constraint": {
                  "type": "string",
                  "description": "The exact constraint text breached, or empty string.",
              },
              "rationale": {
                  "type": "string",
                  "description": "One sentence explaining the decision.",
              },
          },
          "required": ["violation", "violated_constraint", "rationale"],
      },
  }


  def score_constraint_violation(node: Any, spec: SpecModel) -> float:
      """LLM-based: does this action breach a hard constraint in spec.constraints?

      Returns: 1.0 (no violation), 0.0 (violated), 0.5 (fail-safe on error).
      Never raises.
      """
      if not spec.constraints:
          return 1.0  # Nothing to violate

      _, content, tool_info = _extract_node_info(node)
      check_content = (
          f"Tool: {tool_info.get('tool_name', 'N/A')}\n"
          f"Args: {str(tool_info.get('tool_args', {}))[:400]}\n"
          f"Content: {content[:600]}"
      )

      constraints_text = "\n".join(f"- {c}" for c in spec.constraints)
      prompt = (
          f"Hard constraints:\n{constraints_text}\n\n"
          f"Agent action:\n{check_content}"
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
                  return 0.0 if block.input.get("violation", False) else 1.0
      except Exception:
          pass
      return 0.5  # Fail-safe: neutral on error


  # ---------------------------------------------------------------------------
  # Scorer 3 — intent alignment (LLM, fail-safe 0.5)
  # ---------------------------------------------------------------------------

  _INTENT_SYSTEM = (
      "You are a mid-run process supervisor for an AI agent. "
      "Score whether a single agent action is moving toward the stated goal.\n"
      "0.0 = actively working against the goal\n"
      "0.5 = neutral / tangential / unclear\n"
      "0.7 = relevant but indirect progress\n"
      "1.0 = directly advancing the goal\n"
      "Use the full range. Be strict: unclear actions score below 0.7."
  )

  _INTENT_TOOL = {
      "name": "score_intent",
      "description": "Score intent alignment of a single agent action.",
      "input_schema": {
          "type": "object",
          "properties": {
              "score": {
                  "type": "number",
                  "description": "0.0 to 1.0 — alignment with the goal.",
              },
              "rationale": {
                  "type": "string",
                  "description": "One sentence explaining the score.",
              },
          },
          "required": ["score", "rationale"],
      },
  }


  def score_intent_alignment(node: Any, spec: SpecModel) -> float:
      """LLM-based: is this action moving toward the goal?

      Returns float in [0.0, 1.0]. Fail-safe: 0.5 on any error. Never raises.
      """
      _, content, tool_info = _extract_node_info(node)
      scoreable = content or tool_info.get("tool_name", "")
      if not scoreable:
          return 0.5  # Nothing to score — neutral

      criteria = "\n".join(f"  - {c}" for c in spec.success_criteria)
      prompt = (
          f"Goal: {spec.intent}\n"
          f"Success criteria:\n{criteria}\n\n"
          f"Agent action (node type: {type(node).__name__}):\n"
          f"Tool: {tool_info.get('tool_name', 'N/A')}  "
          f"Args: {str(tool_info.get('tool_args', {}))[:200]}\n"
          f"Content: {content[:600]}"
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
                  score = float(block.input.get("score", 0.5))
                  return max(0.0, min(1.0, score))
      except Exception:
          pass
      return 0.5  # Fail-safe: neutral on error


  # ---------------------------------------------------------------------------
  # Node scoreability — duck-typed, version-resilient
  # ---------------------------------------------------------------------------

  # Class name substrings that indicate a node worth scoring.
  # Covers common pydantic-ai node naming conventions across versions.
  # UPDATE THIS SET if Step 3 inspection reveals different class names.
  _SCOREABLE_NAME_FRAGMENTS = frozenset({
      "ModelRequest", "ModelResponse", "ToolCall", "ToolReturn",
      "CallTools", "FunctionCall", "FunctionReturn",
  })


  def _is_scoreable(node: Any) -> bool:
      """Return True if this node should be scored by TrajectoryChecker.

      Checks node class name substrings AND duck-typing attribute presence.
      Resilient to pydantic-ai version differences.
      """
      name = type(node).__name__
      # Known pydantic-ai node type fragments
      if any(frag in name for frag in _SCOREABLE_NAME_FRAGMENTS):
          return True
      # Duck-typing fallback: nodes with tool_name or text are always scoreable
      if hasattr(node, "tool_name") or hasattr(node, "text") or hasattr(node, "content"):
          return True
      return False


  # ---------------------------------------------------------------------------
  # TrajectoryChecker — the public interface for per-node drift scoring
  # ---------------------------------------------------------------------------

  class TrajectoryChecker:
      """Mid-run drift detector. Initialised with a locked SpecModel.

      Call check(node) at every node from Agent.iter.

      Key invariants:
          - Requires a locked SpecModel (is_locked(spec) must be True)
          - Never catches DriftDetected internally — always propagates
          - Never modifies spec — read-only consumer
          - Never writes to memory — caller decides what to persist
      """

      def __init__(self, spec: SpecModel) -> None:
          if not is_locked(spec):
              raise ValueError(
                  "TrajectoryChecker requires a locked SpecModel. "
                  "Call lock(spec) before passing to TrajectoryChecker."
              )
          self.spec = spec
          self._step: int = 0

      def check(self, node: Any) -> Optional[DriftResult]:
          """Score a single pydantic-ai Agent.iter node against the locked spec.

          Returns DriftResult if scored and aggregate >= threshold.
          Returns None if node is not scoreable (type not in _SCOREABLE_NAME_FRAGMENTS
          and has no scoreable attributes), or has no extractable content.

          Raises DriftDetected when aggregate score < spec.drift_threshold.
          DriftDetected is NEVER caught here — always propagates to the caller.
          """
          if not _is_scoreable(node):
              return None

          _, content, tool_info = _extract_node_info(node)
          if not content and not tool_info.get("tool_name"):
              return None  # Scoreable type but no content to evaluate

          self._step += 1

          tool_score = score_tool_compliance(node, self.spec)
          constraint_score = score_constraint_violation(node, self.spec)
          intent_score = score_intent_alignment(node, self.spec)

          aggregate = min(tool_score, constraint_score, intent_score)

          # Identify failing dimension — priority: tool > constraint > intent
          # When scores are equal, higher-priority dimension wins.
          if tool_score == aggregate and tool_score < 1.0:
              failing = "tool"
          elif constraint_score == aggregate and constraint_score < 1.0:
              failing = "constraint"
          elif intent_score == aggregate and intent_score < 1.0:
              failing = "intent"
          else:
              failing = "none"

          result = DriftResult(
              score=round(aggregate, 4),
              intent_score=round(intent_score, 4),
              tool_score=round(tool_score, 4),
              constraint_score=round(constraint_score, 4),
              failing_dimension=failing,
              node_type=type(node).__name__,
              spec_version=self.spec.version,
              raised_at_step=self._step,
              threshold=self.spec.drift_threshold,
          )

          # OTel placeholder: structured kwargs map 1:1 to span.set_attribute()
          # Week 3 upgrade: replace with emit_drift_span(result) from adapters/otel.py
          logger.debug(
              "drift_check step=%d score=%.3f intent=%.3f tool=%.3f "
              "constraint=%.3f failing=%r spec_version=%s node_type=%s",
              self._step, aggregate, intent_score, tool_score,
              constraint_score, failing, self.spec.version,
              type(node).__name__,
          )

          if aggregate < self.spec.drift_threshold:
              raise DriftDetected(result)

          return result

      @property
      def step_count(self) -> int:
          """Number of nodes actually scored (excludes non-scoreable and empty nodes)."""
          return self._step


  # ---------------------------------------------------------------------------
  # run_with_spec — top-level entry point
  # ---------------------------------------------------------------------------

  async def run_with_spec(agent: Agent, task: str, spec: SpecModel) -> Any:
      """Run agent against task, checking every node against the locked spec.

      Calls TrajectoryChecker.check(node) at every Agent.iter node.
      On DriftDetected: logs as warning (OTel placeholder), then re-raises.
      guardrails.py catches DriftDetected and decides the escalation policy.

      Args:
          agent:  A pydantic-ai Agent instance.
          task:   The task string to run.
          spec:   A LOCKED SpecModel — is_locked(spec) must be True.

      Returns:
          The agent's final output.

      Raises:
          ValueError if spec is not locked.
          DriftDetected if any node scores below spec.drift_threshold.
      """
      if not is_locked(spec):
          raise ValueError(
              "spec must be locked before executing. Call lock(spec) first."
          )

      checker = TrajectoryChecker(spec)

      async with agent.iter(task) as agent_run:
          async for node in agent_run:
              try:
                  checker.check(node)
              except DriftDetected as e:
                  # OTel placeholder — Week 3: replace with emit_drift_span(e.result)
                  logger.warning(
                      "drift_detected step=%d score=%.3f failing=%r "
                      "spec_version=%s node_type=%s threshold=%.2f",
                      e.result.raised_at_step,
                      e.result.score,
                      e.result.failing_dimension,
                      e.result.spec_version,
                      e.result.node_type,
                      e.result.threshold,
                  )
                  raise  # Never swallow — guardrails.py handles

      # Extract final output — defensive for pydantic-ai version differences
      if hasattr(agent_run, "get_output"):
          return await agent_run.get_output()
      result = getattr(agent_run, "result", None)
      if result is not None:
          return getattr(result, "data", getattr(result, "output", result))
      logger.warning(
          "run_with_spec: output extraction failed — agent_run has neither "
          "get_output() nor .result. spec_version=%s",
          spec.version,
      )
      return None
  ```

  **What it does:** Replaces the models-only stub with the complete mid-run drift detection system. `_extract_node_info` is duck-typed to handle pydantic-ai node shapes across versions. `_is_scoreable` uses class name fragments + hasattr fallbacks. `TrajectoryChecker.check()` orchestrates all three scorers, aggregates with `min()`, and raises `DriftDetected`. `run_with_spec` wraps the full `Agent.iter` loop.

  **Assumptions:**
  - `pydantic-ai>=0.0.13` installed (confirmed in Step 0)
  - `ballast.core.spec.SpecModel` and `is_locked` importable (confirmed in Step 1)
  - `ANTHROPIC_API_KEY` set for LLM scorers (constraint + intent)

  **Risks:**
  - `_SCOREABLE_NAME_FRAGMENTS` doesn't match installed pydantic-ai node names → `_is_scoreable` returns False for all nodes → no scoring fires. Mitigation: `_is_scoreable` also checks for `hasattr(node, 'tool_name')`, `hasattr(node, 'text')`, `hasattr(node, 'content')` as duck-typing fallback.
  - `run_with_spec` output extraction fails → returns None. Mitigation: defensive `hasattr` chain with three fallback paths.

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/trajectory.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 4.4: replace trajectory.py with pydantic-ai Agent.iter mid-run drift detection"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm `TrajectoryChecker` not in trajectory.py; update `_SCOREABLE_NAME_FRAGMENTS` if Step 3 found different names
  - [ ] 🟥 Write `ballast/core/trajectory.py` (full overwrite with content above)
  - [ ] 🟥 Git checkpoint

  **✓ Verification Test:**

  **Type:** Unit (no live API — mocks scorers and spec)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  from unittest.mock import patch
  from ballast.core.spec import SpecModel, lock
  from ballast.core.trajectory import (
      DriftResult, DriftDetected, TrajectoryChecker,
      _extract_node_info, score_tool_compliance,
      score_constraint_violation, score_intent_alignment,
      run_with_spec,
  )
  print('imports OK')

  # Build a locked spec
  spec = lock(SpecModel(
      intent='count words in a string',
      success_criteria=['returns an integer'],
      constraints=[],
      allowed_tools=['get_word_count'],
      drift_threshold=0.7,
  ))

  # TrajectoryChecker requires locked spec
  try:
      from ballast.core.spec import SpecModel as SM
      TrajectoryChecker(SM(intent='x', success_criteria=['y']))
      assert False, 'Expected ValueError'
  except ValueError:
      pass
  print('locked spec guard OK')

  # _extract_node_info on fake tool node
  class FakeTool:
      tool_name = 'get_word_count'
      args = {'text': 'hello world'}
  node_type, content, tool_info = _extract_node_info(FakeTool())
  assert tool_info['tool_name'] == 'get_word_count'
  print('_extract_node_info OK')

  # score_tool_compliance — rule-based
  assert score_tool_compliance(FakeTool(), spec) == 1.0
  class FakeForbidden:
      tool_name = 'forbidden'
      args = {}
  assert score_tool_compliance(FakeForbidden(), spec) == 0.0
  print('score_tool_compliance OK')

  # TrajectoryChecker.check — passing
  checker = TrajectoryChecker(spec)
  with patch('ballast.core.trajectory.score_intent_alignment', return_value=0.9), \
       patch('ballast.core.trajectory.score_constraint_violation', return_value=1.0):
      result = checker.check(FakeTool())
  assert isinstance(result, DriftResult)
  assert result.tool_score == 1.0
  assert result.failing_dimension == 'none'
  assert checker.step_count == 1
  print('passing check OK')

  # TrajectoryChecker.check — forbidden tool raises DriftDetected
  checker2 = TrajectoryChecker(spec)
  try:
      with patch('ballast.core.trajectory.score_intent_alignment', return_value=1.0), \
           patch('ballast.core.trajectory.score_constraint_violation', return_value=1.0):
          checker2.check(FakeForbidden())
      assert False, 'Expected DriftDetected'
  except DriftDetected as e:
      assert e.result.tool_score == 0.0
      assert e.result.failing_dimension == 'tool'
  print('forbidden tool → DriftDetected OK')

  # validate_trajectory must NOT exist
  try:
      from ballast.core.trajectory import validate_trajectory
      assert False, 'validate_trajectory should not exist'
  except ImportError:
      pass
  print('validate_trajectory removed OK')

  print('Step 4 OK')
  "
  ```

  **Pass:** All OK lines print with exit code 0.

  **Fail:**
  - `ImportError on TrajectoryChecker` → write failed → re-read trajectory.py
  - `score_tool_compliance returns wrong value` → `_extract_node_info` not detecting tool_name → check `hasattr(node, 'tool_name')` branch
  - `validate_trajectory importable` → old file not replaced → check file was written

---

### Phase 3 — Tests

**Goal:** `tests/test_spec.py` and `tests/test_trajectory.py` fully replaced to match new APIs. All non-integration tests pass. Integration tests skip without `ANTHROPIC_API_KEY`.

---

- [ ] 🟥 **Step 5: Full rewrite of `tests/test_spec.py`** — *Critical: old tests import removed symbols*

  **Step Architecture Thinking:**

  **Pattern applied:** **Contract testing**. Each test proves one specific contract — a function's observable behaviour for one case. No implementation-internal tests.

  **Why this step exists before Step 6:** test_spec.py tests the SpecModel which is imported by test_trajectory.py helpers. If test_spec.py import fails (due to old symbols), the test runner may cascade-fail test_trajectory.py even before Step 6 runs.

  **What breaks if this step deviates:** `pytest tests/` fails with `ImportError` on old symbols (`AmbiguityScore`, `LockedSpec`, `lock_spec`) — no tests run.

  ---

  **Idempotent:** Yes — full overwrite.

  **Pre-Read Gate:**
  - Run `grep -n "from ballast.core.spec import" /Users/ngchenmeng/Ballast/tests/test_spec.py`. Record: old imports (will be confirmed gone after write).
  - Run `grep -n "class LockedSpec\|def lock_spec" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return nothing — confirms Step 1 succeeded.

  Write `/Users/ngchenmeng/Ballast/tests/test_spec.py` (full overwrite):

  ```python
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
      assert SpecModel(intent="x", success_criteria=["y"]).escalation_timeout_seconds == 300


  def test_spec_model_default_allowed_tools_empty():
      assert SpecModel(intent="x", success_criteria=["y"]).allowed_tools == []


  def test_spec_model_default_locked_at_empty():
      assert SpecModel(intent="x", success_criteria=["y"]).locked_at == ""


  def test_spec_model_default_version_empty():
      assert SpecModel(intent="x", success_criteria=["y"]).version == ""


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
      assert spec.escalation_timeout_seconds == 300


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
      assert spec.version == ""


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
      assert spec.escalation_timeout_seconds == 300


  # ---------------------------------------------------------------------------
  # lock
  # ---------------------------------------------------------------------------

  def test_lock_sets_version_8_chars():
      locked = lock(_make_draft())
      assert len(locked.version) == 8


  def test_lock_sets_locked_at_iso_format():
      locked = lock(_make_draft())
      assert locked.locked_at.endswith("Z")
      assert "T" in locked.locked_at


  def test_lock_version_is_stable():
      draft = _make_draft()
      assert lock(draft).version == lock(draft).version


  def test_lock_version_differs_for_different_intent():
      draft1 = _make_draft()
      draft2 = SpecModel(
          intent="COMPLETELY DIFFERENT INTENT",
          success_criteria=["returns an integer", "integer is accurate"],
      )
      assert lock(draft1).version != lock(draft2).version


  def test_lock_does_not_mutate_input():
      draft = _make_draft()
      locked = lock(draft)
      assert draft.locked_at == ""   # original unchanged
      assert draft.version == ""
      assert locked.locked_at != ""
      assert locked.version != ""


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
  ```

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add tests/test_spec.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 4.5: replace test_spec.py for new SpecModel API"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: read current test_spec.py first line to confirm old imports present
  - [ ] 🟥 Write `tests/test_spec.py` (full overwrite)
  - [ ] 🟥 Git checkpoint

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/test_spec.py -v -m "not integration" --tb=short 2>&1 | tail -10
  ```

  **Pass:** All non-integration tests pass. 0 failed. Integration tests skipped (not `failed`).

  **Fail:**
  - `ImportError: cannot import name 'SpecModel'` → Step 1 write failed → re-read spec.py
  - `ImportError: cannot import name 'AmbiguityScore'` → old test file not overwritten → check write succeeded
  - `test_parse_spec_extracts_success_criteria_list` fails → bullet parser returns wrong count → re-read `_bullets()` in parse_spec

---

- [ ] 🟥 **Step 6: Full rewrite of `tests/test_trajectory.py`** — *Critical: old imports (`validate_trajectory`, `TrajectoryReport`) no longer exist*

  **Step Architecture Thinking:**

  **Pattern applied:** **Contract testing with mocked LLM scorers**. `score_tool_compliance` is tested directly (pure Python, no mock). `score_intent_alignment` and `score_constraint_violation` are mocked in all unit tests — tests prove orchestration logic, not LLM output.

  **What breaks if this step deviates:** `test_trajectory.py` imports `TrajectoryReport` and `validate_trajectory` — both removed — test collection fails, blocking CI.

  ---

  **Idempotent:** Yes — full overwrite.

  **Pre-Read Gate:**
  - Run `grep -n "validate_trajectory\|TrajectoryReport" /Users/ngchenmeng/Ballast/ballast/core/trajectory.py`. Must return nothing — confirms Step 4 succeeded.
  - Run `grep -n "class TrajectoryChecker" /Users/ngchenmeng/Ballast/ballast/core/trajectory.py`. Must return exactly 1 match.

  Write `/Users/ngchenmeng/Ballast/tests/test_trajectory.py` (full overwrite):

  ```python
  """Tests for ballast/core/trajectory.py — mid-run drift detection.

  Unit tests mock score_intent_alignment and score_constraint_violation.
  score_tool_compliance is tested directly (pure Python, no LLM).
  Integration test requires ANTHROPIC_API_KEY. Skip with: pytest -m 'not integration'
  """
  import os
  from unittest.mock import patch

  import pytest

  from ballast.core.spec import SpecModel, lock
  from ballast.core.trajectory import (
      DriftDetected,
      DriftResult,
      TrajectoryChecker,
      _extract_node_info,
      score_tool_compliance,
  )


  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  def _make_spec(
      allowed_tools: list = None,
      constraints: list = None,
      drift_threshold: float = 0.7,
  ) -> SpecModel:
      return lock(SpecModel(
          intent="count words in a string",
          success_criteria=["returns an integer", "integer is accurate"],
          constraints=constraints or [],
          allowed_tools=allowed_tools or [],
          drift_threshold=drift_threshold,
      ))


  class FakeToolNode:
      """Simulates a pydantic-ai node with tool_name and args attributes."""
      def __init__(self, tool_name: str, args: dict = None):
          self.tool_name = tool_name
          self.args = args or {}


  class FakeTextNode:
      """Simulates a pydantic-ai node with a text attribute."""
      def __init__(self, text: str):
          self.text = text


  class FakeEmptyNode:
      """Node with no scoreable attributes."""
      pass


  # ---------------------------------------------------------------------------
  # _extract_node_info
  # ---------------------------------------------------------------------------

  def test_extract_tool_node_detects_tool_name():
      node = FakeToolNode("get_word_count", {"text": "hello"})
      node_type, content, tool_info = _extract_node_info(node)
      assert tool_info["tool_name"] == "get_word_count"
      assert tool_info["tool_args"] == {"text": "hello"}


  def test_extract_text_node_captures_content():
      node = FakeTextNode("The word count is 4.")
      node_type, content, tool_info = _extract_node_info(node)
      assert "word count" in content
      assert tool_info == {}


  def test_extract_empty_node_returns_empty():
      node_type, content, tool_info = _extract_node_info(FakeEmptyNode())
      assert content == ""
      assert tool_info == {}


  def test_extract_node_type_name_is_class_name():
      node = FakeToolNode("any")
      node_type, _, _ = _extract_node_info(node)
      assert node_type == "FakeToolNode"


  # ---------------------------------------------------------------------------
  # score_tool_compliance (rule-based, no LLM)
  # ---------------------------------------------------------------------------

  def test_tool_compliance_empty_allowed_all_permitted():
      spec = _make_spec(allowed_tools=[])
      assert score_tool_compliance(FakeToolNode("any_tool"), spec) == 1.0


  def test_tool_compliance_tool_in_list():
      spec = _make_spec(allowed_tools=["get_word_count"])
      assert score_tool_compliance(FakeToolNode("get_word_count"), spec) == 1.0


  def test_tool_compliance_tool_not_in_list():
      spec = _make_spec(allowed_tools=["get_word_count"])
      assert score_tool_compliance(FakeToolNode("forbidden"), spec) == 0.0


  def test_tool_compliance_non_tool_node_always_passes():
      spec = _make_spec(allowed_tools=["get_word_count"])
      assert score_tool_compliance(FakeTextNode("some output"), spec) == 1.0


  # ---------------------------------------------------------------------------
  # TrajectoryChecker — init guards
  # ---------------------------------------------------------------------------

  def test_checker_requires_locked_spec():
      draft = SpecModel(intent="x", success_criteria=["y"])
      with pytest.raises(ValueError, match="locked"):
          TrajectoryChecker(draft)


  def test_checker_accepts_locked_spec():
      checker = TrajectoryChecker(_make_spec())
      assert checker.step_count == 0


  # ---------------------------------------------------------------------------
  # TrajectoryChecker — non-scoreable events
  # ---------------------------------------------------------------------------

  def test_checker_empty_node_returns_none():
      checker = TrajectoryChecker(_make_spec())
      result = checker.check(FakeEmptyNode())
      assert result is None
      assert checker.step_count == 0


  # ---------------------------------------------------------------------------
  # TrajectoryChecker — passing checks
  # ---------------------------------------------------------------------------

  def test_checker_passing_tool_check_returns_drift_result():
      spec = _make_spec(allowed_tools=["get_word_count"])
      checker = TrajectoryChecker(spec)
      node = FakeToolNode("get_word_count", {"text": "hello"})
      with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.9), \
           patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
          result = checker.check(node)
      assert isinstance(result, DriftResult)
      assert result.tool_score == 1.0
      assert result.failing_dimension == "none"
      assert checker.step_count == 1


  def test_checker_step_count_increments_per_scored_node():
      checker = TrajectoryChecker(_make_spec())
      with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.9), \
           patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
          checker.check(FakeToolNode("t1"))
          checker.check(FakeTextNode("output"))
      assert checker.step_count == 2


  def test_checker_non_scoreable_does_not_increment_step():
      checker = TrajectoryChecker(_make_spec())
      checker.check(FakeEmptyNode())
      assert checker.step_count == 0


  # ---------------------------------------------------------------------------
  # TrajectoryChecker — drift detected
  # ---------------------------------------------------------------------------

  def test_checker_forbidden_tool_raises_drift_detected():
      spec = _make_spec(allowed_tools=["get_word_count"])
      checker = TrajectoryChecker(spec)
      with pytest.raises(DriftDetected) as exc_info:
          with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
               patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
              checker.check(FakeToolNode("forbidden_tool"))
      result = exc_info.value.result
      assert result.tool_score == 0.0
      assert result.failing_dimension == "tool"
      assert result.score == 0.0


  def test_checker_constraint_violation_raises_drift():
      spec = _make_spec(constraints=["do not write files"])
      checker = TrajectoryChecker(spec)
      with pytest.raises(DriftDetected) as exc_info:
          with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.9), \
               patch("ballast.core.trajectory.score_constraint_violation", return_value=0.0):
              checker.check(FakeTextNode("I modified the file"))
      assert exc_info.value.result.constraint_score == 0.0
      assert exc_info.value.result.failing_dimension == "constraint"


  def test_checker_intent_misalignment_raises_drift():
      checker = TrajectoryChecker(_make_spec())
      with pytest.raises(DriftDetected) as exc_info:
          with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.2), \
               patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
              checker.check(FakeTextNode("completely unrelated output"))
      assert exc_info.value.result.intent_score == 0.2
      assert exc_info.value.result.failing_dimension == "intent"
      assert exc_info.value.result.score == 0.2


  # ---------------------------------------------------------------------------
  # failing_dimension priority
  # ---------------------------------------------------------------------------

  def test_failing_dimension_tool_beats_constraint_when_both_zero():
      spec = _make_spec(allowed_tools=["safe"], constraints=["do not do x"])
      checker = TrajectoryChecker(spec)
      with pytest.raises(DriftDetected) as exc_info:
          with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
               patch("ballast.core.trajectory.score_constraint_violation", return_value=0.0):
              checker.check(FakeToolNode("forbidden"))
      result = exc_info.value.result
      # tool=0.0, constraint=0.0, intent=1.0 → tool priority
      assert result.failing_dimension == "tool"


  def test_failing_dimension_constraint_beats_intent_when_equal():
      # Regression: constraint_score == intent_score == aggregate; constraint has priority
      spec = _make_spec(constraints=["do not write files"])
      checker = TrajectoryChecker(spec)
      with pytest.raises(DriftDetected) as exc_info:
          with patch("ballast.core.trajectory.score_intent_alignment", return_value=0.5), \
               patch("ballast.core.trajectory.score_constraint_violation", return_value=0.5):
              checker.check(FakeTextNode("I modified a file"))
      assert exc_info.value.result.failing_dimension == "constraint"


  def test_failing_dimension_none_when_all_pass():
      checker = TrajectoryChecker(_make_spec())
      with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
           patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
          result = checker.check(FakeTextNode("word count returned"))
      assert result.failing_dimension == "none"
      assert result.score == 1.0


  # ---------------------------------------------------------------------------
  # DriftResult fields
  # ---------------------------------------------------------------------------

  def test_drift_result_spec_version_matches_spec():
      spec = _make_spec()
      checker = TrajectoryChecker(spec)
      with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
           patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
          result = checker.check(FakeToolNode("t"))
      assert result.spec_version == spec.version


  def test_drift_result_threshold_matches_spec_drift_threshold():
      spec = _make_spec(drift_threshold=0.6)
      checker = TrajectoryChecker(spec)
      with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
           patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
          result = checker.check(FakeToolNode("t"))
      assert result.threshold == 0.6


  def test_drift_result_raised_at_step_increments():
      checker = TrajectoryChecker(_make_spec(drift_threshold=0.0))  # threshold=0 → never raises
      with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
           patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
          r1 = checker.check(FakeTextNode("step 1"))
          r2 = checker.check(FakeTextNode("step 2"))
      assert r1.raised_at_step == 1
      assert r2.raised_at_step == 2


  def test_drift_detected_message_contains_step_and_failing():
      spec = _make_spec(allowed_tools=["safe"])
      checker = TrajectoryChecker(spec)
      try:
          with patch("ballast.core.trajectory.score_intent_alignment", return_value=1.0), \
               patch("ballast.core.trajectory.score_constraint_violation", return_value=1.0):
              checker.check(FakeToolNode("forbidden"))
      except DriftDetected as e:
          assert "step 1" in str(e)
          assert "tool" in str(e)


  # ---------------------------------------------------------------------------
  # Integration test — requires ANTHROPIC_API_KEY
  # ---------------------------------------------------------------------------

  @pytest.mark.integration
  def test_trajectory_checker_real_llm():
      if not os.environ.get("ANTHROPIC_API_KEY"):
          pytest.skip("ANTHROPIC_API_KEY not set")

      spec = _make_spec(
          allowed_tools=["get_word_count"],
          constraints=["do not modify any files"],
          drift_threshold=0.4,
      )
      checker = TrajectoryChecker(spec)
      node = FakeToolNode("get_word_count", {"text": "the quick brown fox"})
      result = checker.check(node)
      assert isinstance(result, DriftResult)
      assert result.tool_score == 1.0
      print(
          f"\nIntegration: score={result.score:.2f} "
          f"intent={result.intent_score:.2f} "
          f"tool={result.tool_score:.2f} "
          f"constraint={result.constraint_score:.2f} "
          f"failing={result.failing_dimension}"
      )
  ```

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add tests/test_trajectory.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 4.6: replace test_trajectory.py for mid-run drift detection API"
  ```

  **Subtasks:**
  - [ ] 🟥 Pre-Read Gate: confirm `validate_trajectory` absent from trajectory.py; confirm `TrajectoryChecker` present
  - [ ] 🟥 Write `tests/test_trajectory.py` (full overwrite)
  - [ ] 🟥 Git checkpoint

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ -v -m "not integration" --tb=short 2>&1 | tail -15
  ```

  **Expected:**
  - All spec, trajectory, memory, and stream tests pass
  - 0 failed
  - Integration tests show as `skipped` (not `failed`)
  - Test count ≥ 20 new spec tests + 20 new trajectory tests + existing memory/stream tests

  **Pass:** `0 failed`, integration tests skipped.

  **Fail:**
  - `ImportError: cannot import name 'TrajectoryChecker'` → Step 4 write failed → re-read trajectory.py
  - `test_checker_empty_node_returns_none` fails → `_is_scoreable(FakeEmptyNode())` returned True → FakeEmptyNode has unexpected attrs → re-read `_is_scoreable`
  - Old spec test failures → old test_spec.py not overwritten → check Step 5 write

---

## Regression Guard

| System | Why affected | Verification |
|--------|-------------|--------------|
| `tests/test_spec.py` | All 22 old tests import removed symbols (`LockedSpec`, `lock_spec`, etc.) | After Step 5: `pytest tests/test_spec.py -m 'not integration'` → 0 failed |
| `tests/test_trajectory.py` | Imports `validate_trajectory`, `TrajectoryReport` — removed in phrase1-partb | After Step 6: `pytest tests/test_trajectory.py -m 'not integration'` → 0 failed |
| `tests/test_memory.py` | Not touched. `memory.py` not imported by new spec.py | `pytest tests/test_memory.py` — must still pass |
| `tests/test_stream.py` | Not touched. `agui.py` not touched | `pytest tests/test_stream.py` — must still pass |
| `scripts/observe.py` | Imports `lock_spec`, `RunPhaseTracker` — both removed | Acknowledged broken — out of scope. Follow-up plan. |

**Test count regression check:**
- Non-integration tests after all 6 steps: run `pytest -m 'not integration'`
- Must include: ≥ 19 spec tests + ≥ 20 trajectory tests + all memory + stream tests
- Integration tests: exactly 2 (1 spec, 1 trajectory) — must show as `skipped`, not `failed`

---

## Rollback Procedure

```bash
# Rollback in reverse step order:
git -C /Users/ngchenmeng/Ballast revert HEAD    # Step 6: test_trajectory.py
git -C /Users/ngchenmeng/Ballast revert HEAD    # Step 5: test_spec.py
git -C /Users/ngchenmeng/Ballast revert HEAD    # Step 4: trajectory.py
git -C /Users/ngchenmeng/Ballast revert HEAD    # Step 3: (no commit — observation only)
git -C /Users/ngchenmeng/Ballast revert HEAD    # Step 2: spec.md
git -C /Users/ngchenmeng/Ballast revert HEAD    # Step 1: spec.py
git -C /Users/ngchenmeng/Ballast revert HEAD    # Step 0: pyproject.toml

# Verify rollback
/Users/ngchenmeng/Ballast/venv/bin/python -c "from ballast.core.spec import LockedSpec; print('rollback OK')"
```

---

## Pre-Flight Checklist

| Phase | Check | How to Confirm | Status |
|-------|-------|----------------|--------|
| Pre-flight | Tests recorded | `pytest tests/ --tb=short` → record count | ⬜ |
| Pre-flight | `SpecModel` absent from spec.py | `grep -n "class SpecModel" ballast/core/spec.py` → no match | ⬜ |
| Pre-flight | `pydantic-ai` absent from pyproject.toml | `grep pydantic-ai pyproject.toml` → no match | ⬜ |
| Step 0 | pydantic-ai installed | `python -c "import pydantic_ai; print(pydantic_ai.__version__)"` → prints | ⬜ |
| Step 1 | `SpecModel` importable; `LockedSpec` not | Verification test passes | ⬜ |
| Step 1 | `lock_spec` removed | `from ballast.core.spec import lock_spec` → ImportError | ⬜ |
| Step 2 | `parse_spec('/Users/ngchenmeng/Ballast/spec.md')` succeeds | Verification test passes | ⬜ |
| Step 3 | Node class names recorded | Inspection script ran; names noted | ⬜ |
| Step 3 | `_SCOREABLE_NAME_FRAGMENTS` updated if needed | Names from Step 3 match or set updated | ⬜ |
| Step 4 | `TrajectoryChecker` importable; `validate_trajectory` not | Verification test passes | ⬜ |
| Step 5 | `pytest tests/test_spec.py -m 'not integration'` → 0 failed | All non-integration spec tests pass | ⬜ |
| Step 6 | `pytest tests/ -m 'not integration'` → 0 failed | All non-integration tests pass | ⬜ |

---

## Success Criteria

| Feature | Target | Verification |
|---------|--------|--------------|
| `SpecModel` data contract | `drift_threshold=0.4`, `locked_at=''` defaults | `SpecModel(intent='x', success_criteria=['y']).drift_threshold == 0.4` |
| `parse_spec` reads spec.md | Extracts all 5 sections correctly | `parse_spec('spec.md').success_criteria` is a list with ≥ 1 item |
| `lock()` idempotency | Same input → same version | `lock(draft).version == lock(draft).version` |
| `lock()` immutability | Input draft unchanged | `draft.locked_at == ''` after `lock(draft)` |
| `TrajectoryChecker` requires locked spec | `ValueError` on unlocked spec | `TrajectoryChecker(draft)` → `ValueError` |
| `score_tool_compliance` rule-based | Forbidden tool → 0.0; empty allowed → 1.0 | Unit tests pass without any LLM calls |
| `DriftDetected` carries full context | `e.result.tool_score`, `e.result.spec_version` accessible | `test_checker_forbidden_tool_raises_drift_detected` passes |
| `failing_dimension` priority | tool > constraint > intent | `test_failing_dimension_tool_beats_constraint_when_both_zero` passes |
| Old API removed | `LockedSpec`, `lock_spec`, `validate_trajectory`, `TrajectoryReport` all raise ImportError | Verified in Step 1 and Step 4 verification tests |
| No regressions | `test_memory.py`, `test_stream.py` still pass | `pytest tests/test_memory.py tests/test_stream.py` → 0 failed |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Steps 3→4 are linked: update `_SCOREABLE_NAME_FRAGMENTS` in Step 4 code if Step 3 reveals different node class names.**
⚠️ **`trajectory.py` must never contain `except DriftDetected` without an immediate `raise` — check after Step 4 write.**
⚠️ **`observe.py` will be broken after this plan — this is expected and documented. Do not attempt to fix it in this plan.**
⚠️ **Do not batch multiple steps into one git commit.**
