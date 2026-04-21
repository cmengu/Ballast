# Cost Guard Design Fixes

**Overall Progress:** `0% (0/4 steps done)`

---

## TLDR

Four design gaps in `ballast/core/cost.py` and its wiring in `trajectory.py`, identified through staff-engineer review. In severity order: (1) `RunCostGuard` is not resume-aware — after a crash and resume, `_total` resets to 0.0 while the checkpoint shows prior spend, silently allowing over-budget runs. (2) `check()` / `record()` are two separate calls at the call-site — nothing prevents a future engineer from calling `record()` without `check()`, or from an `await` slipping between them. (3) `getattr(node, "cost_usd", 0.0)` silently makes the guard a no-op if pydantic-ai doesn't expose that attribute. (4) `HARD_CAP_USD` is a module constant — the `HardCapExceeded` path cannot be tested without monkey-patching global state. All four are fixed with minimal surface-area changes. No behaviour changes to callers that don't use `cost_guard`.

---

## Architecture Overview

**The problem this plan solves:**

`ballast/core/cost.py` has the right shape but four correctness and testability gaps. In order:

1. **Not resume-aware:** `RunCostGuard._total` is always `0.0` at construction. `run_with_spec` resumes from `BallastProgress` but never seeds the guard with `progress.total_cost_usd`. A run that spent $0.08 of a $0.10 cap in segment 1, crashed, and resumed would have $0.10 available again. The project invariant ("always a hard stop") is defeated across resume boundaries.

2. **Two-call protocol is unenforced:** `check()` then `record()` is a convention documented in a docstring. Nothing in the type system prevents `record()` being called without `check()`. The standard call-site in `run_with_spec` exposes two lines that future engineers can reorder, drop, or split with an `await`.

3. **Silent no-op when `cost_usd` absent:** `getattr(node, "cost_usd", 0.0)` returns 0.0 if the attribute doesn't exist. With every node costing $0.00, the guard never fires. No log line, no error, no indication that enforcement is silently disabled.

4. **`HARD_CAP_USD` is a module constant:** The `HardCapExceeded` path cannot be exercised in tests without setting `rg._total = HARD_CAP_USD - 0.001` — accessing private state. Tests are fragile. Any test that exercises the hard cap is coupled to $300 arithmetic.

**Patterns applied:**

- **Command (check_and_record):** Encapsulate the two-phase protocol into one method. The correct usage becomes the easy usage. The two individual methods remain for callers with genuine split needs.
- **Template Method (seed_prior_spend):** The resume path in `run_with_spec` is the only caller of `seed_prior_spend`. It owns the seeding decision; the guard just exposes the hook.
- **Constructor injection (hard_cap_usd):** `HARD_CAP_USD` stays as the production default. Tests pass a small value. No monkey-patching.

**What stays unchanged:**

`check()`, `record()`, `register()`, `report()` public signatures — all existing callers unaffected. `HARD_CAP_USD = 300.0` constant stays as the default. `cost_guard=None` default on `run_with_spec` — all existing tests unaffected.

**Critical decisions:**

| Decision | Alternative | Why rejected |
|---|---|---|
| `seed_prior_spend()` called by `run_with_spec`, not the caller | Caller passes `prior_spend` to `RunCostGuard.__init__` | Caller constructs the guard before knowing whether a resume will happen. `run_with_spec` reads the checkpoint and is the only place with that knowledge. |
| `seed_prior_spend` raises if `_total != 0.0` | Silently overwrite | Double-seeding is a programmer error. Loud failure is better than silent wrong state. |
| Warn once per run, not per node | Warn every node | A single missing-attribute warning per run is signal. Per-node would be noise that drowns real warnings. |
| `hard_cap_usd` on `RunCostGuard`, not on `AgentCostGuard` | Per-agent hard cap | The hard cap is a global run ceiling, not a per-agent concern. Architecturally it belongs on the coordinator. |

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
cd /Users/ngchenmeng/Ballast && source venv/bin/activate

# (1) Baseline test count
python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3

# (2) Confirm current RunCostGuard.__init__ signature (no hard_cap_usd param)
grep -n 'def __init__' ballast/core/cost.py

# (3) Confirm no check_and_record or seed_prior_spend exist yet
grep -c 'check_and_record\|seed_prior_spend' ballast/core/cost.py

# (4) Confirm current call-site in trajectory.py (two separate calls)
grep -n 'cost_guard.check\|cost_guard.record' ballast/core/trajectory.py

# (5) Confirm current node_cost extraction (getattr)
grep -n 'getattr.*cost_usd' ballast/core/trajectory.py

# (6) Line counts
wc -l ballast/core/cost.py tests/test_cost.py ballast/core/trajectory.py
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan:               ____   (expected: 141)
cost.py line count:                   ____   (expected: 197)
test_cost.py line count:              ____   (expected: 256)
trajectory.py line count:             ____   (expected: 795)
check_and_record exists:              ____   (expected: 0)
seed_prior_spend exists:              ____   (expected: 0)
cost_guard call-site lines:           ____   (expected: lines 771-772)
```

**Automated checks (all must pass before Step 1):**
- [ ] 141 tests pass
- [ ] `grep -c 'check_and_record\|seed_prior_spend\|hard_cap_usd' ballast/core/cost.py` returns `0`
- [ ] `grep -c 'cost_guard.check\b' ballast/core/trajectory.py` returns `1`
- [ ] `grep -c 'cost_guard.record\b' ballast/core/trajectory.py` returns `1`

---

## Steps Analysis

```
Step 1 (hard_cap_usd constructor param)   — Critical   — full code review — Idempotent: Yes
Step 2 (check_and_record atomic method)   — Critical   — full code review — Idempotent: Yes
Step 3 (seed_prior_spend + resume wiring) — Critical   — full code review — Idempotent: Yes
Step 4 (warn once when cost_usd absent)   — Non-critical — verification  — Idempotent: Yes
```

---

## Tasks

### Phase 1 — Testability fix

---

- [ ] 🟥 **Step 1: Add `hard_cap_usd` constructor param to `RunCostGuard`; carry it through `HardCapExceeded`** — *Critical: tests that bypass via `_total` are replaced; the hard cap path becomes testable without private state access*

  **Step Architecture Thinking:**

  **Pattern applied:** Constructor injection. The production default ($300) stays as `HARD_CAP_USD`. Tests inject a small value, no monkey-patching.

  **Why this step is first:** Self-contained to `cost.py` + `test_cost.py`. Steps 2–4 don't depend on it, but doing it first means Step 2's new `check_and_record` tests can also use `hard_cap_usd` for clean hard-cap coverage.

  **Why `RunCostGuard` and not `AgentCostGuard`:** The hard cap is a global ceiling on the entire run, not a per-agent limit. It lives on the coordinator.

  **What breaks if deviated:** If `HardCapExceeded` doesn't store `self.hard_cap`, callers catching the exception can't report what the cap was — only what was spent. The error message becomes incomplete.

  ---

  **Idempotent:** Yes — replacements of unique strings.

  **Pre-Read Gate:**
  - `grep -n 'def __init__(self) -> None' ballast/core/cost.py` must return exactly 1 match (inside `RunCostGuard`). If 0 or 2+ → STOP.
  - `grep -n 'HARD_CAP_USD' ballast/core/cost.py` — note all lines; each must be updated.
  - `grep -c 'hard_cap_usd' ballast/core/cost.py` must return `0`. If not → Step 1 already applied → STOP.

  **Edit A — Update `HardCapExceeded.__init__` to accept and store `hard_cap`.**

  Replace:
  ```python
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
  ```
  With:
  ```python
  class HardCapExceeded(Exception):
      """Raised when the global run hard cap would be exceeded."""

      def __init__(self, total: float, estimated: float, hard_cap: float = HARD_CAP_USD) -> None:
          self.total = total
          self.estimated = estimated
          self.hard_cap = hard_cap
          super().__init__(
              f"hard run cap exceeded: "
              f"total={total:.6f} + estimated={estimated:.6f} > "
              f"hard_cap={hard_cap:.6f}"
          )
  ```

  **Edit B — Add `hard_cap_usd` param to `RunCostGuard.__init__`.**

  Replace:
  ```python
      def __init__(self) -> None:
          self._agents: dict[str, AgentCostGuard] = {}
          self._total: float = 0.0
  ```
  With:
  ```python
      def __init__(self, hard_cap_usd: float = HARD_CAP_USD) -> None:
          self._agents: dict[str, AgentCostGuard] = {}
          self._total: float = 0.0
          self.hard_cap_usd: float = hard_cap_usd
  ```

  **Edit C — Update `RunCostGuard.check()` to use `self.hard_cap_usd`.**

  Replace:
  ```python
          if self._total + estimated > HARD_CAP_USD:
              raise HardCapExceeded(self._total, estimated)
  ```
  With:
  ```python
          if self._total + estimated > self.hard_cap_usd:
              raise HardCapExceeded(self._total, estimated, self.hard_cap_usd)
  ```

  **Edit D — Update `RunCostGuard.report()` to use `self.hard_cap_usd`.**

  Replace:
  ```python
              "hard_cap": HARD_CAP_USD,
              "remaining": round(HARD_CAP_USD - self._total, 6),
  ```
  With:
  ```python
              "hard_cap": self.hard_cap_usd,
              "remaining": round(self.hard_cap_usd - self._total, 6),
  ```

  **Edit E — Update module docstring to reflect `hard_cap_usd` param.**

  Replace:
  ```python
      guard = RunCostGuard()
  ```
  With:
  ```python
      guard = RunCostGuard(hard_cap_usd=300.0)   # defaults to HARD_CAP_USD
  ```

  **Edit F — Rewrite 3 tests that bypass via `rg._total`; add 1 new test.**

  Replace:
  ```python
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
  ```
  With:
  ```python
  def test_run_guard_raises_hard_cap_before_agent_cap():
      """HardCapExceeded fires even when per-agent cap would allow it."""
      rg = RunCostGuard(hard_cap_usd=1.0)
      rg.register("worker", cap=10.0, escalation_pool=0.0)
      rg.record("worker", 0.999)
      with pytest.raises(HardCapExceeded):
          rg.check("worker", 0.002)


  def test_run_guard_hard_cap_exceeded_carries_total_and_estimated():
      rg = RunCostGuard(hard_cap_usd=1.0)
      rg.register("worker", cap=10.0, escalation_pool=0.0)
      rg.record("worker", 0.999)
      with pytest.raises(HardCapExceeded) as exc_info:
          rg.check("worker", 0.002)
      assert exc_info.value.total == pytest.approx(0.999)
      assert exc_info.value.estimated == pytest.approx(0.002)
      assert exc_info.value.hard_cap == pytest.approx(1.0)


  def test_run_guard_custom_hard_cap_respected():
      """RunCostGuard(hard_cap_usd=X) enforces X, not the module-level default."""
      rg = RunCostGuard(hard_cap_usd=0.50)
      rg.register("worker", cap=10.0, escalation_pool=0.0)
      rg.record("worker", 0.40)
      with pytest.raises(HardCapExceeded):
          rg.check("worker", 0.20)   # 0.40 + 0.20 = 0.60 > 0.50
      rg.check("worker", 0.09)       # 0.40 + 0.09 = 0.49 < 0.50 — must not raise
  ```

  Replace the integration test that uses `rg._total`:
  ```python
  def test_run_with_spec_hard_cap_stops_run(tmp_path, monkeypatch):
      """HardCapExceeded propagates when global hard cap is hit."""
      monkeypatch.chdir(tmp_path)
      spec = _make_spec()
      # hard_cap=1.0; pre-record 0.5; node costs 0.6 → 1.1 > 1.0 → raises
      nodes = [_RwsNode()]
      agent, _ = _rws_make_agent(nodes, node_costs=[0.6])
      rg = RunCostGuard(hard_cap_usd=1.0)
      rg.register("worker", cap=500.0, escalation_pool=0.0)
      rg.record("worker", 0.5)
      with patch("ballast.core.trajectory.score_drift", return_value=(1.0, "PROGRESSING", "")):
          with pytest.raises(HardCapExceeded):
              asyncio.run(run_with_spec(agent, "task", spec, cost_guard=rg, agent_id="worker"))
  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/core/cost.py tests/test_cost.py
  git commit -m "step 1: hard_cap_usd constructor param on RunCostGuard; HardCapExceeded carries hard_cap"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate && python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  Also confirm no `_total =` private access remains in tests:
  ```bash
  grep -n '_total' tests/test_cost.py
  ```

  **Pass:** Test count ≥ 141 (one new test added). Zero `_total` references in test file.

  **Fail:**
  - `HardCapExceeded.__init__() takes 3 positional arguments but 4 were given` → Edit A not applied → check `HardCapExceeded` signature.
  - `AttributeError: 'RunCostGuard' object has no attribute 'hard_cap_usd'` → Edit B not applied.
  - Test count drops → an existing test was deleted rather than replaced → `git diff tests/test_cost.py`.

---

### Phase 2 — Protocol safety

---

- [ ] 🟥 **Step 2: Add `check_and_record()` to both guards; update `run_with_spec` call-site** — *Critical: makes the correct call sequence the path of least resistance*

  **Step Architecture Thinking:**

  **Pattern applied:** Command. A single method encapsulates check-then-record atomically. The two-call protocol remains available for callers with genuine split needs (estimate vs actual), but is no longer the default path.

  **Why this step exists here in the sequence:**
  Independent of Step 1. Doing it after Step 1 means the new `check_and_record` tests can use `hard_cap_usd` for clean coverage of the hard cap path.

  **What breaks if deviated:**
  If `check_and_record` on `RunCostGuard` calls `self._agents[agent_id].check_and_record` instead of `self.check(...)` then `self.record(...)`, the global hard cap check in `RunCostGuard.check()` is bypassed — only the per-agent cap fires.

  ---

  **Idempotent:** Yes — new methods added; one replacement in trajectory.py.

  **Pre-Read Gate:**
  - `grep -c 'check_and_record' ballast/core/cost.py` must return `0`. If not → STOP.
  - `grep -n 'cost_guard.check\b' ballast/core/trajectory.py` must return exactly 1 match (line 771). If 0 or 2+ → STOP.
  - `grep -n 'cost_guard.record\b' ballast/core/trajectory.py` must return exactly 1 match (line 772).

  **Edit A — Add `check_and_record()` to `AgentCostGuard` after `record()`.**

  Insert immediately after:
  ```python
      def record(self, actual: float, is_escalation: bool = False) -> None:
          """Commit actual spend. Only call after check() has passed."""
          if is_escalation:
              self._escalation_spent += actual
          else:
              self._spent += actual
  ```

  The block to insert (goes immediately after the `record` method, before `@property spent`):
  ```python

      def check_and_record(self, actual: float, is_escalation: bool = False) -> None:
          """Raise if actual would exceed this agent's cap, then commit atomically.

          Preferred over separate check() + record() calls. If check() raises,
          record() is never called — state is never partially mutated.
          """
          self.check(actual, is_escalation)
          self.record(actual, is_escalation)

  ```

  **Edit B — Add `check_and_record()` to `RunCostGuard` after `record()`.**

  Insert immediately after:
  ```python
      def record(
          self, agent_id: str, actual: float, is_escalation: bool = False
      ) -> None:
          """Commit actual spend to global total and per-agent guard."""
          self._total += actual
          self._agents[agent_id].record(actual, is_escalation)
  ```

  The block to insert (goes immediately after, before `@property total_spent`):
  ```python

      def check_and_record(
          self, agent_id: str, actual: float, is_escalation: bool = False
      ) -> None:
          """Raise if actual would exceed any cap, then commit atomically.

          Preferred call-site for run_with_spec. Prevents partial mutation if
          a future await is inserted between check and record.
          Checks global hard cap first, then per-agent cap, then commits both.
          """
          self.check(agent_id, actual, is_escalation)
          self.record(agent_id, actual, is_escalation)

  ```

  **Edit C — Update `run_with_spec` step 6b to single `check_and_record` call.**

  Replace:
  ```python
              # ── 6b. Cost enforcement ────────────────────────────────────
              if cost_guard is not None:
                  cost_guard.check(agent_id, node_cost)
                  cost_guard.record(agent_id, node_cost)
  ```
  With:
  ```python
              # ── 6b. Cost enforcement ────────────────────────────────────
              if cost_guard is not None:
                  cost_guard.check_and_record(agent_id, node_cost)
  ```

  **Edit D — Update module docstring usage example in `cost.py`.**

  Replace:
  ```python
      # at each node boundary in run_with_spec:
      guard.check("worker", node_cost)   # raises if cap would be exceeded
      guard.record("worker", node_cost)  # commits spend only if check passed
  ```
  With:
  ```python
      # at each node boundary in run_with_spec:
      guard.check_and_record("worker", node_cost)   # atomic: raises before committing
  ```

  **Edit E — Add 2 unit tests for `check_and_record` in `test_cost.py`.**

  Append after `test_escalation_budget_exceeded_carries_agent_id`:
  ```python

  def test_agent_guard_check_and_record_raises_before_committing():
      """If check_and_record raises, _spent must be unchanged."""
      g = AgentCostGuard("worker", agent_cap_usd=0.10, escalation_pool_usd=0.03)
      g.record(0.10)  # fill cap
      with pytest.raises(AgentCapExceeded):
          g.check_and_record(0.001)
      assert g.spent == pytest.approx(0.10)  # record was never called


  def test_run_guard_check_and_record_raises_before_committing():
      """If RunCostGuard.check_and_record raises, total_spent must be unchanged."""
      rg = RunCostGuard(hard_cap_usd=1.0)
      rg.register("worker", cap=10.0, escalation_pool=0.0)
      rg.record("worker", 1.0)  # fill hard cap
      before = rg.total_spent
      with pytest.raises(HardCapExceeded):
          rg.check_and_record("worker", 0.001)
      assert rg.total_spent == pytest.approx(before)  # record was never called
  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/core/cost.py ballast/core/trajectory.py tests/test_cost.py
  git commit -m "step 2: add check_and_record() to both guards; run_with_spec uses single atomic call"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate && python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  Confirm two-call pattern is gone:
  ```bash
  grep -c 'cost_guard.check\b\|cost_guard.record\b' ballast/core/trajectory.py
  ```

  **Pass:** Test count ≥ 143 (two new tests). `grep` returns `0`.

  **Fail:**
  - `AttributeError: 'RunCostGuard' object has no attribute 'check_and_record'` → Edit B not applied.
  - `grep` still returns `1` or `2` → Edit C not applied → check anchor matched correctly.

---

### Phase 3 — Resume safety

---

- [ ] 🟥 **Step 3: Add `seed_prior_spend()` to `RunCostGuard`; wire into `run_with_spec` resume path** — *Critical: closes the silent over-budget-on-resume gap*

  **Step Architecture Thinking:**

  **Pattern applied:** Template Method. `run_with_spec` is the only caller that knows whether a resume is happening and what the prior spend was. `RunCostGuard` exposes a hook; `run_with_spec` decides when to call it.

  **Why this step exists here in the sequence:**
  `check_and_record` (Step 2) must exist first — the seeded total flows through `check_and_record` at the next node boundary. Logically step 3 depends on step 2.

  **Alternative rejected:**
  `RunCostGuard.__init__(prior_spend=0.0)`. Rejected: the caller constructs the guard before passing it to `run_with_spec`. It doesn't know yet whether the run will resume. Seeding inside `run_with_spec` — the place that reads the checkpoint — is the only place with that knowledge.

  **What breaks if deviated:**
  If `seed_prior_spend` does not check `self._total != 0.0`, a guard that has already recorded spend can be double-seeded. Budget tracking becomes permanently wrong.

  ---

  **Idempotent:** Yes — new method + new branch in existing if-block.

  **Pre-Read Gate:**
  - `grep -c 'seed_prior_spend' ballast/core/cost.py` must return `0`. If not → STOP.
  - Confirm resume branch anchor: `grep -n 'node_offset = progress.last_clean_node_index' ballast/core/trajectory.py` must return exactly 1 match. The seed call goes immediately after that line.
  - `grep -c 'seed_prior_spend' ballast/core/trajectory.py` must return `0`. If not → STOP.

  **Edit A — Add `seed_prior_spend()` to `RunCostGuard` after `check_and_record()`.**

  Insert immediately after the `check_and_record` method, before `@property total_spent`:
  ```python

      def seed_prior_spend(self, prior_spend: float) -> None:
          """Seed global total from a prior run segment (used by run_with_spec on resume).

          Must be called before any check_and_record() call on this guard.
          Raises ValueError if the guard already has recorded spend — double-seeding
          would corrupt budget tracking.
          """
          if self._total != 0.0:
              raise ValueError(
                  f"seed_prior_spend: guard already has _total={self._total:.6f}; "
                  "cannot seed a guard that has already recorded spend"
              )
          self._total = prior_spend

  ```

  **Edit B — Wire `seed_prior_spend` into the resume branch of `run_with_spec`.**

  Replace:
  ```python
          task = f"{progress.resume_context()}\n\nOriginal task: {task}"
          node_offset = progress.last_clean_node_index + 1
          logger.info(
              "run_with_spec resuming run_id=%s from node=%d spec_version=%s",
              progress.run_id, node_offset, spec.version_hash,
          )
  ```
  With:
  ```python
          task = f"{progress.resume_context()}\n\nOriginal task: {task}"
          node_offset = progress.last_clean_node_index + 1
          if cost_guard is not None:
              cost_guard.seed_prior_spend(progress.total_cost_usd)
              logger.info(
                  "cost_guard seeded prior_spend=%.6f from checkpoint run_id=%s",
                  progress.total_cost_usd, progress.run_id,
              )
          logger.info(
              "run_with_spec resuming run_id=%s from node=%d spec_version=%s",
              progress.run_id, node_offset, spec.version_hash,
          )
  ```

  **Edit C — Add 3 tests to `test_cost.py`.**

  Append after `test_run_guard_check_and_record_raises_before_committing`:
  ```python


  def test_run_guard_seed_prior_spend_sets_total():
      rg = RunCostGuard(hard_cap_usd=1.0)
      rg.seed_prior_spend(0.50)
      assert rg.total_spent == pytest.approx(0.50)


  def test_run_guard_seed_prior_spend_raises_if_guard_has_spend():
      rg = RunCostGuard(hard_cap_usd=1.0)
      rg.register("worker", cap=2.0, escalation_pool=0.0)
      rg.record("worker", 0.01)
      with pytest.raises(ValueError, match="already has"):
          rg.seed_prior_spend(0.50)


  def test_run_with_spec_seeds_cost_guard_on_resume(tmp_path, monkeypatch):
      """On resume, cost_guard._total is seeded from progress.total_cost_usd.

      Without seeding: node costs 0.04, hard_cap=0.10 → no raise (0.04 < 0.10).
      With seeding:    prior_spend=0.07 + 0.04 = 0.11 > 0.10 → HardCapExceeded.
      """
      monkeypatch.chdir(tmp_path)
      from ballast.core.checkpoint import BallastProgress
      from datetime import datetime, timezone
      spec = _make_spec()

      # Write a checkpoint from a "prior run segment" that spent $0.07
      prior = BallastProgress(
          spec_hash=spec.version_hash,
          active_spec_hash=spec.version_hash,
          spec_intent=spec.intent,
          run_id="prior",
          started_at=datetime.now(timezone.utc).isoformat(),
          updated_at=datetime.now(timezone.utc).isoformat(),
          total_cost_usd=0.07,
          last_clean_node_index=0,
          remaining_success_criteria=list(spec.success_criteria),
      )
      prior.write(str(tmp_path / "ballast-progress.json"))

      # hard_cap=0.10; prior spend seeded as 0.07; node costs 0.04 → 0.11 > 0.10
      nodes = [_RwsNode()]
      agent, _ = _rws_make_agent(nodes, node_costs=[0.04])
      rg = RunCostGuard(hard_cap_usd=0.10)
      rg.register("default", cap=1.0, escalation_pool=0.0)

      with patch("ballast.core.trajectory.score_drift", return_value=(1.0, "PROGRESSING", "")):
          with pytest.raises(HardCapExceeded):
              asyncio.run(run_with_spec(agent, "task", spec, cost_guard=rg))
  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/core/cost.py ballast/core/trajectory.py tests/test_cost.py
  git commit -m "step 3: seed_prior_spend() on RunCostGuard; run_with_spec seeds guard on resume"
  ```

  **✓ Verification Test:**

  **Type:** Unit + Integration

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate && python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  **Pass:** Test count ≥ 146 (three new tests). Zero failures.

  **Fail:**
  - `test_run_with_spec_seeds_cost_guard_on_resume` does not raise → seed not called → check Edit B anchor matched; confirm `cost_guard is not None` branch executes.
  - `ValueError: seed_prior_spend: guard already has _total` in integration test → guard was pre-seeded before `run_with_spec` → test setup error.

---

### Phase 4 — Silent failure detection

---

- [ ] 🟥 **Step 4: Warn once per run when `cost_usd` is absent from a node and the guard is active** — *Non-critical: converts a silent no-op into an observable warning*

  **Step Architecture Thinking:**

  **Pattern applied:** Fail-fast (soft). `cost_guard` is active but has no data to enforce against. One warning per run is the minimum signal needed to detect this in a log trace.

  **Why warn once, not per node:** A noisy per-node warning drowns signal. The first occurrence is the information; subsequent occurrences add no new information.

  **What breaks if deviated:** If `_cost_usd_warned` is not scoped inside `run_with_spec` (e.g. made a module-level variable), it persists across runs and the warning fires only on the very first affected run in a process. Each run should warn independently.

  ---

  **Idempotent:** Yes — replacement of unique string + new local variable.

  **Pre-Read Gate:**
  - `grep -n 'getattr.*cost_usd' ballast/core/trajectory.py` must return exactly 1 match. If 0 → already changed → STOP. If 2+ → STOP.
  - Confirm anchor: the line is inside `run_with_spec`, before step `# ── 4. Drift response`.
  - `grep -c '_cost_usd_warned' ballast/core/trajectory.py` must return `0`.

  **Edit A — Add `_cost_usd_warned` flag before the async for loop in `run_with_spec`.**

  Replace:
  ```python
      full_window: list = []
      compact_history: list[dict] = []
      node_index = node_offset
  ```
  With:
  ```python
      full_window: list = []
      compact_history: list[dict] = []
      node_index = node_offset
      _cost_usd_warned = False
  ```

  **Edit B — Replace `getattr` with explicit `hasattr` check + one-time warning.**

  Replace:
  ```python
              node_cost = getattr(node, "cost_usd", 0.0)
  ```
  With:
  ```python
              if hasattr(node, "cost_usd"):
                  node_cost = float(node.cost_usd)
              else:
                  node_cost = 0.0
                  if cost_guard is not None and not _cost_usd_warned:
                      logger.warning(
                          "cost_usd_missing node_type=%s node_index=%d run_id=%s"
                          " — cost guard is active but node exposes no cost_usd;"
                          " cap enforcement will not fire",
                          type(node).__name__, node_index, run_id,
                      )
                      _cost_usd_warned = True
  ```

  **Edit C — Add `_NoCostAgentRun` helper + 1 test to `test_cost.py`.**

  Append after the existing `_rws_make_agent` function definition (after line ~198):
  ```python


  class _NoCostAgentRun:
      """AgentRun that yields nodes WITHOUT cost_usd — simulates pydantic-ai not exposing it."""

      def __init__(self, nodes, output="done"):
          self._nodes = nodes
          self._output = output
          state = MagicMock()
          state.message_history = []
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
              yield node  # no cost_usd attribute set
  ```

  Append the test after `test_run_with_spec_seeds_cost_guard_on_resume`:
  ```python


  def test_run_with_spec_warns_once_when_cost_usd_missing(tmp_path, monkeypatch, caplog):
      """Warning fires exactly once (not per-node) when cost_usd is absent and guard active."""
      import logging
      monkeypatch.chdir(tmp_path)
      spec = _make_spec()
      nodes = [_RwsNode(), _RwsNode()]   # two nodes, neither will have cost_usd

      run = _NoCostAgentRun(nodes)
      agent = MagicMock()

      from contextlib import asynccontextmanager

      @asynccontextmanager
      async def _iter(task):
          yield run

      agent.iter = _iter

      rg = RunCostGuard(hard_cap_usd=10.0)
      rg.register("worker", cap=1.0, escalation_pool=0.0)

      with patch("ballast.core.trajectory.score_drift", return_value=(1.0, "PROGRESSING", "")):
          with caplog.at_level(logging.WARNING, logger="ballast.core.trajectory"):
              asyncio.run(run_with_spec(agent, "task", spec, cost_guard=rg, agent_id="worker"))

      warnings = [r for r in caplog.records if "cost_usd_missing" in r.message]
      assert len(warnings) == 1   # fires once, not twice for two nodes
  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/core/trajectory.py tests/test_cost.py
  git commit -m "step 4: warn once per run when cost_usd absent and cost guard is active"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate && python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  Confirm `getattr` call is gone:
  ```bash
  grep -c 'getattr.*cost_usd' ballast/core/trajectory.py
  ```

  **Pass:** Test count ≥ 147 (one new test). `grep` returns `0`.

  **Fail:**
  - `len(warnings) == 2` → `_cost_usd_warned` flag not set after first warning → check Edit B.
  - `len(warnings) == 0` → warning not firing → confirm `cost_guard is not None` in test setup; confirm `caplog.at_level` logger name matches `ballast.core.trajectory`.

---

## Regression Guard

| System | Pre-change behaviour | Post-change verification |
|---|---|---|
| Existing callers with no `cost_guard` | `run_with_spec` completes normally | `test_run_with_spec_no_cost_guard_is_backward_compatible` passes |
| `RunCostGuard()` default construction | Hard cap = $300 | `RunCostGuard().hard_cap_usd == HARD_CAP_USD` |
| `check()` / `record()` still callable | Two-step protocol available | Existing `test_agent_guard_check_does_not_mutate_spent` passes |

**Test count regression check:** Run `python -m pytest tests/ -m 'not integration' -q` after each step. Count must be ≥ baseline + new tests added in that step.

---

## Success Criteria

| Fix | Target | Verification |
|---|---|---|
| Testable hard cap | `RunCostGuard(hard_cap_usd=1.0)` raises at $1 | `test_run_guard_custom_hard_cap_respected` passes |
| Atomic protocol | `cost_guard.check_and_record(...)` single call in `run_with_spec` | `grep -c 'cost_guard.check\b' ballast/core/trajectory.py` returns `0` |
| Resume-aware | Over-budget resume raises `HardCapExceeded` | `test_run_with_spec_seeds_cost_guard_on_resume` passes |
| Silent no-op detected | Warning fires once when `cost_usd` absent | `test_run_with_spec_warns_once_when_cost_usd_missing` passes |
| No private state in tests | No `rg._total =` in test file | `grep -c '_total' tests/test_cost.py` returns `0` |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Steps 1–4 are sequential — each step's pre-read gate confirms the prior step landed.**
⚠️ **Do not batch multiple steps into one git commit.**
