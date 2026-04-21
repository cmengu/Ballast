# Feature Implementation Plan: Fix trajectory.py — Full Node-Boundary Orchestration

**Overall Progress:** `0% (0/5 steps done)`

---

## TLDR

`trajectory.py`'s `run_with_spec` is a simplified loop that only scores drift — it has no spec polling, no checkpoint, no context window, no label system, and no wiring points for Layer 2. The project overview specifies a 7-step node-boundary orchestration. This plan creates the missing `checkpoint.py` dependency (build step 2), adds `score_drift()` + `_compact_node()` + label type to `trajectory.py` (Layer 1 cascade interface), refactors `run_with_spec()` to the full 7-step loop with stubs for unbuilt layers, and adds covering tests. After this plan executes, `trajectory.py` implements the full orchestration loop described in `projet-overview.md` through build step 4, with stub placeholders for build steps 7–13.

---

## Architecture Overview

**The problem this plan solves:**
`ballast/core/trajectory.py:run_with_spec` (line 507) is a simplified loop: it iterates nodes, calls `TrajectoryChecker.check()`, and re-raises `DriftDetected`. It has no spec polling, no checkpoint writing, no label system (PROGRESSING/STALLED/VIOLATED/VIOLATED_IRREVERSIBLE), no context window management, and no injection path for corrections or escalations. The project spec requires a 7-step synchronisation point at every node boundary. `ballast/core/checkpoint.py` does not exist, blocking the checkpoint step.

**The patterns applied:**
- **Coordinator** (`run_with_spec`): owns the full orchestration sequence; all 7 steps delegate to separate modules. Violation: inlining any step's logic breaks the one-responsibility boundary.
- **DTO** (`NodeSummary`, `BallastProgress`): data flows from the orchestration loop into the checkpoint file and future dashboard. No behaviour, only structure.
- **Open/Closed** (stub comments): wiring points for Layer 2/3 are marked with `# TODO StepN` comments. Adding evaluator.py at Step 10 means only those stub lines change — not the orchestration skeleton.
- **Facade** (`score_drift`): single function wraps three scorers + irreversibility check into a `(score, label, rationale)` tuple. Callers never reference individual scorers.

**What stays unchanged:**
- `ballast/core/spec.py` — complete, no changes needed.
- `ballast/core/sync.py` — complete, no changes needed.
- `ballast/core/hook.py` — stays as the simpler "spec propagation only" entrypoint (no drift scoring). trajectory.py does NOT import from hook.py to avoid circular coupling; both import `ModelRequest`/`UserPromptPart` from `pydantic_ai.messages` directly.
- `TrajectoryChecker` class and all tests targeting it — preserved exactly. The class remains the single-node, fixed-spec scoring API.

**What this plan adds:**
- `ballast/core/checkpoint.py` — `NodeSummary` (per-node audit stamp) + `BallastProgress` (full run state + resume logic) + read/write to `ballast-progress.json`.
- `trajectory.py`: `DriftLabel` type alias, `score_drift()` (Layer 1 cascade → label), `_compact_node()` (node → compact dict for context window history).
- `trajectory.py`: refactored `run_with_spec(agent, task, spec, poller=None)` — 7-step orchestration loop.
- `tests/test_checkpoint.py` — checkpoint round-trip + resume_context tests.
- New tests appended to `tests/test_trajectory.py` — `score_drift()` + `run_with_spec()` with mock poller.

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|----------|----------------------|--------------------------|
| `run_with_spec` accepts `poller: SpecPoller \| None = None` | Require poller always | Breaks existing callers; optional keeps backward compatibility |
| Inject via `agent_run.ctx.state.message_history` (same as hook.py) | Extract shared helper into utils.py | Unnecessary abstraction for two files; both import from pydantic_ai.messages directly |
| `score_drift` calls existing LLM scorers (Layer 1 interim) | Defer LLM scoring entirely to evaluator.py stub | Loses drift scoring until Step 10; existing scorers are functional and provide real signal |
| Layer 2/3 stubs are TODO comments, not function calls | Import stub modules | Nonexistent modules cause ImportError; comments are zero-risk and unambiguously labelled with step numbers |
| `TrajectoryChecker` unchanged | Refactor to accept live spec updates | 17 tests target it; changing it risks breakage; `run_with_spec` uses `score_drift()` directly for the live-spec case |

**Known limitations acknowledged:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| Layer 2 evaluator stubbed | evaluator.py is build step 8 | Step 10: replace `# TODO Step 10` stubs in `run_with_spec` |
| Environment probe stubbed | probe.py is build step 9 | Step 9: replace `# TODO Step 9` stub |
| Escalation stubbed | escalation.py is build step 7 | Step 7: replace `# TODO Step 7` stub |
| OTel emission stubbed | adapters/otel.py is build step 13 | Step 13: replace `# TODO Step 13` stub |
| Correction is plain text injection | guardrails.py (step 6) has richer logic | Step 6: replace inline correction string with `build_correction()` call |

---

## Decisions Log (resolved pre-check flaws)

| Flaw | Resolution applied |
|------|-------------------|
| `MagicMock` not imported in Step 5 test additions | Step 5 appended imports changed to `from unittest.mock import MagicMock, patch`; existing file top-level import updated to match |
| Output extraction returns wrong type (`result.data` auto-exists on MagicMock, never uses default) | `_MockAgentRun` in Step 5 gets an explicit `async def get_output(self)` method so the `hasattr(agent_run, "get_output")` branch fires correctly |
| Step 2 import edits were two separate operations on same block (race with anchor drift) | Consolidated into one single replacement of the entire import block (lines 22–31), showing exact old → exact new |
| trajectory.py module docstring becomes stale after Step 3 | Step 3 includes docstring update as first sub-edit |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---------|----------|--------|----------|----------|
| `agent_run.ctx` injection path valid in installed pydantic-ai | Confirm `agent_run.ctx.state.message_history` exists at runtime | hook.py uses it; all 8 hook tests pass | Step 3 | ✅ |
| `run_with_spec` existing callers | Are there callers outside tests? | grep confirmed none | Step 3 | ✅ |

---

## Agent Failure Protocol

1. A verification command fails → read the full error output.
2. Cause is unambiguous → make ONE targeted fix → re-run the same verification.
3. If still failing after one fix → **STOP**. Output full contents of every modified file in this step. Report: (a) command run, (b) full error verbatim, (c) fix attempted, (d) current state of each modified file, (e) why you cannot proceed.
4. Never attempt a second fix without human instruction.
5. Never modify files not named in the current step.

---

## Pre-Flight — Run Before Any Code Changes

```bash
# Run in order. Record all output.
cd /Users/ngchenmeng/Ballast

# (1) Existing test suite baseline
python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -5

# (2) Confirm run_with_spec current signature (must show exactly 1 result)
grep -n 'async def run_with_spec' ballast/core/trajectory.py

# (3) Confirm no run_with_spec callers outside tests
grep -rn 'run_with_spec' ballast/ tests/

# (4) Confirm checkpoint.py does not exist
ls ballast/core/checkpoint.py 2>&1

# (5) Confirm exact current import block in trajectory.py (lines 22-31)
sed -n '22,31p' ballast/core/trajectory.py

# (6) Confirm exact top-level import line in test_trajectory.py
head -10 tests/test_trajectory.py

# (7) Line counts for regression check
wc -l ballast/core/trajectory.py tests/test_trajectory.py
```

**Baseline Snapshot (agent fills during pre-flight — do not pre-fill):**
```
Test count before plan:          ____
trajectory.py line count:        ____
test_trajectory.py line count:   ____
run_with_spec at line:           ____
checkpoint.py exists:            ____ (expected: No such file)
```

**Automated checks (all must pass before Step 1):**
- [ ] Existing tests pass. Count: `____`
- [ ] `async def run_with_spec(agent: Agent, task: str, spec: SpecModel) -> Any:` appears exactly once.
- [ ] `ballast/core/checkpoint.py` does not exist.
- [ ] trajectory.py lines 22–31 match exactly:
  ```
  from __future__ import annotations
  
  import logging
  from typing import Any, Optional
  
  import anthropic
  from pydantic import BaseModel, Field
  from pydantic_ai import Agent
  
  from ballast.core.spec import SpecModel, is_locked
  ```
- [ ] test_trajectory.py line 8 is `from unittest.mock import patch` (only `patch`, no `MagicMock`).

---

## Steps Analysis

```
Step 1 (Create checkpoint.py)                      — Critical   — full code review — Idempotent: Yes
Step 2 (Add score_drift + _compact_node + imports) — Critical   — full code review — Idempotent: Yes
Step 3 (Refactor run_with_spec)                    — Critical   — full code review — Idempotent: Yes
Step 4 (Create tests/test_checkpoint.py)           — Non-critical — verification  — Idempotent: Yes
Step 5 (Append tests to test_trajectory.py)        — Non-critical — verification  — Idempotent: Yes
```

---

## Tasks

### Phase 1 — Checkpoint foundation

**Goal:** `ballast/core/checkpoint.py` exists and is importable. trajectory.py can import `BallastProgress` and `NodeSummary` without error.

---

- [ ] 🟥 **Step 1: Create `ballast/core/checkpoint.py`** — *Critical: trajectory.py Step 3 imports from it*

  **Step Architecture Thinking:**

  **Pattern applied:** DTO (Data Transfer Object). `NodeSummary` and `BallastProgress` are pure data containers with no business logic — no drift scoring, no LLM calls, no spec knowledge. Serialisation and deserialisation are the only behaviours.

  **Why this step exists here in the sequence:**
  `checkpoint.py` is build step 2 in the project sequence. Step 3 imports `BallastProgress` and `NodeSummary` from it; the import will fail if this file does not exist first.

  **Why this file is the right location:**
  `ballast/core/` is the kernel. Checkpoint state is core infrastructure used by trajectory.py, future dashboard.py, and future resume logic. It does not belong in adapters/.

  **Alternative approach considered and rejected:**
  Inline `NodeSummary` and `BallastProgress` inside trajectory.py. Rejected: dashboard.py (step 11) and the smolagents adapter (step 12) both need to read checkpoint state — a shared module is required.

  **What breaks if this step deviates:**
  If `NodeSummary.spec_hash` is omitted or renamed, the training dataset loses the per-node spec stamp — the core architectural invariant (project overview invariant 4). All downstream consumers would silently lose this field.

  ---

  **Idempotent:** Yes — new file. Pre-flight confirmed it does not exist.

  **Pre-Read Gate:**
  - `ls ballast/core/checkpoint.py` must return `No such file`. If it returns a path → STOP.
  - `ls ballast/core/__init__.py` must succeed. If not → STOP (package missing).

  **Self-Contained Rule:** All code below is complete and runnable.

  **No-Placeholder Rule:** No `<VALUE>` tokens.

  ```python
  """ballast/core/checkpoint.py — Per-run audit state and resume context.

  Public interface:
      NodeSummary     — per-node audit stamp: label, score, cost, spec_hash, timestamp
      BallastProgress — full run state: dispatch hash, active hash, transitions, summaries
      CHECKPOINT_FILE — default path for ballast-progress.json

  Invariant: NodeSummary.spec_hash is the version_hash of the spec active when
  that node executed — not the spec at job dispatch. This per-node stamp is the
  training dataset audit trail (projet-overview.md invariant 4).
  """
  from __future__ import annotations

  import json
  from dataclasses import asdict, dataclass, field
  from datetime import datetime, timezone
  from pathlib import Path

  CHECKPOINT_FILE = "ballast-progress.json"


  @dataclass
  class NodeSummary:
      """Audit record for one Agent.iter node.

      spec_hash: version_hash of the spec that was ACTIVE when this node executed.
      This may differ from BallastProgress.spec_hash (dispatch hash) if a live
      spec update arrived mid-run.
      """
      index: int
      tool_name: str
      label: str                  # PROGRESSING | STALLED | VIOLATED | VIOLATED_IRREVERSIBLE
      drift_score: float
      cost_usd: float
      verified: bool              # True if environment probe confirmed the claim
      spec_hash: str              # spec version active at this node — per-node audit stamp
      timestamp: str              # ISO-8601 UTC


  @dataclass
  class BallastProgress:
      """Full run state. Written to ballast-progress.json at every checkpoint.

      spec_hash:        version_hash at job dispatch (never changes during run).
      active_spec_hash: version_hash currently active (updates on live spec change).
      spec_transitions: ordered log of live spec updates seen during this run.
      """
      spec_hash: str
      active_spec_hash: str = ""
      spec_intent: str = ""
      run_id: str = ""
      started_at: str = ""
      updated_at: str = ""
      last_clean_node_index: int = -1
      completed_node_summaries: list = field(default_factory=list)
      spec_transitions: list = field(default_factory=list)
      total_cost_usd: float = 0.0
      total_drift_events: int = 0
      total_violations: int = 0
      remaining_success_criteria: list = field(default_factory=list)
      last_escalation: str | None = None
      is_complete: bool = False

      def __post_init__(self) -> None:
          if not self.active_spec_hash:
              self.active_spec_hash = self.spec_hash

      def write(self, path: str = CHECKPOINT_FILE) -> None:
          """Serialise to JSON. NodeSummary objects are converted via asdict."""
          data = asdict(self)
          Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

      @classmethod
      def read(cls, path: str = CHECKPOINT_FILE) -> "BallastProgress | None":
          """Deserialise from JSON. Returns None if file does not exist."""
          p = Path(path)
          if not p.exists():
              return None
          data = json.loads(p.read_text(encoding="utf-8"))
          data["completed_node_summaries"] = [
              NodeSummary(**n) for n in data["completed_node_summaries"]
          ]
          return cls(**data)

      def resume_context(self) -> str:
          """Plain-text summary prepended to the task string on resume."""
          completed = len(self.completed_node_summaries)
          last = (
              self.completed_node_summaries[-1]
              if self.completed_node_summaries
              else None
          )
          last_action = f"{last.tool_name} → {last.label}" if last else "none"
          remaining = "\n".join(f"- {c}" for c in self.remaining_success_criteria)
          return (
              f"[BALLAST RESUME CONTEXT]\n"
              f"Spec at dispatch:  {self.spec_hash[:8]}\n"
              f"Active spec now:   {self.active_spec_hash[:8]}\n"
              f"Spec updates seen: {len(self.spec_transitions)}\n"
              f"Intent: {self.spec_intent}\n"
              f"Progress: {completed} nodes completed\n"
              f"Last clean node: #{self.last_clean_node_index} ({last_action})\n"
              f"Drift events: {self.total_drift_events} | "
              f"Violations: {self.total_violations}\n"
              f"Cost so far: ${self.total_cost_usd:.4f}\n"
              f"Remaining success criteria:\n{remaining}\n"
              f"Resume from node #{self.last_clean_node_index + 1}.\n"
              f"Do not repeat completed work.\n"
              f"[END RESUME CONTEXT]"
          )
  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/core/checkpoint.py
  git commit -m "step 1: add checkpoint.py — NodeSummary + BallastProgress + read/write/resume"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && python -c "
  from ballast.core.checkpoint import BallastProgress, NodeSummary, CHECKPOINT_FILE
  p = BallastProgress(spec_hash='abc123', spec_intent='test', run_id='r1',
      started_at='2026-01-01T00:00:00Z', updated_at='2026-01-01T00:00:00Z')
  p.completed_node_summaries.append(NodeSummary(
      index=0, tool_name='read_file', label='PROGRESSING',
      drift_score=0.9, cost_usd=0.001, verified=True,
      spec_hash='abc123', timestamp='2026-01-01T00:00:01Z'))
  p.write('/tmp/test-ballast-progress.json')
  p2 = BallastProgress.read('/tmp/test-ballast-progress.json')
  assert p2.spec_hash == 'abc123'
  assert p2.active_spec_hash == 'abc123'
  assert isinstance(p2.completed_node_summaries[0], NodeSummary)
  assert p2.completed_node_summaries[0].spec_hash == 'abc123'
  ctx = p2.resume_context()
  assert 'BALLAST RESUME CONTEXT' in ctx
  assert 'abc123'[:8] in ctx
  print('checkpoint.py OK')
  "
  ```

  **Pass:** Prints `checkpoint.py OK` with no exceptions.

  **Fail:**
  - `ModuleNotFoundError` → file not in correct location → confirm `ballast/core/checkpoint.py` exists.
  - `TypeError: __init__() got unexpected keyword argument` → field name mismatch → check `asdict` output keys match dataclass field names exactly.

---

### Phase 2 — Extend trajectory.py

**Goal:** `trajectory.py` exposes `score_drift()`, `_compact_node()`, and a `DriftLabel` type. `run_with_spec()` accepts an optional `SpecPoller` and executes the 7-step node-boundary loop.

---

- [ ] 🟥 **Step 2: Replace trajectory.py import block + add `DriftLabel`, `score_drift()`, `_compact_node()`** — *Critical: run_with_spec (Step 3) calls these*

  **Step Architecture Thinking:**

  **Pattern applied:** Facade. `score_drift()` is the single Layer 1 interface. The cascade logic (irreversibility → tool → LLM) is encapsulated here. Callers never reference the three individual scorers directly.

  **Why this step exists here in the sequence:**
  Step 3's `run_with_spec` calls `score_drift()` and `_compact_node()`. Both must exist before Step 3 adds the function body. Step 1 (checkpoint.py) must exist before this step adds the import.

  **Why this file is the right location:**
  `score_drift` wraps the three scorers that live in trajectory.py — no cross-file import needed. `_compact_node` is a private helper with no consumers outside this file.

  **Alternative approach considered and rejected:**
  Add `score_drift()` to a new `ballast/core/cascade.py`. Rejected: it would import from trajectory.py (the scorers) and be imported by trajectory.py (in run_with_spec), creating a circular dependency.

  **What breaks if this step deviates:**
  If `score_drift` does not check `spec.irreversible_actions` before calling LLM scorers, an irreversible tool call is scored by the LLM instead of being hard-stopped — the `VIOLATED_IRREVERSIBLE` label never fires and the training dataset loses this label class.

  ---

  **Idempotent:** Yes — new functions added to file. Pre-Read Gate confirms their absence.

  **Pre-Read Gate:**
  Before any edit:
  - `grep -n 'def score_drift' ballast/core/trajectory.py` must return 0 matches. If 1+ → STOP.
  - `grep -n 'def _compact_node' ballast/core/trajectory.py` must return 0 matches. If 1+ → STOP.
  - `grep -n 'DriftLabel' ballast/core/trajectory.py` must return 0 matches. If 1+ → STOP.
  - `grep -n 'from ballast.core.checkpoint' ballast/core/trajectory.py` must return 0 matches. If 1+ → STOP.
  - `grep -c 'def score_intent_alignment' ballast/core/trajectory.py` must return `1` (anchor exists). If 0 → STOP.

  **Edit A — Replace the entire import block (lines 22–31).**

  Confirm anchor uniqueness: `grep -c 'from typing import Any, Optional' ballast/core/trajectory.py` must return `1`. If not → STOP.

  Replace:
  ```python
  from __future__ import annotations

  import logging
  from typing import Any, Optional

  import anthropic
  from pydantic import BaseModel, Field
  from pydantic_ai import Agent

  from ballast.core.spec import SpecModel, is_locked
  ```

  With:
  ```python
  from __future__ import annotations

  import logging
  import uuid
  from datetime import datetime, timezone
  from typing import Any, Literal, Optional

  import anthropic
  from pydantic import BaseModel, Field
  from pydantic_ai import Agent
  from pydantic_ai.messages import ModelRequest, UserPromptPart

  from ballast.core.checkpoint import BallastProgress, NodeSummary
  from ballast.core.spec import SpecModel, is_locked
  from ballast.core.sync import SpecPoller
  ```

  **Edit B — Insert new functions after `score_intent_alignment` ends, before `_SCOREABLE_NAME_FRAGMENTS`.**

  Confirm anchor: `grep -n '_SCOREABLE_NAME_FRAGMENTS' ballast/core/trajectory.py` must return exactly 1 match. The new block goes immediately before that line.

  Insert:
  ```python
  # ---------------------------------------------------------------------------
  # DriftLabel — the cascade label system
  # ---------------------------------------------------------------------------

  DriftLabel = Literal["PROGRESSING", "STALLED", "VIOLATED", "VIOLATED_IRREVERSIBLE"]


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
      _, content, tool_info = _extract_node_info(node)
      tool_name = tool_info.get("tool_name", "")

      # ── Heuristic gate ────────────────────────────────────────────────────
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


  def _compact_node(
      node: Any,
      score: float,
      label: str,
      cost_usd: float,
      verified: bool,
  ) -> dict:
      """Compact an evicted node to a summary dict for compact_history.

      compact_history is the portion of the context window beyond full_window.
      Passed as context to the Layer 2 evaluator (Step 10) and escalation (Step 7).
      """
      _, content, tool_info = _extract_node_info(node)
      return {
          "tool_name": tool_info.get("tool_name", ""),
          "label": label,
          "score": round(score, 3),
          "cost_usd": cost_usd,
          "verified": verified,
          "summary": content[:200],
      }


  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/core/trajectory.py
  git commit -m "step 2: add score_drift, _compact_node, DriftLabel + expand imports"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && python -c "
  from unittest.mock import patch
  from ballast.core.spec import SpecModel, lock
  from ballast.core.trajectory import score_drift, _compact_node, DriftLabel

  spec = lock(SpecModel(
      intent='count words',
      success_criteria=['returns integer'],
      irreversible_actions=['send_email'],
      allowed_tools=['read_file'],
      drift_threshold=0.4,
  ))

  class FakeTool:
      def __init__(self, name): self.tool_name = name; self.args = {}
  class FakeText:
      def __init__(self, t): self.text = t

  # irreversible → VIOLATED_IRREVERSIBLE
  s, lbl, _ = score_drift(FakeTool('send_email'), [], spec)
  assert lbl == 'VIOLATED_IRREVERSIBLE' and s == 0.0, f'expected VIOLATED_IRREVERSIBLE, got {lbl}'

  # forbidden tool → VIOLATED
  s, lbl, _ = score_drift(FakeTool('forbidden'), [], spec)
  assert lbl == 'VIOLATED' and s == 0.0, f'expected VIOLATED, got {lbl}'

  # clean node → PROGRESSING
  with patch('ballast.core.trajectory.score_constraint_violation', return_value=1.0), \
       patch('ballast.core.trajectory.score_intent_alignment', return_value=0.9):
      s, lbl, rationale = score_drift(FakeText('good output'), [], spec)
  assert lbl == 'PROGRESSING', f'expected PROGRESSING, got {lbl}'
  assert s == 0.9

  # borderline → STALLED
  with patch('ballast.core.trajectory.score_constraint_violation', return_value=0.6), \
       patch('ballast.core.trajectory.score_intent_alignment', return_value=0.6):
      s, lbl, _ = score_drift(FakeText('unclear'), [], spec)
  assert lbl == 'STALLED', f'expected STALLED, got {lbl}'

  # _compact_node structure
  compact = _compact_node(FakeText('output'), 0.9, 'PROGRESSING', 0.001, True)
  assert set(compact.keys()) == {'tool_name','label','score','cost_usd','verified','summary'}

  print('Step 2 OK')
  "
  ```

  **Pass:** Prints `Step 2 OK` with no exceptions.

  **Fail:**
  - `ImportError: cannot import name 'score_drift'` → function not inserted or syntax error → read the inserted block in trajectory.py.
  - `ImportError: cannot import name 'BallastProgress'` → Edit A not applied or checkpoint.py missing → check Step 1 and the import block.
  - `AssertionError` on PROGRESSING test → mock patch path wrong → confirm the path `ballast.core.trajectory.score_constraint_violation` matches the module where the function is defined.

---

- [ ] 🟥 **Step 3: Refactor `run_with_spec()` + update module docstring in `trajectory.py`** — *Critical: main orchestration entry point*

  **Step Architecture Thinking:**

  **Pattern applied:** Coordinator. `run_with_spec` owns the sequence; all steps delegate to separate units. The function does no scoring logic itself — it only orchestrates.

  **Why this step exists here in the sequence:**
  Steps 1 and 2 must complete first. `run_with_spec` imports `BallastProgress`, `NodeSummary` (Step 1) and calls `score_drift()`, `_compact_node()` (Step 2). Both must exist before this function body references them.

  **Why this file is the right location:**
  `trajectory.py` is the node-boundary orchestration module per the project spec. The 7-step loop belongs here.

  **Alternative approach considered and rejected:**
  Compose `run_with_spec` by calling `run_with_live_spec` from hook.py and wrapping its `on_node` callback. Rejected: creates tight coupling between modules; checkpoint writing is awkward inside a callback (no access to `progress` or `full_window`).

  **What breaks if this step deviates:**
  If `NodeSummary.spec_hash` is set to `spec.version_hash` (dispatch hash) instead of `active_spec.version_hash` (current active hash), every node in the training dataset after a live update is stamped with the wrong spec version — the core audit invariant is silently violated.

  ---

  **Idempotent:** Yes — replaces the existing `run_with_spec` function and module docstring in full.

  **Pre-Read Gate:**
  - `grep -n 'async def run_with_spec' ballast/core/trajectory.py` must return exactly 1 match. If 0 or 2+ → STOP.
  - `grep -n 'def score_drift' ballast/core/trajectory.py` must return exactly 1 match (Step 2 complete). If 0 → STOP.
  - `grep -n 'from ballast.core.checkpoint import' ballast/core/trajectory.py` must return exactly 1 match (Step 2 complete). If 0 → STOP.

  **Edit A — Replace the module docstring (lines 1–21).**

  Confirm anchor uniqueness: `grep -c 'trajectory.py detects and reports' ballast/core/trajectory.py` must return `1`. If not → STOP.

  Replace:
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
  ```

  With:
  ```python
  """ballast/core/trajectory.py — Node-boundary orchestration and drift detection.

  Public interface:
      run_with_spec(agent, task, spec, poller=None)
                        — 7-step orchestration loop; spec poll, drift score,
                          context window, checkpoint, correction inject.
      score_drift(node, full_window, spec)
                        — Layer 1 cascade: returns (score, label, rationale).
      TrajectoryChecker — single-node, fixed-spec drift scorer (simpler API).
      DriftResult       — scored assessment of one node (used by TrajectoryChecker).
      DriftDetected     — raised by TrajectoryChecker.check() when score < threshold.

  Score dimensions (aggregate = min of all three):
      score_tool_compliance      — rule-based: is tool in allowed_tools?
      score_constraint_violation — LLM: did action breach a hard constraint?
      score_intent_alignment     — LLM: is action moving toward the goal?

  Threshold: spec.drift_threshold (travels with the spec — invariant 2).
  Interception: pydantic-ai Agent.iter node boundaries (duck-typed for version resilience).
  """
  ```

  **Edit B — Replace the entire `run_with_spec` function.**

  Replacement target: from `async def run_with_spec(agent: Agent, task: str, spec: SpecModel) -> Any:` through its final `return None` line. Replace in full.

  ```python
  async def run_with_spec(
      agent: Agent,
      task: str,
      spec: SpecModel,
      poller: Optional[SpecPoller] = None,
  ) -> Any:
      """Full 7-step node-boundary orchestration loop.

      At every node boundary:
          1. Poll M5 for spec update → inject SpecDelta if version changed
          2. Cascade drift score (Layer 1; Layer 2 stubbed until Step 10)
          3. Environment probe (stubbed until Step 9)
          4. Drift response — inject correction or log escalation
          5. Context window management (full_window + compact_history)
          6. Checkpoint write every checkpoint_every_n_nodes nodes
          7. OTel emit (stubbed until Step 13)

      Args:
          agent:   pydantic-ai Agent instance.
          task:    Task string to run.
          spec:    Locked SpecModel — is_locked(spec) must be True.
          poller:  Optional SpecPoller. If None, spec stays fixed for the run.

      Returns:
          Final agent output.

      Raises:
          ValueError if spec is not locked.
      """
      if not is_locked(spec):
          raise ValueError(
              "spec must be locked before executing. Call lock(spec) first."
          )

      run_id = str(uuid.uuid4())[:8]
      active_spec = spec

      # Resume from checkpoint if available
      progress = BallastProgress.read()
      if (
          progress
          and progress.spec_hash == spec.version_hash
          and not progress.is_complete
      ):
          task = f"{progress.resume_context()}\n\nOriginal task: {task}"
          node_offset = progress.last_clean_node_index + 1
          logger.info(
              "run_with_spec resuming run_id=%s from node=%d spec_version=%s",
              progress.run_id, node_offset, spec.version_hash,
          )
      else:
          progress = BallastProgress(
              spec_hash=spec.version_hash,
              active_spec_hash=spec.version_hash,
              spec_intent=spec.intent,
              run_id=run_id,
              started_at=datetime.now(timezone.utc).isoformat(),
              updated_at=datetime.now(timezone.utc).isoformat(),
              remaining_success_criteria=list(spec.success_criteria),
          )
          node_offset = 0

      full_window: list = []
      compact_history: list[dict] = []
      node_index = node_offset

      async with agent.iter(task) as agent_run:
          async for node in agent_run:

              # ── 1. Poll for spec update ─────────────────────────────────
              if poller and node_index % active_spec.harness.spec_poll_interval_nodes == 0:
                  new_spec = poller.poll()
                  if new_spec:
                      delta = active_spec.diff(new_spec)
                      active_spec = new_spec
                      agent_run.ctx.state.message_history.append(
                          ModelRequest(parts=[UserPromptPart(content=delta.as_injection())])
                      )
                      progress.active_spec_hash = active_spec.version_hash
                      progress.spec_transitions.append({
                          "at_node": node_index,
                          "from_hash": delta.from_hash,
                          "to_hash": delta.to_hash,
                      })
                      logger.info(
                          "spec_updated from=%s to=%s at_node=%d run_id=%s",
                          delta.from_hash[:8], delta.to_hash[:8], node_index, run_id,
                      )

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
              progress.total_cost_usd += node_cost
              progress.updated_at = datetime.now(timezone.utc).isoformat()
              if label not in ("VIOLATED", "VIOLATED_IRREVERSIBLE"):
                  progress.last_clean_node_index = node_index
              if node_index % active_spec.harness.checkpoint_every_n_nodes == 0:
                  progress.write()

              # ── 7. OTel emit — STUB ─────────────────────────────────────
              # TODO Step 13: emit_drift_span(node, active_spec, score, label)
              # if label in ("VIOLATED", "VIOLATED_IRREVERSIBLE", "STALLED"):
              #     emit_drift_span(node, active_spec, score, label)

              node_index += 1

      progress.is_complete = True
      progress.write()

      # Extract final output — defensive for pydantic-ai version differences.
      # agent_run.get_output() is preferred; result.output is the fallback.
      if hasattr(agent_run, "get_output"):
          return await agent_run.get_output()
      result = getattr(agent_run, "result", None)
      if result is not None:
          return getattr(result, "data", getattr(result, "output", result))
      logger.warning(
          "run_with_spec: output extraction failed. spec_version=%s run_id=%s",
          spec.version_hash, run_id,
      )
      return None
  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/core/trajectory.py
  git commit -m "step 3: refactor run_with_spec — full 7-step node-boundary orchestration"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && python -m pytest tests/ -m 'not integration' -q 2>&1
  ```

  **Expected:** Same or greater number of passing tests as Pre-Flight baseline. Zero failures.

  **Pass:** Test count ≥ pre-flight baseline, 0 failures.

  **Fail:**
  - `ImportError: cannot import name 'BallastProgress'` → Step 2 import edit not applied → check trajectory.py import block.
  - `ImportError: cannot import name 'ModelRequest'` → Step 2 import edit not applied → check trajectory.py import block.
  - Any existing trajectory test fails → edit touched `TrajectoryChecker` or its dependencies → read the failing test.

---

### Phase 3 — Tests

**Goal:** `test_checkpoint.py` covers write/read/resume. `test_trajectory.py` covers `score_drift()` and `run_with_spec()` with mock poller and checkpoint isolation.

---

- [ ] 🟥 **Step 4: Create `tests/test_checkpoint.py`** — *Non-critical*

  **Step Architecture Thinking:**

  **Pattern applied:** Unit test with tmpdir isolation — all file I/O goes to `tmp_path`, never to the project root.

  **Why this step exists here:** checkpoint.py is a new module with no existing tests. Adding tests immediately after creation catches serialisation bugs before they propagate into trajectory tests.

  **What breaks if this step deviates:** Nothing in production. Tests must use `tmp_path` — writing to the project root would pollute cwd state for the next test run.

  ---

  **Idempotent:** Yes — new file.

  **Pre-Read Gate:**
  - `ls tests/test_checkpoint.py` must return `No such file`. If it exists → STOP.

  ```python
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
      assert "Resume from node #8" in ctx


  def test_resume_context_no_summaries_shows_none():
      p = _make_progress()
      ctx = p.resume_context()
      assert "none" in ctx
  ```

  **Git Checkpoint:**
  ```bash
  git add tests/test_checkpoint.py
  git commit -m "step 4: add test_checkpoint.py — round-trip, resume_context, spec_hash invariant"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && python -m pytest tests/test_checkpoint.py -v 2>&1
  ```

  **Pass:** All 10 tests pass, 0 failures.

  **Fail:**
  - `TypeError: __init__() got unexpected keyword argument` → field name mismatch → compare `_make_node` and `_make_progress` kwargs against the dataclass field names in checkpoint.py.

---

- [ ] 🟥 **Step 5: Append `score_drift` and `run_with_spec` tests to `tests/test_trajectory.py`** — *Non-critical*

  **Step Architecture Thinking:**

  **Pattern applied:** Unit test with controlled mocking and cwd isolation. LLM scorers are mocked (same pattern as existing trajectory tests). `run_with_spec` is tested with a mock agent that has an explicit `get_output()` method — required because the output-extraction code prefers `get_output()` over the fallback `result.data/output` chain, and MagicMock auto-attributes make the fallback return a sub-mock instead of the configured string.

  **Why this step exists here:** Steps 2 and 3 added new public surface. Tests confirm the label invariants and the critical `NodeSummary.spec_hash` audit stamp.

  **What breaks if this step deviates:**
  - If `_MockAgentRun` lacks `get_output()`, output extraction falls through to `result.data` which auto-exists as a MagicMock sub-attribute — `test_run_with_spec_returns_output` would always fail.
  - If `MagicMock` is not imported, `NameError` at collection time kills the entire test file.
  - If tests write to cwd instead of `tmp_path`, they corrupt the real `ballast-progress.json` and break subsequent test runs.

  ---

  **Idempotent:** Yes — appends to existing test file.

  **Pre-Read Gate:**
  - `grep -n 'def test_score_drift' tests/test_trajectory.py` must return 0 matches. If 1+ → STOP.
  - `grep -n 'def test_run_with_spec' tests/test_trajectory.py` must return 0 matches. If 1+ → STOP.
  - `grep -n 'MagicMock' tests/test_trajectory.py` must return 0 matches (not yet imported). If 1+ → STOP.

  **Edit A — Update the existing top-level mock import (line 8).**

  Confirm uniqueness: `grep -c 'from unittest.mock import patch' tests/test_trajectory.py` must return `1`.

  Replace:
  ```python
  from unittest.mock import patch
  ```
  With:
  ```python
  from unittest.mock import MagicMock, patch
  ```

  **Edit B — Append the following block at the end of `tests/test_trajectory.py`** (after `test_trajectory_checker_real_llm`):

  ```python
  # ---------------------------------------------------------------------------
  # score_drift — label system (Step 2 additions)
  # ---------------------------------------------------------------------------

  import asyncio
  from contextlib import asynccontextmanager

  from ballast.core.checkpoint import BallastProgress
  from ballast.core.trajectory import _compact_node, run_with_spec, score_drift


  def _make_spec_with_irreversible() -> SpecModel:
      return lock(SpecModel(
          intent="count words",
          success_criteria=["returns integer"],
          irreversible_actions=["send_email"],
          allowed_tools=["read_file"],
          drift_threshold=0.4,
      ))


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


  def test_compact_node_returns_expected_keys():
      compact = _compact_node(FakeTextNode("some output"), 0.9, "PROGRESSING", 0.001, True)
      assert set(compact.keys()) == {"tool_name", "label", "score", "cost_usd", "verified", "summary"}
      assert compact["label"] == "PROGRESSING"
      assert compact["score"] == 0.9
      assert "some output" in compact["summary"]


  # ---------------------------------------------------------------------------
  # run_with_spec — orchestration loop (Step 3 additions)
  # ---------------------------------------------------------------------------

  class _RwsNode:
      """Minimal stand-in for a pydantic-ai node (no tool, no content)."""


  class _RwsAgentRun:
      """Mock AgentRun for run_with_spec tests.

      Exposes get_output() so the preferred output-extraction branch fires.
      Without get_output(), the fallback uses result.data which auto-exists
      as a MagicMock sub-attribute — causing assertion failures on string equality.
      """

      def __init__(self, nodes, output="done"):
          self._nodes = nodes
          self._output = output
          self.message_history = []

          state = MagicMock()
          state.message_history = self.message_history
          ctx = MagicMock()
          ctx.state = state
          self._ctx = ctx

      @property
      def ctx(self):
          return self._ctx

      async def get_output(self):
          return self._output

      def __aiter__(self):
          return self._gen()

      async def _gen(self):
          for node in self._nodes:
              yield node


  def _rws_make_agent(nodes, output="done"):
      """Return (mock_agent, mock_run) for run_with_spec tests."""
      run = _RwsAgentRun(nodes, output)
      agent = MagicMock()

      @asynccontextmanager
      async def _iter(task):
          yield run

      agent.iter = _iter
      return agent, run


  def _rws_make_poller(return_values):
      poller = MagicMock()
      poller.poll.side_effect = return_values
      return poller


  def test_run_with_spec_requires_locked_spec():
      draft = SpecModel(intent="x", success_criteria=["y"])
      agent, _ = _rws_make_agent([])
      with pytest.raises(ValueError, match="locked"):
          asyncio.run(run_with_spec(agent, "task", draft))


  def test_run_with_spec_returns_output(tmp_path, monkeypatch):
      monkeypatch.chdir(tmp_path)
      spec = _make_spec()
      nodes = [_RwsNode(), _RwsNode()]
      agent, _ = _rws_make_agent(nodes, output="my result")
      with patch("ballast.core.trajectory.score_drift", return_value=(1.0, "PROGRESSING", "")):
          out = asyncio.run(run_with_spec(agent, "task", spec))
      assert out == "my result"


  def test_run_with_spec_writes_checkpoint(tmp_path, monkeypatch):
      monkeypatch.chdir(tmp_path)
      spec = _make_spec()
      nodes = [_RwsNode()]
      agent, _ = _rws_make_agent(nodes)
      with patch("ballast.core.trajectory.score_drift", return_value=(1.0, "PROGRESSING", "")):
          asyncio.run(run_with_spec(agent, "task", spec))
      progress = BallastProgress.read(str(tmp_path / "ballast-progress.json"))
      assert progress is not None
      assert progress.is_complete is True
      assert len(progress.completed_node_summaries) == 1


  def test_run_with_spec_node_summary_uses_active_spec_hash(tmp_path, monkeypatch):
      """Critical: NodeSummary.spec_hash must be active_spec.version_hash, not dispatch hash."""
      monkeypatch.chdir(tmp_path)
      spec = lock(SpecModel(intent="Task A", success_criteria=["done A"]))
      spec_v2 = lock(SpecModel(intent="Task B", success_criteria=["done B"]))
      assert spec.version_hash != spec_v2.version_hash

      nodes = [_RwsNode(), _RwsNode()]
      agent, _ = _rws_make_agent(nodes)
      # spec_v2 returned at node 0 poll; None at node 1
      poller = _rws_make_poller([spec_v2, None])

      with patch("ballast.core.trajectory.score_drift", return_value=(1.0, "PROGRESSING", "")):
          asyncio.run(run_with_spec(agent, "task", spec, poller=poller))

      progress = BallastProgress.read(str(tmp_path / "ballast-progress.json"))
      # Both nodes must be stamped with spec_v2 (active after node-0 poll)
      assert progress.completed_node_summaries[0].spec_hash == spec_v2.version_hash
      assert progress.completed_node_summaries[1].spec_hash == spec_v2.version_hash


  def test_run_with_spec_violation_increments_counter(tmp_path, monkeypatch):
      monkeypatch.chdir(tmp_path)
      spec = _make_spec(drift_threshold=0.7)
      nodes = [_RwsNode()]
      agent, _ = _rws_make_agent(nodes)
      with patch("ballast.core.trajectory.score_drift", return_value=(0.3, "VIOLATED", "bad")):
          asyncio.run(run_with_spec(agent, "task", spec))
      progress = BallastProgress.read(str(tmp_path / "ballast-progress.json"))
      assert progress.total_violations == 1
      assert progress.total_drift_events == 1


  def test_run_with_spec_no_poller_skips_injection(tmp_path, monkeypatch):
      monkeypatch.chdir(tmp_path)
      spec = _make_spec()
      nodes = [_RwsNode()]
      agent, run = _rws_make_agent(nodes)
      with patch("ballast.core.trajectory.score_drift", return_value=(1.0, "PROGRESSING", "")):
          asyncio.run(run_with_spec(agent, "task", spec))
      assert run.message_history == []
  ```

  **Git Checkpoint:**
  ```bash
  git add tests/test_trajectory.py
  git commit -m "step 5: add score_drift + run_with_spec tests to test_trajectory.py"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && python -m pytest tests/ -m 'not integration' -q 2>&1
  ```

  **Pass:** All tests pass. Count ≥ pre-flight baseline + 17 new tests. Zero failures.

  **Fail:**
  - `NameError: name 'MagicMock' is not defined` → Edit A (import line update) was not applied → confirm line 8 of test_trajectory.py now reads `from unittest.mock import MagicMock, patch`.
  - `test_run_with_spec_returns_output` fails with `AssertionError` on string equality → `_RwsAgentRun.get_output` is missing or misspelled → check the class definition.
  - `test_run_with_spec_node_summary_uses_active_spec_hash` fails → `NodeSummary.spec_hash` set to `spec.version_hash` instead of `active_spec.version_hash` → check Step 3 checkpoint block (line with `spec_hash=active_spec.version_hash`).
  - `test_run_with_spec_writes_checkpoint` fails with `FileNotFoundError` → `monkeypatch.chdir` not working → confirm `tmp_path` and `monkeypatch` fixtures are both in the function signature.

---

## Regression Guard

| System | Pre-change behavior | Post-change verification |
|--------|---------------------|--------------------------|
| TrajectoryChecker | `check()` raises DriftDetected, returns DriftResult | All 17 pre-existing trajectory tests pass |
| hook.py | run_with_live_spec logs audit, injects on spec change | All 8 hook tests pass |
| sync.py | SpecPoller returns None on no change | All 9 sync tests pass |

**Test count regression check:**
- Before plan: `____`
- After plan: run `python -m pytest tests/ -m 'not integration' -q` — count must be ≥ baseline + 17.

---

## Rollback Procedure

```bash
git revert HEAD  # step 5
git revert HEAD  # step 4
git revert HEAD  # step 3
git revert HEAD  # step 2
git revert HEAD  # step 1
python -m pytest tests/ -m 'not integration' -q  # must match pre-flight baseline count
```

---

## Risk Heatmap

| Step | Risk | What Could Go Wrong | Early Detection | Idempotent |
|------|------|---------------------|-----------------|------------|
| Step 1 | 🟢 Low | Field name mismatch breaks round-trip | test_checkpoint.py immediately catches | Yes |
| Step 2 | 🟡 Medium | Import block edit creates duplicate lines | Pre-Read Gate + import error at test collection | Yes |
| Step 3 | 🔴 High | `NodeSummary.spec_hash` stamped with dispatch hash instead of active hash | `test_node_summary_uses_active_spec_hash` fails immediately | Yes |
| Step 4 | 🟢 Low | Test writes to project root | tmp_path isolation prevents | Yes |
| Step 5 | 🟡 Medium | `MagicMock` not imported (Edit A skipped) OR `get_output` missing → wrong return type | Collection-time NameError OR assertion fails on string equality | Yes |

---

## Success Criteria

| Feature | Target | Verification |
|---------|--------|--------------|
| checkpoint.py round-trip | Write + read produces identical typed structure | `test_round_trip_with_node_summary` passes |
| per-node spec_hash audit stamp | NodeSummary.spec_hash = active spec at execution time | `test_node_summary_uses_active_spec_hash` passes |
| VIOLATED_IRREVERSIBLE label | Irreversible tool → (0.0, VIOLATED_IRREVERSIBLE, ...) | `test_score_drift_irreversible_tool_returns_violated_irreversible` passes |
| Layer 2 stub boundary | 0.25 < score < 0.85 → STALLED | `test_score_drift_borderline_returns_stalled` passes |
| run_with_spec backward compat | 3-arg callers unbroken | All 17 pre-existing trajectory tests pass |
| run_with_spec checkpoint write | is_complete=True after run | `test_run_with_spec_writes_checkpoint` passes |
| Regression: hook.py | Unchanged | All 8 hook tests pass |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Do not batch multiple steps into one git commit.**
⚠️ **Step 5 has TWO edits (Edit A: import line, Edit B: appended block). Both must be applied. Missing Edit A causes a NameError that kills the entire test file.**
⚠️ **`NodeSummary.spec_hash` must always be `active_spec.version_hash`. Any deviation silently corrupts the training dataset.**
⚠️ **`_RwsAgentRun` must have `get_output()`. Without it, output extraction returns a MagicMock sub-attribute instead of the configured string.**
