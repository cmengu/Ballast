# Day 3 — `hook.py`: Agent iteration with live spec injection

**Overall Progress:** `0%` (0/2 steps)

---

## TLDR

Create `ballast/core/hook.py` implementing `run_with_live_spec()` — the function that wires
`Agent.iter` + `SpecPoller` + `SpecDelta` injection. At every node boundary it polls for a
spec update, injects any delta into `message_history`, stamps an audit log entry, and prints
`node 00 | spec:a3f2xxxx | NodeTypeName`. Then create `tests/test_hook.py` with 8 unit tests
(all mocked — no LLM, no HTTP). Test count goes from 90 → 98.

---

## Architecture Overview

**The problem this plan solves:**
`trajectory.py` (drift detection) and `sync.py` (SpecPoller) exist as independent modules
with no wire-up. `hook.py` is the glue layer that runs them together inside the `Agent.iter`
loop. Without it, there is no observable node output and no live spec injection.

**The pattern applied:**
Coordinator — `run_with_live_spec` calls into three separate modules (`Agent.iter`, `SpecPoller`,
`SpecDelta.as_injection`) without owning their logic. Each dependency is passed in; nothing is
instantiated inside the function.

**What stays unchanged:**
- `spec.py` — SpecModel, SpecDelta, lock() — no edits
- `trajectory.py` — TrajectoryChecker — no edits
- `sync.py` — SpecPoller — no edits
- `server.py` — FastAPI app — no edits

**What this plan adds:**
- `ballast/core/hook.py` — `run_with_live_spec()`: coordinates Agent.iter + poll + inject + log
- `tests/test_hook.py` — 8 unit tests, fully mocked

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|----------|----------------------|--------------------------|
| `run.ctx.state.message_history.append(...)` for injection | Pass `message_history` as init param to `Agent.iter` | Init param doesn't support mid-run injection; ctx path confirmed via pydantic-ai source |
| `run.result.output` for final output | `await run.get_output()` (mvp.md pseudocode) | `AgentRun` has no `get_output()` method; `.result.output` is the confirmed dataclass field |
| Sync tests with `asyncio.run()` | `@pytest.mark.asyncio` / `asyncio_mode=auto` | All existing tests are sync; no config change needed; consistent with project style |
| Drop `job_id` from `run_with_live_spec` signature | Keep `job_id` per mvp.md | SpecPoller already encodes job_id in its URL; passing it again is redundant |

**Known limitations:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| `run_with_live_spec` does not call `TrajectoryChecker` | hook.py is the glue layer; drift detection is trajectory.py's job | Day 4 demo.py wires both together |
| `on_node` must be `async` or `None` | Matches mvp.md contract; async-only simplifies the call | Add `asyncio.iscoroutinefunction` check if sync callbacks are needed later |
| `run.result` not guarded against `None` | `None` only occurs on mid-run exception, which propagates before `return` is reached | Day 4 add defensive fallback if needed |

---

## API facts confirmed by source inspection

Before writing any code, the following were verified against the installed pydantic-ai:

| Fact | Confirmed path |
|------|---------------|
| message_history injection | `run.ctx.state.message_history.append(ModelRequest(...))` — `ctx` returns `GraphRunContext`; `.state` is `GraphAgentState` (dataclass with `.message_history: list[ModelMessage]`) |
| `ModelRequest` + `UserPromptPart` | `from pydantic_ai.messages import ModelRequest, UserPromptPart` — importable, constructable |
| `parts[0].content` access | Confirmed — `ModelRequest(parts=[UserPromptPart(content=s)]).parts[0].content == s` |
| Final output | `run.result.output` — `AgentRun.result` is `AgentRunResult` (dataclass); `.output` is the first field |
| SpecModel field name | `.version` (not `.version_hash`) — confirmed in spec.py |
| SpecDelta field names | `.from_version`, `.to_version` (not `.from_hash`, `.to_hash`) — 4 occurrences confirmed in spec.py |

---

## Agent Failure Protocol

1. A verification command fails → read the full error output.
2. Cause is unambiguous → make ONE targeted fix → re-run the same verification command.
3. If still failing after one fix → **STOP**. Output full contents of every modified file. Report: (a) command run, (b) full error verbatim, (c) fix attempted, (d) current state of each modified file, (e) why you cannot proceed.
4. Never attempt a second fix without human instruction.
5. Never modify files not named in the current step.

---

## Pre-Flight — Run Before Any Code Changes

```bash
# Confirm current test baseline (venv activation must be chained — fresh shell each call)
source venv/bin/activate && pytest tests/ -m "not integration" -q --tb=no 2>&1 | tail -3
# Expected: 90 passed

# Confirm hook.py does NOT yet exist
ls ballast/core/hook.py 2>&1
# Expected: No such file or directory

# Confirm imports that hook.py will use (each command chains venv activation)
source venv/bin/activate && python -c "from pydantic_ai.messages import ModelRequest, UserPromptPart; print('ok')"
source venv/bin/activate && python -c "from ballast.core.spec import SpecModel, SpecDelta; print('ok')"
source venv/bin/activate && python -c "from ballast.core.sync import SpecPoller; print('ok')"
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan: 90
hook.py exists: No
ModelRequest importable: ok
SpecModel importable: ok
SpecPoller importable: ok
```

---

## Tasks

### Phase 1 — Implementation

---

- [ ] 🟥 **Step 7.1: Create `ballast/core/hook.py`** — *Critical: new public API consumed by demo.py and tests*

  **Step Architecture Thinking:**

  **Pattern applied:** Coordinator — `run_with_live_spec` owns the loop and delegates to
  `SpecPoller.poll()`, `SpecModel.diff()`, `SpecDelta.as_injection()`, and the pydantic-ai
  `Agent.iter` context manager. It never owns the logic of any of these.

  **Why this step exists here in the sequence:**
  `spec.py` (SpecDelta, diff) and `sync.py` (SpecPoller) must exist before this step. Both
  are done. After this step, `tests/test_hook.py` (Step 7.2) can import `run_with_live_spec`.

  **Why `ballast/core/hook.py` is the right location:**
  Consistent with the module layout in README.md. All four core modules (`spec.py`,
  `trajectory.py`, `sync.py`, `hook.py`) live in `ballast/core/`. Callers import from one
  canonical path.

  **Alternative approach considered and rejected:**
  Merge `run_with_live_spec` into `trajectory.py`. Rejected because trajectory.py is the
  drift-detection module (TrajectoryChecker); mixing polling + injection + audit logging into
  it would break the single-responsibility boundary already established.

  **What breaks if this step deviates:**
  If `run.result.output` is replaced with `await run.get_output()`, the call raises
  `AttributeError` — `AgentRun` has no `get_output` method in this pydantic-ai version.

  ---

  **Idempotent:** Yes — creating a new file is idempotent if re-run after deletion.

  **Context:** `hook.py` is a new file. No existing file is modified.

  **Pre-Read Gate:**
  Before writing:
  - `ls ballast/core/hook.py` → must show "No such file". If file exists → STOP.
  - `grep -En "from_version|to_version" ballast/core/spec.py` → must return at least 2 matches (both field names present in SpecDelta). Confirm before referencing them in hook.py. (Note: use `-E` flag, not `\|` BRE syntax — macOS grep requires ERE for alternation.)
  - `grep -n "class SpecPoller" ballast/core/sync.py` → must return 1 match.

  **No-Placeholder Rule:** All code below is complete and immediately runnable. No `<VALUE>` tokens.

  ---

  ```python
  """ballast/core/hook.py — Agent iteration hook with live spec injection.

  Public interface:
      run_with_live_spec(agent, task, spec, poller, on_node=None)

  Wires Agent.iter + SpecPoller + SpecDelta injection:
      - At every node boundary: poll for spec update
      - On spec change: inject SpecDelta.as_injection() into message_history
      - Stamp every node in the audit log with active spec_hash + node_type
      - Print: "node 00 | spec:a3f2xxxx | NodeTypeName"
      - Call optional async on_node(node_index, node, active_spec, delta) callback

  Returns:
      (output, audit_log)
      audit_log: list of {node_index, spec_hash, node_type, delta_injected}

  Injection mechanism (confirmed against pydantic-ai source):
      run.ctx.state.message_history.append(
          ModelRequest(parts=[UserPromptPart(content=injection)])
      )
      Path: AgentRun.ctx → GraphRunContext → .state (GraphAgentState) → .message_history
  """
  from __future__ import annotations

  from typing import Any, Callable, Optional

  from pydantic_ai import Agent
  from pydantic_ai.messages import ModelRequest, UserPromptPart

  from ballast.core.spec import SpecDelta, SpecModel
  from ballast.core.sync import SpecPoller


  async def run_with_live_spec(
      agent: Agent,
      task: str,
      spec: SpecModel,
      poller: SpecPoller,
      on_node: Optional[Callable] = None,
  ) -> tuple[Any, list[dict]]:
      """Run agent with live spec polling at every node boundary.

      Polls poller at every node. On spec version change:
        - computes SpecDelta via active_spec.diff(new_spec)
        - injects delta.as_injection() into run.ctx.state.message_history
        - updates active_spec to the new spec

      Args:
          agent:    A pydantic-ai Agent instance.
          task:     The task string to run.
          spec:     A locked SpecModel — used as initial active spec.
          poller:   Initialised SpecPoller (set_initial already called by caller).
          on_node:  Optional async callback: fn(node_index, node, active_spec, delta).
                    delta is None if no spec update occurred at this node.

      Returns:
          (output, audit_log)
          Each audit_log entry: {node_index, spec_hash, node_type, delta_injected}
          delta_injected: "fromhash→tohash" string, or None if no update at that node.
      """
      active_spec = spec
      node_index = 0
      audit_log: list[dict] = []

      async with agent.iter(task) as run:
          async for node in run:
              # Poll for spec update at every node boundary
              delta: Optional[SpecDelta] = None
              new_spec = poller.poll()
              if new_spec:
                  delta = active_spec.diff(new_spec)
                  active_spec = new_spec
                  injection = delta.as_injection()
                  run.ctx.state.message_history.append(
                      ModelRequest(parts=[UserPromptPart(content=injection)])
                  )

              # Stamp this node in the audit log
              audit_log.append({
                  "node_index": node_index,
                  "spec_hash": active_spec.version,
                  "node_type": type(node).__name__,
                  "delta_injected": (
                      f"{delta.from_version[:8]}→{delta.to_version[:8]}"
                      if delta else None
                  ),
              })

              print(
                  f"  node {node_index:02d} | spec:{active_spec.version[:8]}"
                  f" | {type(node).__name__}"
              )

              if on_node:
                  await on_node(node_index, node, active_spec, delta)

              node_index += 1

      return run.result.output, audit_log
  ```

  ---

  **What it does:** Wraps `Agent.iter` to poll for spec changes at every node, inject deltas
  into message history, build an audit log, and return `(output, audit_log)`.

  **Why this approach:** Minimal coordinator — each concern is in its own module. The function
  is ~40 lines and has no logic that belongs elsewhere.

  **Assumptions:**
  - `agent.iter(task)` is an async context manager yielding an `AgentRun` (confirmed via trajectory.py usage).
  - `run.ctx.state.message_history` is a mutable list (confirmed via pydantic-ai source).
  - `run.result.output` is populated after the async-for loop completes (confirmed via `AgentRunResult` dataclass).
  - Caller has already called `poller.set_initial(spec)` before passing poller in.

  **Risks:**
  - pydantic-ai upgrades may rename `GraphAgentState.message_history` → mitigation: pinned in `pyproject.toml` deps
  - `run.result` is `None` if agent exits without producing output → only possible on mid-run exception, which propagates before `return` is reached

  **Verification (chain venv activation — each is a single Bash call):**
  ```bash
  source venv/bin/activate && python -c "from ballast.core.hook import run_with_live_spec; print('import ok')"
  ```
  Expected: `import ok`

  **Git Checkpoint:**
  ```bash
  git add ballast/core/hook.py
  git commit -m "step 7.1: create hook.py — run_with_live_spec with live spec injection"
  ```

---

- [ ] 🟥 **Step 7.2: Create `tests/test_hook.py`** — *Non-critical: additive tests only, no existing code modified*

  **Step Architecture Thinking:**

  **Pattern applied:** Mock-based unit tests — `Agent` and `SpecPoller` are replaced with
  lightweight fakes. No LLM calls, no HTTP calls, no network. All 8 tests are sync functions
  using `asyncio.run()`, matching the project's existing test style.

  **Why this step exists here in the sequence:**
  `hook.py` (Step 7.1) must exist before this step imports `run_with_live_spec`.

  **Why `tests/test_hook.py` is the right location:**
  Consistent with `test_spec.py`, `test_trajectory.py`, `test_sync.py` — one test file per
  core module.

  **Alternative approach considered and rejected:**
  `@pytest.mark.asyncio` with async test functions. Rejected because no existing test uses
  it; adding `asyncio_mode = "auto"` to pytest config would be a project-wide change for one
  test file. `asyncio.run()` achieves the same with zero config change.

  **What breaks if this step deviates:**
  If `_MockAgentRun.ctx` returns a new `MagicMock()` each call without stable state, the
  `message_history.append(...)` will write to a throwaway list and `run.message_history` will
  stay empty, causing `test_spec_update_injects_model_request` to fail.

  ---

  **Idempotent:** Yes — creating a new file.

  **Context:** New file. No existing files are modified.

  **Pre-Read Gate:**
  Before writing:
  - `ls tests/test_hook.py` → must show "No such file". If exists → STOP.
  - `grep -n "def run_with_live_spec" ballast/core/hook.py` → must return 1 match (Step 7.1 complete).

  **Self-Contained Rule:** All helpers are defined in this file. No cross-step references.

  **Note on `test_spec_update_injects_model_request`:** `spec_v1` and `spec_v2` in this test
  have the same `intent` + `success_criteria`, so `spec_v1.version == spec_v2.version`. This
  is intentional — the version hash is not what we're testing here. We're testing that the
  injection content contains the added constraint. The `delta_injected` field in the audit log
  will show `"XXXXXXXX→XXXXXXXX"` (same hash both sides) — this is correct behavior, not a bug.

  ---

  ```python
  """tests/test_hook.py — run_with_live_spec unit tests.

  All 8 tests are unit tests: no LLM calls, no HTTP calls.
  Agent and SpecPoller are replaced with lightweight fakes.
  Tests are sync functions using asyncio.run() — matches project convention.

  Mock design:
      _MockAgentRun: async-iterable; stable ctx.state.message_history (same list every call);
                     result.output returns configured string.
      _make_agent:   builds a mock Agent whose .iter() is an asynccontextmanager.
      _make_poller:  MagicMock with poll().side_effect returning a list in order.
  """
  from __future__ import annotations

  import asyncio
  from contextlib import asynccontextmanager
  from unittest.mock import MagicMock

  from pydantic_ai.messages import ModelRequest

  from ballast.core.hook import run_with_live_spec
  from ballast.core.spec import SpecModel, lock


  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  def _locked_spec(**overrides) -> SpecModel:
      base = dict(
          intent="Write a report",
          success_criteria=["report exists"],
          constraints=[],
          allowed_tools=[],
      )
      base.update(overrides)
      return lock(SpecModel(**base))


  class _MockNode:
      """Minimal stand-in for a pydantic-ai node."""


  class _MockAgentRun:
      """Fake AgentRun: async-iterable, stable ctx.state.message_history, result.output."""

      def __init__(self, nodes: list, output: str = "done") -> None:
          self._nodes = nodes
          self.message_history: list = []

          # Stable ctx so message_history.append() writes to the same list every call.
          # A new MagicMock() per ctx access would create a fresh message_history each time,
          # causing append() to write to a throwaway list — this is why _ctx is pre-built.
          state = MagicMock()
          state.message_history = self.message_history
          ctx = MagicMock()
          ctx.state = state
          self._ctx = ctx

          result = MagicMock()
          result.output = output
          self.result = result

      @property
      def ctx(self):
          return self._ctx

      def __aiter__(self):
          return self._gen()

      async def _gen(self):
          for node in self._nodes:
              yield node


  def _make_agent(nodes: list, output: str = "done") -> tuple:
      """Return (mock_agent, mock_run). agent.iter is an asynccontextmanager."""
      run = _MockAgentRun(nodes, output)
      agent = MagicMock()

      @asynccontextmanager
      async def _iter(task):
          yield run

      agent.iter = _iter
      return agent, run


  def _make_poller(return_values: list) -> MagicMock:
      """SpecPoller mock whose poll() returns values from the list in order."""
      poller = MagicMock()
      poller.poll.side_effect = return_values
      return poller


  # ---------------------------------------------------------------------------
  # Tests
  # ---------------------------------------------------------------------------

  def test_audit_log_length_matches_nodes():
      """audit_log has exactly one entry per node."""
      nodes = [_MockNode(), _MockNode(), _MockNode()]
      agent, _ = _make_agent(nodes)
      poller = _make_poller([None, None, None])
      spec = _locked_spec()

      _, audit_log = asyncio.run(run_with_live_spec(agent, "task", spec, poller))

      assert len(audit_log) == 3


  def test_audit_log_entry_fields():
      """Each audit_log entry has correct keys and correct types when no spec change."""
      nodes = [_MockNode()]
      agent, _ = _make_agent(nodes)
      poller = _make_poller([None])
      spec = _locked_spec()

      _, audit_log = asyncio.run(run_with_live_spec(agent, "task", spec, poller))

      entry = audit_log[0]
      assert entry["node_index"] == 0
      assert entry["spec_hash"] == spec.version
      assert entry["node_type"] == "_MockNode"
      assert entry["delta_injected"] is None


  def test_spec_update_switches_hash_in_audit_log():
      """Audit log reflects new spec_hash from the node where poller returns a new spec."""
      nodes = [_MockNode(), _MockNode(), _MockNode()]
      # Use different intent + success_criteria to ensure different version hashes
      spec_v1 = _locked_spec(intent="Task A", success_criteria=["done A"])
      spec_v2 = _locked_spec(intent="Task B", success_criteria=["done B"])

      agent, _ = _make_agent(nodes)
      poller = _make_poller([None, spec_v2, None])  # spec changes at node 1

      _, audit_log = asyncio.run(run_with_live_spec(agent, "task", spec_v1, poller))

      assert audit_log[0]["spec_hash"] == spec_v1.version
      assert audit_log[1]["spec_hash"] == spec_v2.version
      assert audit_log[2]["spec_hash"] == spec_v2.version


  def test_spec_update_injects_model_request():
      """When spec changes, a ModelRequest containing the constraint is appended to message_history.

      Note: spec_v1 and spec_v2 share the same version hash (same intent+criteria).
      The version hash is not what we're testing — we're testing injection content.
      delta_injected in the audit log will show 'XXXXXXXX→XXXXXXXX' (same hash) — correct.
      """
      nodes = [_MockNode(), _MockNode()]
      spec_v1 = _locked_spec(intent="Task A", success_criteria=["done A"])
      spec_v2 = _locked_spec(
          intent="Task A",
          success_criteria=["done A"],
          constraints=["do not mention X"],
      )

      agent, run = _make_agent(nodes)
      poller = _make_poller([spec_v2, None])  # spec changes at node 0

      asyncio.run(run_with_live_spec(agent, "task", spec_v1, poller))

      assert len(run.message_history) == 1
      injected = run.message_history[0]
      assert isinstance(injected, ModelRequest)
      assert "do not mention X" in injected.parts[0].content


  def test_no_injection_when_poller_returns_none():
      """When poller always returns None, message_history stays empty."""
      nodes = [_MockNode(), _MockNode()]
      agent, run = _make_agent(nodes)
      poller = _make_poller([None, None])
      spec = _locked_spec()

      asyncio.run(run_with_live_spec(agent, "task", spec, poller))

      assert run.message_history == []


  def test_return_value_tuple():
      """Returns (output, audit_log) where output matches agent result and audit_log is a list."""
      nodes = [_MockNode()]
      agent, _ = _make_agent(nodes, output="my result")
      poller = _make_poller([None])
      spec = _locked_spec()

      output, audit_log = asyncio.run(run_with_live_spec(agent, "task", spec, poller))

      assert output == "my result"
      assert isinstance(audit_log, list)


  def test_print_format(capsys):
      """Each node prints '  node NN | spec:XXXXXXXX | ClassName'."""
      nodes = [_MockNode()]
      spec = _locked_spec()
      agent, _ = _make_agent(nodes)
      poller = _make_poller([None])

      asyncio.run(run_with_live_spec(agent, "task", spec, poller))

      captured = capsys.readouterr()
      assert f"  node 00 | spec:{spec.version[:8]} | _MockNode" in captured.out


  def test_on_node_callback_called_with_correct_args():
      """on_node is called once per node with (node_index, node, active_spec, delta)."""
      nodes = [_MockNode(), _MockNode()]
      spec = _locked_spec()
      agent, _ = _make_agent(nodes)
      poller = _make_poller([None, None])

      calls: list = []

      async def on_node(node_index, node, active_spec, delta):
          calls.append((node_index, type(node).__name__, active_spec.version, delta))

      asyncio.run(run_with_live_spec(agent, "task", spec, poller, on_node=on_node))

      assert len(calls) == 2
      assert calls[0][0] == 0
      assert calls[1][0] == 1
      assert calls[0][2] == spec.version
      assert calls[0][3] is None   # no delta at node 0
  ```

  ---

  **What it does:** 8 unit tests covering audit log correctness, spec injection, message history
  mutation, return value, print format, and on_node callback.

  **Why this approach:** Fully mocked — runs in milliseconds, no API keys needed, consistent
  with how trajectory.py tests mock the LLM judges.

  **Assumptions:**
  - `asyncio.run()` works with `async def` functions in Python 3.12 — confirmed.
  - `_MockAgentRun.ctx` returns the same stable `_ctx` object every call (it does — `_ctx` is
    set in `__init__` and never reassigned).

  **Risks:**
  - `asyncio.run()` cannot be called from inside a running event loop (e.g. Jupyter). Not
    applicable — all tests run under pytest's sync runner.

  **Verification (each command chains venv activation — single Bash call per line):**
  ```bash
  source venv/bin/activate && pytest tests/test_hook.py -v --tb=short 2>&1 | tail -15
  ```
  Expected: `8 passed` with these test names:
  - `test_audit_log_length_matches_nodes`
  - `test_audit_log_entry_fields`
  - `test_spec_update_switches_hash_in_audit_log`
  - `test_spec_update_injects_model_request`
  - `test_no_injection_when_poller_returns_none`
  - `test_return_value_tuple`
  - `test_print_format`
  - `test_on_node_callback_called_with_correct_args`

  Full suite:
  ```bash
  source venv/bin/activate && pytest tests/ -m "not integration" -q --tb=no 2>&1 | tail -3
  ```
  Expected: `98 passed`

  **Git Checkpoint:**
  ```bash
  git add tests/test_hook.py
  git commit -m "step 7.2: add test_hook.py — 8 unit tests for run_with_live_spec"
  ```

---

## Completion Checklist

- [ ] `source venv/bin/activate && python -c "from ballast.core.hook import run_with_live_spec; print('ok')"` → `ok`
- [ ] `source venv/bin/activate && pytest tests/test_hook.py -v --tb=short` → 8 passed, 0 failed
- [ ] `source venv/bin/activate && pytest tests/ -m "not integration" -q --tb=no` → 98 passed (90 baseline + 8 new)
- [ ] Print output visible: `node 00 | spec:XXXXXXXX | _MockNode` in test_print_format
- [ ] No existing tests broken (full suite still 98 passed, 3 deselected)

---

## Decisions Log (pre-check resolutions)

| Flaw | Resolution applied |
|------|--------------------|
| Venv activation not chained | All `python`/`pytest` commands in Pre-Flight and Step verifications now use `source venv/bin/activate &&` on the same line |
| `grep \|` BRE syntax broken on macOS | Pre-Read Gate now uses `grep -En "from_version\|to_version"` → `grep -En "from_version|to_version"` (ERE, confirmed working) |
| Architecture thinking name drift | Step 7.2 thinking now correctly references `test_spec_update_injects_model_request` |
| Unused `import pytest` | Removed from test_hook.py |
| Same-version-hash in injection test | Documented with inline comment explaining intent |
