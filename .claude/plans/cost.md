# cost.py Implementation Plan

**Overall Progress:** `0%` (0 / 3 steps complete)

---

## Decisions Log (pre-check resolutions)

| # | Flaw | Resolution applied |
|---|------|--------------------|
| 1 | Edit 1 import anchor placed `cost` after `spec` (wrong alphabetical order) | Anchor changed to `from ballast.core.spec import SpecModel, is_locked` → result: `checkpoint → cost → spec → sync` |
| 2 | `run_with_spec` docstring not updated for new params | Edit 2b added to update the Args block |
| 3 | `hook.py` pre-flight fix left `logger = ...` between import groups | Pre-flight fix now includes import block cleanup |
| 4 | Module-level docstring on trajectory.py line 4 references old signature | Folded into Edit 2b |

---

## TLDR

Build `ballast/core/cost.py` — the per-agent stacking cost cap enforcer with a hard global stop — then wire it into `run_with_spec` as two optional keyword params. After this plan: every `run_with_spec` call can accept a `RunCostGuard` + `agent_id`; if any node's cost would push an agent over its cap, or the run over `HARD_CAP_USD`, the run stops immediately with a typed exception. Backward compatible: callers that don't pass `cost_guard` are unaffected.

---

## Architecture Overview

**The problem this plan solves:**
`trajectory.py:run_with_spec` tracks `progress.total_cost_usd` for audit but has no enforcement — a run can spend without bound. `projet-overview.md` invariant 10 requires cost caps enforced in code, always a hard stop, never a config option.

**The patterns applied:**
- **Guard / Coordinator:** `RunCostGuard` is the single authority. All agents route through it. `AgentCostGuard` is a subordinate value object. No caller bypasses the guard to update totals directly.
- **Check-then-record:** `check(estimated)` raises before any state changes. `record(actual)` commits only after check passes. Guarantees the guard never silently accepts an overage.
- **Optional injection:** `cost_guard=None` default → no enforcement → all existing tests and callers unchanged.

**What stays unchanged:**
`spec.py`, `checkpoint.py`, `sync.py`, `hook.py`, `memory.py`, `server.py` — no cost logic touches these.

**What this plan adds:**

| File | Responsibility |
|------|---------------|
| `ballast/core/cost.py` | `HARD_CAP_USD`, three exception types, `AgentCostGuard`, `RunCostGuard` |
| `tests/test_cost.py` | 15 unit + integration tests |
| `ballast/core/trajectory.py` | Import + two new params on `run_with_spec` + step 6b enforcement block + docstring update |

**Critical decisions:**

| Decision | Alternative considered | Why rejected |
|----------|----------------------|--------------|
| `check` raises before `record` commits | Record first, raise after | Silent overage: audit log would show spend beyond cap |
| `cost_guard` optional (`None` default) | Required parameter | Breaks all 122 existing tests and callers |
| Enforcement at step 6b (after checkpoint) | Before drift score (step 2) | Project overview places cost at step 6; checkpoint records the node before stopping |
| `_spent` is `init=False` dataclass field | Constructor param | Allows constructing guards with non-zero spend, bypassing check-then-record |
| Import inserted before `from ballast.core.spec` | Before `from ballast.core.sync` | Alphabetical order: `cost` < `spec` < `sync` |

**Known limitations:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| No pre-node cost estimate | pydantic-ai nodes don't expose pre-execution cost | Wire in when evaluator.py (step 8) provides token estimates |
| `KeyError` on unregistered `agent_id` | Programmer error — visible immediately | Document in `register()` docstring |

---

## Clarification Gate

All values and interfaces are fully specified in `projet-overview.md` and confirmed by reading `trajectory.py`. No unknowns.

---

## Agent Failure Protocol

1. A verification command fails → read the full error output.
2. Cause is unambiguous → make ONE targeted fix → re-run the same verification command.
3. If still failing after one fix → **STOP**. Output full contents of every file modified in this step. Report: (a) command run, (b) full error verbatim, (c) fix attempted, (d) current state of each modified file, (e) why you cannot proceed.
4. Never attempt a second fix without human instruction.
5. Never modify files not named in the current step.

---

## Pre-Flight — Run Before Any Code Changes

```bash
source venv/bin/activate

# 1. Baseline test count (expect: 121 passed, 1 failed — test_print_format)
python -m pytest tests/ -m "not integration" -q --tb=no 2>&1 | tail -3

# 2. Line counts
wc -l ballast/core/trajectory.py ballast/core/hook.py

# 3. cost.py must NOT exist
ls ballast/core/cost.py 2>&1   # expected: No such file or directory

# 4. cost_guard must NOT already be in trajectory.py
grep -c "cost_guard" ballast/core/trajectory.py   # expected: 0

# 5. Confirm exact anchor lines (record line numbers for step 2)
grep -n "^from ballast.core.spec import" ballast/core/trajectory.py   # Edit 1 anchor
grep -n "^from ballast.core.sync import" ballast/core/trajectory.py   # for reference
grep -n "async def run_with_spec" ballast/core/trajectory.py           # Edit 2 anchor
grep -n "poller: Optional\[SpecPoller\] = None" ballast/core/trajectory.py  # Edit 2 anchor
grep -n "# ── 7. OTel emit" ballast/core/trajectory.py                 # Edit 3 anchor
grep -n "poller:  Optional SpecPoller" ballast/core/trajectory.py      # docstring anchor

# 6. Confirm hook.py state
grep -n "logger.debug\|print(" ballast/core/hook.py
grep -n "^logger\|^from\|^import" ballast/core/hook.py   # import block order
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan:       121 passed, 1 failed (test_print_format — known)
Line count trajectory.py:     784
Line count hook.py:           103
cost.py exists:               No
cost_guard in trajectory.py:  0
Edit 1 anchor line:           ____  (from ballast.core.spec import ...)
Edit 2 anchor line:           ____  (async def run_with_spec)
Edit 3 anchor line:           ____  (# ── 7. OTel emit)
Docstring anchor line:        ____  (poller:  Optional SpecPoller)
```

**Pre-flight fix — must be done and committed before Step 1:**

`test_print_format` fails because the prior commit replaced an intentional `print()` in `hook.py` with `logger.debug()`. The print is intentional — it provides live node-by-node visibility in the demo. The same commit also left `logger = logging.getLogger(__name__)` between import groups (between pydantic_ai imports and ballast imports), which violates PEP 8 import grouping. Fix both now:

In `ballast/core/hook.py`:

**Fix A — restore print** (replace `logger.debug` block with original `print`):

Old:
```python
            logger.debug(
                "node=%02d spec=%s node_type=%s",
                node_index, active_spec.version_hash[:8], type(node).__name__,
            )
```
New:
```python
            print(
                f"  node {node_index:02d} | spec:{active_spec.version_hash[:8]}"
                f" | {type(node).__name__}"
            )
```

**Fix B — move `logger` assignment to after all imports** (fix import block ordering):

Old:
```python
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, UserPromptPart

logger = logging.getLogger(__name__)

from ballast.core.spec import SpecDelta, SpecModel
from ballast.core.sync import SpecPoller
```
New:
```python
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, UserPromptPart

from ballast.core.spec import SpecDelta, SpecModel
from ballast.core.sync import SpecPoller

logger = logging.getLogger(__name__)
```

Verify: `python -m pytest tests/test_hook.py -q --tb=short` → all 8 pass.

Commit:
```bash
git add ballast/core/hook.py
git commit -m "pre-flight: restore intentional print in hook.py; fix import block ordering"
```

**All checks (must pass before Step 1):**
- [ ] `ls ballast/core/cost.py` → "No such file"
- [ ] `grep -c "cost_guard" ballast/core/trajectory.py` → `0`
- [ ] `python -m pytest tests/ -m "not integration" -q --tb=no` → 122 passed, 0 failed
- [ ] All 5 anchor greps return exactly 1 match each

---

## Tasks

### Phase 1 — Core cost module

**Goal:** `ballast/core/cost.py` exists and is importable. No other file changed.

---

- [ ] 🟥 **Step 1: Create `ballast/core/cost.py`** — *Critical: trajectory.py imports from it in Step 2*

  **Step Architecture Thinking:**

  **Pattern applied:** Guard / Coordinator + Check-then-record.

  **Why this step is first:** `trajectory.py` imports `RunCostGuard` from this module in Step 2. The module must exist before any wiring.

  **Why `ballast/core/cost.py`:** All `ballast/core/` modules are independent policy units wired by `trajectory.py`. Cost has no dependency on spec, sync, or checkpoint — it belongs at the same level as them.

  **Alternative rejected:** Inlining cost logic into `trajectory.py`. Rejected: `run_with_spec` is already 150+ lines; cost has its own exception hierarchy and must be independently testable without importing pydantic-ai.

  **What breaks if deviated:** If `_spent` is an `__init__` param (not `init=False`), callers can construct guards with non-zero spend, bypassing check-then-record. The invariant is violated silently.

  ---

  **Idempotent:** Yes — writing a new file.

  **Pre-Read Gate:**
  - `ls ballast/core/cost.py` → must return "No such file". If exists → STOP.

  ```python
  """ballast/core/cost.py — Per-agent stacking cost caps with hard global stop.

  Public interface:
      HARD_CAP_USD              — absolute ceiling across all agents in a run ($300)
      AgentCapExceeded          — raised when an agent's per-agent cap is breached
      EscalationBudgetExceeded  — raised when escalation pool is exhausted
      HardCapExceeded           — raised when global run hard cap would be exceeded
      AgentCostGuard            — per-agent cap enforcer: check() then record()
      RunCostGuard              — global enforcer; owns all AgentCostGuards for a run

  Usage:
      guard = RunCostGuard()
      guard.register("worker", cap=0.10, escalation_pool=0.03)
      # at each node boundary in run_with_spec:
      guard.check("worker", node_cost)   # raises if cap would be exceeded
      guard.record("worker", node_cost)  # commits spend only if check passed

  Invariant (projet-overview.md invariant 10):
      cost caps are enforced in code from day one.
      never a config option. always a hard stop.
  """
  from __future__ import annotations

  from dataclasses import dataclass, field

  HARD_CAP_USD: float = 300.0


  # ---------------------------------------------------------------------------
  # Exceptions
  # ---------------------------------------------------------------------------

  class AgentCapExceeded(Exception):
      """Raised when an agent's per-agent spend cap would be exceeded."""

      def __init__(self, agent_id: str, spent: float, cap: float, estimated: float) -> None:
          self.agent_id = agent_id
          self.spent = spent
          self.cap = cap
          self.estimated = estimated
          super().__init__(
              f"agent {agent_id!r} cap exceeded: "
              f"spent={spent:.6f} + estimated={estimated:.6f} > cap={cap:.6f}"
          )


  class EscalationBudgetExceeded(Exception):
      """Raised when an agent's escalation pool would be exceeded."""

      def __init__(self, agent_id: str, spent: float, pool: float, estimated: float) -> None:
          self.agent_id = agent_id
          self.spent = spent
          self.pool = pool
          self.estimated = estimated
          super().__init__(
              f"agent {agent_id!r} escalation pool exceeded: "
              f"spent={spent:.6f} + estimated={estimated:.6f} > pool={pool:.6f}"
          )


  class HardCapExceeded(Exception):
      """Raised when the global run hard cap would be exceeded."""

      def __init__(self, total: float, estimated: float) -> None:
          self.total = total
          self.estimated = estimated
          super().__init__(
              f"hard run cap exceeded: "
              f"total={total:.6f} + estimated={estimated:.6f} > "
              f"hard_cap={HARD_CAP_USD:.6f}"
          )


  # ---------------------------------------------------------------------------
  # AgentCostGuard — per-agent enforcer
  # ---------------------------------------------------------------------------

  @dataclass
  class AgentCostGuard:
      """Tracks and enforces per-agent spend limits.

      _spent and _escalation_spent are internal state — excluded from __init__.
      Always call check() before record() to preserve the invariant.
      """

      agent_id: str
      agent_cap_usd: float
      escalation_pool_usd: float
      _spent: float = field(default=0.0, init=False, repr=False)
      _escalation_spent: float = field(default=0.0, init=False, repr=False)

      def check(self, estimated: float, is_escalation: bool = False) -> None:
          """Raise if recording `estimated` would exceed this agent's cap.

          Does NOT modify state. Always call before record().
          """
          if is_escalation:
              if self._escalation_spent + estimated > self.escalation_pool_usd:
                  raise EscalationBudgetExceeded(
                      self.agent_id,
                      self._escalation_spent,
                      self.escalation_pool_usd,
                      estimated,
                  )
          else:
              if self._spent + estimated > self.agent_cap_usd:
                  raise AgentCapExceeded(
                      self.agent_id,
                      self._spent,
                      self.agent_cap_usd,
                      estimated,
                  )

      def record(self, actual: float, is_escalation: bool = False) -> None:
          """Commit actual spend. Only call after check() has passed."""
          if is_escalation:
              self._escalation_spent += actual
          else:
              self._spent += actual

      @property
      def spent(self) -> float:
          """Total non-escalation spend recorded so far."""
          return self._spent

      @property
      def escalation_spent(self) -> float:
          """Total escalation spend recorded so far."""
          return self._escalation_spent


  # ---------------------------------------------------------------------------
  # RunCostGuard — global enforcer
  # ---------------------------------------------------------------------------

  class RunCostGuard:
      """Global cost enforcer for a single run. Owns all AgentCostGuards.

      Call register() for every agent_id before passing this guard to run_with_spec.
      check() enforces the global HARD_CAP_USD first, then the per-agent cap.
      record() advances both the global total and the per-agent total.
      """

      def __init__(self) -> None:
          self._agents: dict[str, AgentCostGuard] = {}
          self._total: float = 0.0

      def register(self, agent_id: str, cap: float, escalation_pool: float) -> None:
          """Register an agent with its spend cap and escalation pool.

          Must be called before check() or record() for this agent_id.
          """
          self._agents[agent_id] = AgentCostGuard(agent_id, cap, escalation_pool)

      def check(
          self, agent_id: str, estimated: float, is_escalation: bool = False
      ) -> None:
          """Raise if recording `estimated` would exceed any cap.

          Order: global HARD_CAP_USD checked first → per-agent cap second.
          HardCapExceeded fires even if the agent-level cap would allow it.
          Raises KeyError if agent_id was not registered via register().
          Does NOT modify state.
          """
          if self._total + estimated > HARD_CAP_USD:
              raise HardCapExceeded(self._total, estimated)
          self._agents[agent_id].check(estimated, is_escalation)

      def record(
          self, agent_id: str, actual: float, is_escalation: bool = False
      ) -> None:
          """Commit actual spend to global total and per-agent guard."""
          self._total += actual
          self._agents[agent_id].record(actual, is_escalation)

      @property
      def total_spent(self) -> float:
          """Total spend across all agents recorded so far."""
          return self._total

      def report(self) -> dict:
          """Return a spend summary dict suitable for logging or the dashboard."""
          return {
              "total_spent": round(self._total, 6),
              "hard_cap": HARD_CAP_USD,
              "remaining": round(HARD_CAP_USD - self._total, 6),
              "agents": {
                  aid: {
                      "spent": round(g.spent, 6),
                      "escalation_spent": round(g.escalation_spent, 6),
                      "cap": g.agent_cap_usd,
                      "escalation_pool": g.escalation_pool_usd,
                  }
                  for aid, g in self._agents.items()
              },
          }
  ```

  **What it does:** Pure Python — no I/O, no LLM calls, no pydantic-ai. Three typed exceptions, `AgentCostGuard` (per-agent), `RunCostGuard` (global coordinator). `field(init=False)` on private fields prevents callers from constructing guards with pre-loaded spend.

  **Assumptions:**
  - `ballast/core/` directory exists
  - No existing `cost.py` (confirmed pre-flight)

  **Risks:**
  - Float accumulation drift on `_total` → mitigation: `round(..., 6)` only in `report()`; enforcement uses raw floats throughout (consistent with how `node_cost` arrives via pydantic-ai)

  **Git Checkpoint:**
  ```bash
  git add ballast/core/cost.py
  git commit -m "step 6: add cost.py — AgentCostGuard, RunCostGuard, three cap exceptions"
  ```

  **Subtasks:**
  - [ ] 🟥 Write `ballast/core/cost.py` verbatim from code block above
  - [ ] 🟥 Run smoke test (see verification)
  - [ ] 🟥 Commit

  **✓ Verification Test:**

  **Type:** Unit (import smoke test)

  **Action:**
  ```bash
  source venv/bin/activate
  python -c "
  from ballast.core.cost import (
      HARD_CAP_USD, AgentCapExceeded, EscalationBudgetExceeded,
      HardCapExceeded, AgentCostGuard, RunCostGuard
  )
  g = RunCostGuard()
  g.register('worker', cap=0.10, escalation_pool=0.03)
  g.check('worker', 0.05)
  g.record('worker', 0.05)
  assert round(g.total_spent, 4) == 0.05, f'expected 0.05 got {g.total_spent}'
  assert g.report()['remaining'] == round(HARD_CAP_USD - 0.05, 6)
  print('PASSED')
  "
  ```

  **Pass:** Prints `PASSED`, exits 0.

  **Fail:**
  - `ModuleNotFoundError` → file not at `ballast/core/cost.py` → confirm path
  - `AssertionError: expected 0.05 got ...` → logic error in `record()` or `report()` → re-read code block

---

### Phase 2 — Wire into `run_with_spec`

**Goal:** `run_with_spec` accepts `cost_guard` and `agent_id`; enforces caps at step 6b; all 122 existing tests still pass.

---

- [ ] 🟥 **Step 2: Add cost enforcement to `trajectory.py:run_with_spec`** — *Critical: modifies function used by all existing tests*

  **Step Architecture Thinking:**

  **Pattern applied:** Optional injection (Null Object). `cost_guard=None` → block is skipped → identical to prior behaviour. This is the only way to add enforcement without breaking the 122 existing tests.

  **Why after Step 1:** `RunCostGuard` must be importable before `trajectory.py` can reference it.

  **Why `trajectory.py`:** `run_with_spec` owns the node boundary loop. Cost enforcement belongs at the boundary, not in callers. The project overview diagram places it at step 6.

  **Alternative rejected:** Placing the block before step 2 (drift score). Rejected: project overview specifies step 6; placing it earlier changes the semantics — a node would stop before its score is recorded in `BallastProgress`.

  **What breaks if deviated:** If `record()` is called before `check()`, a cap breach is silently committed before the exception fires — audit log shows spend beyond the declared cap.

  ---

  **Idempotent:** No. Detect re-run: `grep -c "cost_guard" ballast/core/trajectory.py` → if > 0, STOP and report.

  **Pre-Read Gate (run ALL before touching the file):**
  - `grep -c "cost_guard" ballast/core/trajectory.py` → must be `0`. If > 0 → STOP.
  - `grep -n "^from ballast.core.spec import" ballast/core/trajectory.py` → exactly 1 match (Edit 1 anchor)
  - `grep -n "async def run_with_spec" ballast/core/trajectory.py` → exactly 1 match
  - `grep -n "poller: Optional\[SpecPoller\] = None," ballast/core/trajectory.py` → exactly 1 match (Edit 2 anchor)
  - `grep -n "# ── 7. OTel emit" ballast/core/trajectory.py` → exactly 1 match (Edit 3 anchor)
  - `grep -n "poller:  Optional SpecPoller" ballast/core/trajectory.py` → exactly 1 match (Edit 2b anchor)
  - `grep -n "run_with_spec(agent, task, spec, poller=None)" ballast/core/trajectory.py` → exactly 1 match (Edit 2c anchor)

  If any grep returns 0 or >1 matches → STOP and report which anchor failed.

  ---

  **Edit 1 — Add import (alphabetical: cost between checkpoint and spec)**

  Old:
  ```python
  from ballast.core.spec import SpecModel, is_locked
  ```
  New:
  ```python
  from ballast.core.cost import RunCostGuard
  from ballast.core.spec import SpecModel, is_locked
  ```

  Result in import block: `checkpoint → cost → spec → sync` ✓ alphabetical.

  ---

  **Edit 2 — Add params to `run_with_spec` signature**

  Old:
  ```python
  async def run_with_spec(
      agent: Agent,
      task: str,
      spec: SpecModel,
      poller: Optional[SpecPoller] = None,
  ) -> Any:
  ```
  New:
  ```python
  async def run_with_spec(
      agent: Agent,
      task: str,
      spec: SpecModel,
      poller: Optional[SpecPoller] = None,
      cost_guard: Optional[RunCostGuard] = None,
      agent_id: str = "default",
  ) -> Any:
  ```

  ---

  **Edit 2b — Update `run_with_spec` docstring Args block**

  Old:
  ```python
      poller:  Optional SpecPoller. If None, spec stays fixed for the run.
  ```
  New:
  ```python
      poller:     Optional SpecPoller. If None, spec stays fixed for the run.
      cost_guard: Optional RunCostGuard. If None, no cost enforcement is applied.
      agent_id:   Agent identifier registered in cost_guard. Default "default".
                  Ignored when cost_guard is None.
  ```

  ---

  **Edit 2c — Update module-level docstring signature (line 4 of trajectory.py)**

  Old:
  ```python
      run_with_spec(agent, task, spec, poller=None)
  ```
  New:
  ```python
      run_with_spec(agent, task, spec, poller=None, cost_guard=None, agent_id="default")
  ```

  Anchor: this exact string appears exactly once in the file. Confirm with:
  ```bash
  grep -n "run_with_spec(agent, task, spec, poller=None)" ballast/core/trajectory.py
  ```
  Expected: exactly 1 match on line ~4.

  ---

  **Edit 3 — Add step 6b enforcement block**

  Old:
  ```python
              # ── 7. OTel emit — STUB ─────────────────────────────────────
  ```
  New:
  ```python
              # ── 6b. Cost enforcement ────────────────────────────────────
              if cost_guard is not None:
                  cost_guard.check(agent_id, node_cost)
                  cost_guard.record(agent_id, node_cost)

              # ── 7. OTel emit — STUB ─────────────────────────────────────
  ```

  **What it does:** Five targeted edits — module docstring, import, signature, args docstring, enforcement block. `node_cost` is already in scope at step 6b (assigned at step 4, line 698). The `if cost_guard is not None:` guard ensures the block is entirely skipped for all existing callers.

  **Why check before record:** `check` raises without changing state; `record` commits. Reversed order = silent overage before exception.

  **Assumptions:**
  - `Optional` is already imported from `typing` in trajectory.py (confirmed: line 27)
  - `node_cost` is in scope at the insertion point (confirmed: assigned at step 4, still in scope)
  - `# ── 7. OTel emit` appears exactly once (confirmed pre-read gate)

  **Risks:**
  - `KeyError` from unregistered `agent_id` → mitigation: documented; `test_run_with_spec_no_cost_guard_is_backward_compatible` confirms None path is unaffected

  **Git Checkpoint:**
  ```bash
  git add ballast/core/trajectory.py
  git commit -m "step 6: wire RunCostGuard into run_with_spec — cost_guard + agent_id params, step 6b enforcement"
  ```

  **Subtasks:**
  - [ ] 🟥 Run pre-read gate (all 6 greps pass)
  - [ ] 🟥 Apply Edit 1 (import)
  - [ ] 🟥 Apply Edit 2 (signature)
  - [ ] 🟥 Apply Edit 2b (args docstring)
  - [ ] 🟥 Apply Edit 2c (module-level docstring signature)
  - [ ] 🟥 Apply Edit 3 (enforcement block)
  - [ ] 🟥 Run existing suite — must equal 122 passed, 0 failed

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  source venv/bin/activate
  python -m pytest tests/ -m "not integration" -q --tb=short 2>&1 | tail -5
  ```

  **Expected:** `122 passed` (or higher after Step 3 runs — must be ≥ 122), 0 failed.

  **Pass:** Zero failures. Count ≥ 122.

  **Fail:**
  - `ImportError: cannot import name 'RunCostGuard'` → Edit 1 not applied → confirm `ballast/core/cost.py` exists
  - `TypeError: run_with_spec() got unexpected keyword argument 'cost_guard'` → Edit 2 not applied → re-check signature
  - Any existing test regression → check `git diff ballast/core/trajectory.py` — confirm only the 4 intended edits are present

---

### Phase 3 — Tests

**Goal:** `tests/test_cost.py` exists; 15 new tests pass; total suite count ≥ 137.

---

- [ ] 🟥 **Step 3: Write `tests/test_cost.py`** — *Non-critical: new file, no existing code touched*

  **Step Architecture Thinking:**

  **Pattern applied:** Arrange-Act-Assert; one behaviour per test.

  **Why after Steps 1 and 2:** Tests import from `ballast.core.cost` (Step 1) and call `run_with_spec` with `cost_guard` param (Step 2). Both must exist.

  **Why a new file instead of appending to `test_trajectory.py`:** Cost unit tests have zero pydantic-ai dependency. Keeping them separate means they run faster and the boundary between cost policy and orchestration remains explicit.

  **What breaks if deviated:** If `_RwsAgentRun._gen` doesn't set `node.cost_usd` before yielding, `getattr(node, "cost_usd", 0.0)` in trajectory.py returns `0.0` for all nodes — integration tests that check `total_spent` will fail with `AssertionError` showing `0.0` instead of the expected sum.

  ---

  **Idempotent:** Yes — writing a new file.

  **Pre-Read Gate:**
  - `ls tests/test_cost.py` → must return "No such file". If exists → STOP.

  ```python
  """Tests for ballast/core/cost.py — cost cap enforcement.

  Sections:
      AgentCostGuard  — unit tests; no pydantic-ai dependency
      RunCostGuard    — unit tests; no pydantic-ai dependency
      run_with_spec   — integration: cost_guard wired into orchestration loop
  """
  from __future__ import annotations

  import asyncio
  from contextlib import asynccontextmanager
  from unittest.mock import MagicMock, patch

  import pytest

  from ballast.core.cost import (
      HARD_CAP_USD,
      AgentCapExceeded,
      AgentCostGuard,
      EscalationBudgetExceeded,
      HardCapExceeded,
      RunCostGuard,
  )
  from ballast.core.spec import SpecModel, lock
  from ballast.core.trajectory import run_with_spec


  # ---------------------------------------------------------------------------
  # AgentCostGuard
  # ---------------------------------------------------------------------------

  def test_agent_guard_allows_under_cap():
      g = AgentCostGuard("worker", agent_cap_usd=0.10, escalation_pool_usd=0.03)
      g.check(0.05)  # must not raise


  def test_agent_guard_raises_when_spend_plus_estimated_exceeds_cap():
      g = AgentCostGuard("worker", agent_cap_usd=0.10, escalation_pool_usd=0.03)
      g.record(0.10)
      with pytest.raises(AgentCapExceeded):
          g.check(0.001)


  def test_agent_guard_check_does_not_mutate_spent():
      g = AgentCostGuard("worker", agent_cap_usd=1.0, escalation_pool_usd=0.1)
      g.check(0.50)
      assert g.spent == 0.0  # check must never change state


  def test_agent_guard_record_accumulates_correctly():
      g = AgentCostGuard("worker", agent_cap_usd=1.0, escalation_pool_usd=0.1)
      g.record(0.30)
      g.record(0.40)
      assert round(g.spent, 4) == 0.70


  def test_agent_guard_escalation_pool_is_independent_of_main_cap():
      g = AgentCostGuard("worker", agent_cap_usd=0.10, escalation_pool_usd=0.03)
      g.record(0.09, is_escalation=False)
      g.check(0.03, is_escalation=True)  # escalation pool is separate — must not raise


  def test_agent_guard_escalation_raises_when_pool_exhausted():
      g = AgentCostGuard("worker", agent_cap_usd=0.10, escalation_pool_usd=0.03)
      g.record(0.03, is_escalation=True)
      with pytest.raises(EscalationBudgetExceeded):
          g.check(0.001, is_escalation=True)


  def test_agent_cap_exceeded_carries_agent_id():
      g = AgentCostGuard("broker", agent_cap_usd=0.05, escalation_pool_usd=0.01)
      g.record(0.05)
      with pytest.raises(AgentCapExceeded) as exc_info:
          g.check(0.001)
      assert exc_info.value.agent_id == "broker"


  def test_escalation_budget_exceeded_carries_agent_id():
      g = AgentCostGuard("ceo", agent_cap_usd=0.10, escalation_pool_usd=0.02)
      g.record(0.02, is_escalation=True)
      with pytest.raises(EscalationBudgetExceeded) as exc_info:
          g.check(0.001, is_escalation=True)
      assert exc_info.value.agent_id == "ceo"


  # ---------------------------------------------------------------------------
  # RunCostGuard
  # ---------------------------------------------------------------------------

  def test_run_guard_allows_under_all_caps():
      rg = RunCostGuard()
      rg.register("worker", cap=0.10, escalation_pool=0.03)
      rg.check("worker", 0.05)  # must not raise


  def test_run_guard_raises_hard_cap_before_agent_cap():
      """HardCapExceeded fires even when per-agent cap would allow it."""
      rg = RunCostGuard()
      rg.register("worker", cap=HARD_CAP_USD + 1.0, escalation_pool=0.0)
      rg._total = HARD_CAP_USD - 0.001
      with pytest.raises(HardCapExceeded):
          rg.check("worker", 0.002)


  def test_run_guard_hard_cap_exceeded_carries_total_and_estimated():
      rg = RunCostGuard()
      rg.register("worker", cap=500.0, escalation_pool=0.0)
      rg._total = HARD_CAP_USD - 0.001
      with pytest.raises(HardCapExceeded) as exc_info:
          rg.check("worker", 0.002)
      assert exc_info.value.total == pytest.approx(HARD_CAP_USD - 0.001)
      assert exc_info.value.estimated == pytest.approx(0.002)


  def test_run_guard_record_advances_global_total():
      rg = RunCostGuard()
      rg.register("worker", cap=1.0, escalation_pool=0.1)
      rg.record("worker", 0.05)
      rg.record("worker", 0.10)
      assert round(rg.total_spent, 4) == 0.15


  def test_run_guard_unregistered_agent_raises_key_error():
      rg = RunCostGuard()
      with pytest.raises(KeyError):
          rg.check("ghost", 0.01)


  def test_run_guard_report_contains_expected_keys():
      rg = RunCostGuard()
      rg.register("worker", cap=0.10, escalation_pool=0.03)
      rg.record("worker", 0.05)
      r = rg.report()
      assert set(r.keys()) == {"total_spent", "hard_cap", "remaining", "agents"}
      assert "worker" in r["agents"]
      assert r["agents"]["worker"]["spent"] == pytest.approx(0.05)
      assert r["hard_cap"] == HARD_CAP_USD


  def test_run_guard_remaining_decrements_on_record():
      rg = RunCostGuard()
      rg.register("worker", cap=1.0, escalation_pool=0.1)
      rg.record("worker", 0.25)
      assert rg.report()["remaining"] == pytest.approx(HARD_CAP_USD - 0.25)


  # ---------------------------------------------------------------------------
  # run_with_spec integration — cost_guard wiring
  # ---------------------------------------------------------------------------

  class _RwsNode:
      """Minimal stand-in for a pydantic-ai node."""


  class _RwsAgentRun:
      """Mock AgentRun for cost integration tests.

      _gen sets node.cost_usd before yielding each node so that
      getattr(node, 'cost_usd', 0.0) in trajectory.py returns the test value.
      """

      def __init__(self, nodes, output="done", node_costs=None):
          self._nodes = nodes
          self._output = output
          self._node_costs = node_costs or [0.0] * len(nodes)
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
          for i, node in enumerate(self._nodes):
              node.cost_usd = self._node_costs[i]  # set before yield
              yield node


  def _rws_make_agent(nodes, output="done", node_costs=None):
      run = _RwsAgentRun(nodes, output, node_costs)
      agent = MagicMock()

      @asynccontextmanager
      async def _iter(task):
          yield run

      agent.iter = _iter
      return agent, run


  def _make_spec():
      return lock(SpecModel(intent="count words", success_criteria=["returns integer"]))


  def test_run_with_spec_records_node_costs_in_guard(tmp_path, monkeypatch):
      """RunCostGuard.total_spent equals sum of all node costs after a clean run."""
      monkeypatch.chdir(tmp_path)
      spec = _make_spec()
      nodes = [_RwsNode(), _RwsNode(), _RwsNode()]
      agent, _ = _rws_make_agent(nodes, node_costs=[0.01, 0.02, 0.03])
      rg = RunCostGuard()
      rg.register("worker", cap=1.0, escalation_pool=0.1)
      with patch("ballast.core.trajectory.score_drift", return_value=(1.0, "PROGRESSING", "")):
          asyncio.run(run_with_spec(agent, "task", spec, cost_guard=rg, agent_id="worker"))
      assert round(rg.total_spent, 4) == 0.06


  def test_run_with_spec_agent_cap_stops_run(tmp_path, monkeypatch):
      """AgentCapExceeded propagates out of run_with_spec when per-agent cap is hit."""
      monkeypatch.chdir(tmp_path)
      spec = _make_spec()
      # cap=0.015: node 0 costs 0.01 (passes), node 1 costs 0.01 (0.01+0.01=0.02 > 0.015 → raises)
      nodes = [_RwsNode(), _RwsNode(), _RwsNode()]
      agent, _ = _rws_make_agent(nodes, node_costs=[0.01, 0.01, 0.01])
      rg = RunCostGuard()
      rg.register("worker", cap=0.015, escalation_pool=0.0)
      with patch("ballast.core.trajectory.score_drift", return_value=(1.0, "PROGRESSING", "")):
          with pytest.raises(AgentCapExceeded):
              asyncio.run(run_with_spec(agent, "task", spec, cost_guard=rg, agent_id="worker"))


  def test_run_with_spec_hard_cap_stops_run(tmp_path, monkeypatch):
      """HardCapExceeded propagates when global hard cap is hit."""
      monkeypatch.chdir(tmp_path)
      spec = _make_spec()
      # _total starts at 299.5; node costs 1.0 → 299.5 + 1.0 = 300.5 > 300 → raises
      nodes = [_RwsNode()]
      agent, _ = _rws_make_agent(nodes, node_costs=[1.0])
      rg = RunCostGuard()
      rg.register("worker", cap=500.0, escalation_pool=0.0)
      rg._total = HARD_CAP_USD - 0.5
      with patch("ballast.core.trajectory.score_drift", return_value=(1.0, "PROGRESSING", "")):
          with pytest.raises(HardCapExceeded):
              asyncio.run(run_with_spec(agent, "task", spec, cost_guard=rg, agent_id="worker"))


  def test_run_with_spec_no_cost_guard_is_backward_compatible(tmp_path, monkeypatch):
      """Omitting cost_guard entirely — run completes normally, no exceptions raised."""
      monkeypatch.chdir(tmp_path)
      spec = _make_spec()
      nodes = [_RwsNode()]
      agent, _ = _rws_make_agent(nodes)
      with patch("ballast.core.trajectory.score_drift", return_value=(1.0, "PROGRESSING", "")):
          out = asyncio.run(run_with_spec(agent, "task", spec))
      assert out == "done"
  ```

  **What it does:** 15 tests. `AgentCostGuard` / `RunCostGuard` sections have zero pydantic-ai dependency. Integration section uses a local `_RwsAgentRun` that sets `node.cost_usd` dynamically before yielding each node — this is what makes `getattr(node, "cost_usd", 0.0)` in `trajectory.py` return the test value.

  **Assumptions:**
  - `ballast/core/cost.py` exists (Step 1)
  - `run_with_spec` accepts `cost_guard` and `agent_id` (Step 2)
  - `_RwsNode` is `class _RwsNode: pass` with no `__slots__` → dynamic attribute setting works

  **Risks:**
  - `total_spent == 0.0` when expected sum > 0 → `node.cost_usd` not set before yield → re-read `_RwsAgentRun._gen`

  **Git Checkpoint:**
  ```bash
  git add tests/test_cost.py
  git commit -m "step 5: add test_cost.py — AgentCostGuard, RunCostGuard, run_with_spec integration (15 tests)"
  ```

  **Subtasks:**
  - [ ] 🟥 Write `tests/test_cost.py` verbatim from code block above
  - [ ] 🟥 Run full suite — confirm ≥ 137 passed, 0 failed

  **✓ Verification Test:**

  **Type:** Unit + Integration

  **Action:**
  ```bash
  source venv/bin/activate
  python -m pytest tests/test_cost.py -v --tb=short 2>&1 | tail -20
  python -m pytest tests/ -m "not integration" -q --tb=no 2>&1 | tail -3
  ```

  **Expected:**
  - `test_cost.py`: 15 passed, 0 failed
  - Full suite: ≥ 137 passed, 0 failed

  **Pass:** Both commands exit 0; `test_cost.py` shows 15 passed.

  **Fail:**
  - `ImportError: cannot import name 'RunCostGuard' from 'ballast.core.trajectory'` → Step 2 Edit 1 not applied
  - `TypeError: run_with_spec() got an unexpected keyword argument 'cost_guard'` → Step 2 Edit 2 not applied
  - `AssertionError: assert 0.0 == 0.06` in `test_run_with_spec_records_node_costs_in_guard` → `_gen` not setting `node.cost_usd` → re-read `_RwsAgentRun._gen`
  - Any pre-existing test failure → pre-flight hook.py fix was not committed

---

## Regression Guard

| System | Pre-change behaviour | Post-change verification |
|--------|---------------------|--------------------------|
| `run_with_spec` (no cost_guard) | Completes normally | `test_run_with_spec_no_cost_guard_is_backward_compatible` passes |
| All `test_trajectory.py` tests | 37 pass | `python -m pytest tests/test_trajectory.py -q` → same count |
| All `test_spec.py` tests | 34 pass | `python -m pytest tests/test_spec.py -q` → same count |

**Test count regression check:**
- Baseline: 122 passed (after pre-flight fix)
- After plan: ≥ 137 passed, 0 failed

---

## Rollback Procedure

```bash
# Reverse in commit order (newest first)
git revert HEAD   # reverts Step 3 (test_cost.py)
git revert HEAD   # reverts Step 2 (trajectory.py wiring)
git revert HEAD   # reverts Step 1 (cost.py)
git revert HEAD   # reverts pre-flight (hook.py fix)

# Confirm:
source venv/bin/activate
python -m pytest tests/ -m "not integration" -q --tb=no
# Must return: 121 passed, 1 failed (test_print_format — original state)
```

---

## Success Criteria

| Feature | Target | Verification |
|---------|--------|--------------|
| Module importable | All 6 names importable | `python -c "from ballast.core.cost import HARD_CAP_USD, AgentCapExceeded, EscalationBudgetExceeded, HardCapExceeded, AgentCostGuard, RunCostGuard; print('ok')"` → `ok` |
| Agent cap enforced | `AgentCapExceeded` raised when cap hit | `test_run_with_spec_agent_cap_stops_run` passes |
| Hard cap priority | `HardCapExceeded` before agent cap | `test_run_guard_raises_hard_cap_before_agent_cap` passes |
| Check is read-only | `check()` never changes `_spent` | `test_agent_guard_check_does_not_mutate_spent` passes |
| Backward compatible | No existing test breaks | Full suite ≥ 137 passed, 0 failed |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Pre-flight hook.py fix must be committed before Step 1 begins.**
⚠️ **Step 2 is not idempotent — run `grep -c "cost_guard" trajectory.py` → must be 0 before applying.**
⚠️ **Do not batch multiple steps into one commit.**
⚠️ **Apply all 4 edits in Step 2 before running the verification test.**
