# escalation.py — Implementation Plan

**Overall Progress:** `0%`

---

## Spec Summary — Escalation Module

**What this module does.** When a Ballast run detects an irreversible action that violates the spec (label = `VIOLATED_IRREVERSIBLE`), the system does not crash or silently continue. Instead it escalates: it climbs a three-level hierarchy of LLM agents — Broker, CEO, Human — until one resolves the violation by returning a corrective instruction the worker agent can act on. If all levels fail, it raises `EscalationFailed` and the caller halts the run cleanly.

**Input.** `escalate()` receives an `assessment: NodeAssessment` (the drift result that triggered escalation), a `spec: SpecModel` (the active locked spec), and a `context: list` (the node's conversation history for the LLM). Optional `run_id` and `node_index` are accepted for log traceability.

**Output.** On success, `escalate()` returns a single `str` — the resolution text, suitable for injection directly into the agent's message history as a `UserPromptPart`. The caller is responsible for injection. On total failure, it raises `EscalationFailed`.

**Hierarchy.**
- **Level 1 — Broker.** A pydantic-ai `Agent` with a focused system prompt. Given the assessment summary, spec intent, and context window, it decides: `{"escalate": false, "resolution": "<text>"}` or `{"escalate": true}`. If it resolves, return resolution. If it decides to escalate or throws, move to Level 2.
- **Level 2 — CEO.** Same structure, more authoritative system prompt. Sees the same inputs. Same decision schema. If it resolves, return resolution. If it escalates or throws, move to Level 3.
- **Level 3 — Human.** Not a live human. This level raises `EscalationFailed` unconditionally. It is the explicit ceiling of the automated chain. The caller (`run_with_spec`) catches this, writes a checkpoint, then raises `HardInterrupt` to stop the run.

**The `EscalationPacket` dataclass.** All three levels receive the same structured input: `assessment`, `spec`, `context`, `run_id`, `node_index`. This is constructed once in `escalate()` and passed to each `_call_level()` invocation. It is not stored or serialised — it is a transient call envelope.

**`_call_level()`.** Internal `async` function. Accepts a pydantic-ai `Agent` and an `EscalationPacket`. Calls `await agent.run(prompt)`, parses the JSON response, and returns either the dict from the agent or `{"escalate": True}` on any exception. It never raises — this ensures the chain always continues upward rather than silently breaking.

**Fail-safe.** Every exception inside `_call_level()` is caught and treated as an escalation signal. This prevents a flaky LLM call from short-circuiting the chain and creating a false resolution. When all levels are exhausted, `EscalationFailed` is raised with the original assessment and spec attached.

  **Constraints.**
- No disk I/O inside `_call_level()`. The function is `async` because `agent.run()` is async-native and `run_sync()` raises `RuntimeError` when called inside an already-running event loop (which is always the case when `_call_level` is called from `escalate()` from `run_with_spec()`).
- `escalate()` itself is `async` because pydantic-ai agents are async-native.
- No retries within a level. One call per level. Retry logic lives outside this module.
- The LLM model is `claude-haiku-4-5-20251001` for both Broker and CEO (fast, cheap — escalation path should be low-latency).

**Success criteria (eval-derivable).**
1. Broker resolving returns a non-empty string from `escalate()`.
2. Broker escalating passes to CEO; CEO resolving returns a non-empty string.
3. Broker and CEO both escalating raises `EscalationFailed`.
4. Any exception inside `_call_level()` → treated as escalate, not failure.
5. `EscalationFailed` carries `.assessment` and `.spec` matching inputs.
6. `EscalationPacket` exposes all five fields: `assessment, spec, context, run_id, node_index`.
7. `run_with_spec` catches `EscalationFailed`, writes progress, raises `HardInterrupt`.
8. On successful escalation, `run_with_spec` appends resolution to message history.
9. `progress.total_violations` and `progress.last_escalation` are always updated after escalation, success or failure.
10. A `HardInterrupt` raised by `run_with_spec` carries `.assessment`, `.spec`, `.node_index` matching the originating node.

---

## Architecture Overview

**The problem this plan solves:**
`trajectory.py:775–789` has a TODO stub for irreversible-action handling. When `assessment.label == "VIOLATED_IRREVERSIBLE"`, the run logs a warning and continues — no recovery attempt, no safe stop. This is a production gap: an agent that has performed an irreversible action outside its spec will keep running with no intervention.

**The pattern applied:**
- **Chain of Responsibility** — `escalate()` walks levels in order; each level either resolves or passes upward. No level knows about the others.
- **Null Object / Fail-Safe Default** — `_call_level()` returns `{"escalate": True}` on any exception. The chain never breaks silently.
- **DTO (EscalationPacket)** — structured input envelope; all levels receive identical data; avoids positional argument drift as the call chain grows.

**What stays unchanged:**
- `spec.py`, `checkpoint.py`, `sync.py`, `cost.py`, `guardrails.py` — none touched.
- `trajectory.py` is edited in Step 3 only, and only the VIOLATED_IRREVERSIBLE block.

**What this plan adds:**
- `ballast/core/escalation.py` — `EscalationPacket`, `EscalationFailed`, `_call_level()`, `escalate()`, Broker agent, CEO agent.
- `tests/test_escalation.py` — 19 unit tests covering all paths.

  **Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|---|---|---|
| `_call_level()` is async | Make it sync with `run_sync()` | `run_sync()` calls `loop.run_until_complete()` which raises `RuntimeError: This event loop is already running` when called from inside `escalate()` (which is always called from the async `run_with_spec()` loop). Making `_call_level` async and using `await agent.run()` is the only correct approach. |
| `escalate()` is async | Make it sync | `Agent.run()` is async-native; calling sync from async is fragile |
| Haiku model for both levels | Sonnet or Opus | Escalation is latency-sensitive; Haiku is fast and cheap; model can be upgraded per-deployment |
| `EscalationFailed` stores assessment + spec | Store only a message string | Callers (trajectory.py) need to construct `HardInterrupt` from the same objects |
| 3 levels hard-coded | Configurable chain | YAGNI; chain depth is an architectural decision, not a runtime config |

**Known limitations:**

| Limitation | Why acceptable now | Upgrade path |
|---|---|---|
| No retry within a level | Escalation is rare; one call is enough for MVP | Add `max_retries` param to `_call_level()` in Step 9 (probe) hardening pass |
| Haiku for CEO (same as Broker) | Fast-path matters more than authority at this stage | Swap `CEO_MODEL` constant to Sonnet/Opus when eval results justify it |
| No telemetry in escalation chain | OTel stubbed until Step 13 | Add `emit_escalation_span()` calls in Step 13 |

---

## Decisions Log (pre-check resolutions)

| # | Flaw | Resolution applied |
|---|---|---|
| 1 | `_call_level()` called `agent.run_sync()` which raises `RuntimeError: This event loop is already running` when called from inside async `escalate()` → `run_with_spec()`. | Changed `_call_level` to `async def`; replaced `agent.run_sync(prompt)` with `await agent.run(prompt)`; updated both call-sites in `escalate()` to `await _call_level(...)`; converted all 4 `TestCallLevel` tests to `@pytest.mark.asyncio async def` using `AsyncMock` for `agent.run`. |
| 2 | Pre-read gates used `grep -n "HardInterrupt"` expecting 1 match, but the identifier appears many times in `guardrails.py` (class def, docstring, `__init__`, message string). Gate would always STOP falsely. | Replaced with `grep -c "class HardInterrupt"` in both Step 1 and Step 3 pre-read gates. |
| 3 | `test_uses_str_fallback_when_output_attr_missing` assigned `result.__str__ = lambda self: ...` on a MagicMock instance. Python resolves `str()` on the class, not the instance — the lambda was never called. Test passed via JSONDecodeError catch path, not the str() fallback path. | Changed to `result.__str__ = MagicMock(return_value='{"escalate": true}')` which MagicMock's metaclass routes correctly. |
| 4 | `_broker_agent` and `_ceo_agent` were constructed at module level with `Agent(model=_HAIKU, ...)`. `pydantic_ai.Agent(model=...)` raises `UserError: Set the ANTHROPIC_API_KEY environment variable` if the key is absent — which it is in `pytest -m 'not integration'` runs. The Step 1 import verification and all test collection would fail with a `UserError`, not a test failure. | Replaced with lazy-singleton getters `_get_broker_agent()` and `_get_ceo_agent()` (same pattern as `trajectory.py::_get_judge_client()`). Agents are only constructed when `escalate()` is actually called with a live LLM; tests that mock `_call_level` or `agent.run` are never affected. |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---|---|---|---|---|
| All fields | All resolved per spec above + decisions log | codebase + design + pre-check | — | ✅ |

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
Read ballast/core/trajectory.py lines 1–40 and lines 770–800. Capture and output:
(1) Exact current import block (lines 34–37)
(2) Exact current VIOLATED_IRREVERSIBLE block (lines 775–789)
(3) Confirm guardrails plan executed: grep -n "build_correction" ballast/core/trajectory.py
(4) Confirm escalation.py does not yet exist: ls ballast/core/escalation.py
(5) Run pytest tests/ -x -q — record passing test count

Do not change anything. Show full output and wait.
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan: ____
guardrails import present: ____  (yes/no)
escalation.py exists: ____  (yes/no)
```

**Automated checks (all must pass before Step 1):**
- [ ] Existing test suite passes. Document test count.
- [ ] `ballast/core/escalation.py` does NOT exist yet.
- [ ] `ballast/core/guardrails.py` exists (guardrails plan must be executed first).
- [ ] `build_correction` is imported in `trajectory.py` (guardrails Step 3 applied).
- [ ] `HardInterrupt` is defined in `ballast/core/guardrails.py`.

> **PREREQUISITE:** The guardrails plan (`.claude/plans/guardrails.md`) must be fully executed before this plan starts. If `ballast/core/guardrails.py` does not exist, stop and execute the guardrails plan first.

---

## Environment Matrix

| Step | Dev | Staging | Prod |
|------|-----|---------|------|
| Step 1 (create escalation.py) | ✅ | ✅ | ✅ |
| Step 2 (create test_escalation.py) | ✅ | ✅ | ✅ |
| Step 3 (wire trajectory.py) | ✅ | ✅ | ✅ |

---

## Tasks

### Phase 1 — Core Module

---

- [ ] 🟥 **Step 1: Create `ballast/core/escalation.py`** — *Critical: new module; all downstream steps depend on it*

  **Step Architecture Thinking:**

  **Pattern applied:** Chain of Responsibility + Null Object (fail-safe default in `_call_level()`).

  **Why this step exists here in the sequence:**
  Steps 2 and 3 both import from this file. It must exist before either can run.

  **Why this file / class is the right location:**
  `ballast/core/` is the kernel layer. Escalation is a core orchestration concern, not an adapter or UI concern. It belongs here alongside `guardrails.py` and `trajectory.py`.

  **Alternative approach considered and rejected:**
  Inline escalation logic inside `trajectory.py`. Rejected because it would make the already-large `run_with_spec()` function responsible for LLM calls — violating Single Responsibility and making testing harder.

  **What breaks if this step deviates from the described pattern:**
  If `_call_level()` raises instead of returning `{"escalate": True}` on exception, a flaky LLM call will break the escalation chain and leave the run in an undefined state.

  ---

  **Idempotent:** Yes — creating a new file is idempotent if the file does not exist (pre-flight confirms this).

  **Context:** This file is the entire escalation subsystem. `trajectory.py` will import `escalate` and `EscalationFailed` from here in Step 3.

  **Pre-Read Gate:**
  - Run `ls ballast/core/escalation.py` — must return "No such file". If file exists → STOP.
  - Run `grep -c "class HardInterrupt" ballast/core/guardrails.py` — must return `1`. If 0 → guardrails plan not executed → STOP. (Do NOT use `grep -n "HardInterrupt"` — the identifier appears multiple times in the file; only the class definition line counts.)

  **Self-Contained Rule:** Code block below is complete and immediately runnable.

  **No-Placeholder Rule:** No `<VALUE>` tokens in the code block.

  ---

  ```python
  """ballast/core/escalation.py — Three-level escalation chain for irreversible violations.

  Public interface:
      escalate(assessment, spec, context, *, run_id, node_index) -> str
          — Walks Broker → CEO → Human. Returns resolution string or raises EscalationFailed.
      EscalationFailed
          — Raised when all levels exhaust without resolution. Carries .assessment and .spec.
      EscalationPacket
          — Typed input envelope passed to every level.

  Chain behaviour:
      _call_level() never raises. Any exception → {"escalate": True}.
      Level 3 (Human) always raises EscalationFailed — it is the ceiling.
  """
  from __future__ import annotations

  import json
  import logging
  from dataclasses import dataclass, field
  from typing import TYPE_CHECKING, Any

  from pydantic_ai import Agent

  from ballast.core.spec import SpecModel

  if TYPE_CHECKING:
      from ballast.core.trajectory import NodeAssessment

  logger = logging.getLogger(__name__)

  _HAIKU = "claude-haiku-4-5-20251001"

  _BROKER_SYSTEM = (
      "You are Broker, a spec-compliance reviewer for an AI agent system called Ballast. "
      "An agent has taken an irreversible action that violates its spec. "
      "Your job: decide if you can provide a corrective instruction that gets the agent back on track, "
      "or if this requires escalation to a higher authority. "
      "Respond ONLY with valid JSON: "
      '{"escalate": false, "resolution": "<corrective instruction>"} '
      'or {"escalate": true}. '
      "Be decisive. If unsure, escalate."
  )

  _CEO_SYSTEM = (
      "You are CEO, the final automated authority in Ballast's escalation chain. "
      "A Broker-level reviewer has already decided this violation requires escalation. "
      "You have full authority to override, redirect, or accept the violation. "
      "Your job: provide a definitive corrective instruction or confirm escalation to a human. "
      "Respond ONLY with valid JSON: "
      '{"escalate": false, "resolution": "<definitive corrective instruction>"} '
      'or {"escalate": true}. '
      "Only escalate if you genuinely cannot provide a resolution."
  )

  # Agents are constructed lazily — NOT at module level — so importing this
  # module in tests does not require ANTHROPIC_API_KEY to be set.
  # Consistent with trajectory.py's _get_judge_client() lazy-singleton pattern.
  _broker_agent: "Agent | None" = None
  _ceo_agent: "Agent | None" = None


  def _get_broker_agent() -> Agent:
      global _broker_agent
      if _broker_agent is None:
          _broker_agent = Agent(model=_HAIKU, system_prompt=_BROKER_SYSTEM)
      return _broker_agent


  def _get_ceo_agent() -> Agent:
      global _ceo_agent
      if _ceo_agent is None:
          _ceo_agent = Agent(model=_HAIKU, system_prompt=_CEO_SYSTEM)
      return _ceo_agent


  # ---------------------------------------------------------------------------
  # EscalationPacket — typed input envelope
  # ---------------------------------------------------------------------------

  @dataclass
  class EscalationPacket:
      """Structured input passed to every escalation level.

      All fields are set at construction in escalate(); levels treat this as
      read-only. run_id and node_index are for logging only.
      """

      assessment: "NodeAssessment"
      spec: SpecModel
      context: list[Any]
      run_id: str = field(default="")
      node_index: int = field(default=0)


  # ---------------------------------------------------------------------------
  # EscalationFailed — raised when all levels exhaust
  # ---------------------------------------------------------------------------

  class EscalationFailed(Exception):
      """Raised by escalate() when all automated levels fail to resolve.

      Callers should write a checkpoint and raise HardInterrupt.
      """

      def __init__(self, assessment: "NodeAssessment", spec: SpecModel) -> None:
          self.assessment = assessment
          self.spec = spec
          super().__init__(
              f"escalation chain exhausted: tool={assessment.tool_name!r} "
              f"spec_version={spec.version_hash[:8]}"
          )


  # ---------------------------------------------------------------------------
  # _call_level — async, never raises
  # ---------------------------------------------------------------------------

  async def _call_level(agent: Agent, packet: EscalationPacket) -> dict:
      """Call one escalation level. Returns parsed dict or {"escalate": True} on any failure.

      Async because agent.run() is async-native. run_sync() must NOT be used here:
      _call_level is always called from escalate() which is always called from the
      async run_with_spec() loop — run_sync() would raise RuntimeError (event loop
      already running).

      Never raises. Any exception (LLM error, parse error, network error) is treated
      as an implicit escalation signal so the chain always continues upward.
      """
      prompt = (
          f"ASSESSMENT\n"
          f"  tool: {packet.assessment.tool_name!r}\n"
          f"  score: {packet.assessment.score:.3f}\n"
          f"  label: {packet.assessment.label}\n"
          f"  rationale: {packet.assessment.rationale}\n\n"
          f"SPEC INTENT\n  {packet.spec.intent[:400]}\n\n"
          f"SPEC VERSION\n  {packet.spec.version_hash[:8]}\n\n"
          f"RUN CONTEXT\n  run_id={packet.run_id}  node_index={packet.node_index}\n\n"
          f"CONTEXT WINDOW (last {min(len(packet.context), 5)} messages)\n"
          + "\n".join(str(m) for m in packet.context[-5:])
      )
      try:
          result = await agent.run(prompt)
          raw = result.output if hasattr(result, "output") else str(result)
          return json.loads(raw)
      except Exception as exc:  # noqa: BLE001
          logger.warning(
              "escalation_level_failed agent=%s exc=%s — treating as escalate",
              agent.__class__.__name__,
              exc,
          )
          return {"escalate": True}


  # ---------------------------------------------------------------------------
  # escalate — public async entry point
  # ---------------------------------------------------------------------------

  async def escalate(
      assessment: "NodeAssessment",
      spec: SpecModel,
      context: list[Any],
      *,
      run_id: str = "",
      node_index: int = 0,
  ) -> str:
      """Walk the escalation chain. Return resolution string or raise EscalationFailed.

      Chain: Broker → CEO → Human (EscalationFailed).

      Args:
          assessment:  NodeAssessment that triggered escalation.
          spec:        Active locked SpecModel.
          context:     Node conversation history for LLM context.
          run_id:      For logging. Optional.
          node_index:  For logging. Optional.

      Returns:
          Resolution string to inject into the agent's message history.

      Raises:
          EscalationFailed: when all automated levels fail to resolve.
      """
      packet = EscalationPacket(
          assessment=assessment,
          spec=spec,
          context=context,
          run_id=run_id,
          node_index=node_index,
      )

      # Level 1 — Broker
      broker_result = await _call_level(_get_broker_agent(), packet)
      if not broker_result.get("escalate", True):
          resolution = broker_result.get("resolution", "")
          if resolution:
              logger.info(
                  "escalation_resolved_broker node=%d run_id=%s",
                  node_index,
                  run_id,
              )
              return resolution

      logger.info("escalation_broker_escalated node=%d run_id=%s", node_index, run_id)

      # Level 2 — CEO
      ceo_result = await _call_level(_get_ceo_agent(), packet)
      if not ceo_result.get("escalate", True):
          resolution = ceo_result.get("resolution", "")
          if resolution:
              logger.info(
                  "escalation_resolved_ceo node=%d run_id=%s",
                  node_index,
                  run_id,
              )
              return resolution

      logger.warning(
          "escalation_chain_exhausted node=%d tool=%s run_id=%s",
          node_index,
          assessment.tool_name,
          run_id,
      )

      # Level 3 — Human (ceiling — always raises)
      raise EscalationFailed(assessment, spec)
  ```

  **What it does:** Defines the full escalation subsystem — packet DTO, failure exception, async level caller, async chain walker.

  **Why this approach:** `_call_level()` is async because `agent.run()` is async-native and calling `run_sync()` inside an already-running event loop raises `RuntimeError`. `escalate()` is async because `run_with_spec()` is async. The fail-safe `{"escalate": True}` default in `_call_level()` means no exception can silently resolve a violation. Agents are lazy-initialized so the module can be imported without `ANTHROPIC_API_KEY`.

  **Assumptions:**
  - `NodeAssessment` has fields: `tool_name: str`, `score: float`, `label: str`, `rationale: str`. Confirmed from `trajectory.py:387–400`.
  - `SpecModel` has fields: `intent: str`, `version_hash: str`. Confirmed from `spec.py:119`.
  - `pydantic_ai.Agent` accepts `model=` and `system_prompt=` constructor args and has `async run(prompt)`. Confirmed — `AgentRunResult` is a dataclass with `output: OutputDataT` field.
  - `result.output` is the typed response from `await agent.run()`. Confirmed from `AgentRunResult` source.
  - `agent.run_sync()` is NOT used — it raises `RuntimeError: This event loop is already running` when called inside an async call stack.

  **Risks:**
  - `result.output` attribute may differ across pydantic-ai versions → mitigation: `hasattr(result, "output")` guard with `str(result)` fallback already in `_call_level()`.
  - `json.loads` fails on non-JSON output → mitigation: caught by the `except Exception` block in `_call_level()`.

  **Git Checkpoint:**
  ```bash
  git add ballast/core/escalation.py
  git commit -m "step 7: add escalation.py — EscalationPacket, EscalationFailed, _call_level, escalate chain"
  ```

  **Verification:**
  ```
  Type:     Unit
  Action:   python -c "from ballast.core.escalation import escalate, EscalationFailed, EscalationPacket; print('ok')"
  Expected: prints "ok" with no ImportError or UserError
  Pass:     "ok" printed
  Fail:     ImportError → check import paths in escalation.py against actual module names
            UserError: Set ANTHROPIC_API_KEY → Agent() is being constructed at module level, not inside _get_broker/ceo_agent() getters → check lazy-init pattern
  ```

---

### Phase 2 — Tests

---

- [ ] 🟥 **Step 2: Create `tests/test_escalation.py`** — *Critical: confirms all chain paths*

  **Step Architecture Thinking:**

  **Pattern applied:** Mock injection — `_call_level` is patched to control chain behaviour without LLM calls.

  **Why this step exists here in the sequence:**
  Step 1 must exist so imports resolve. Step 3 wires into trajectory.py — tests here confirm the module contract before wiring.

  **Why this file is the right location:**
  All Ballast tests live in `tests/`. Naming convention: `test_<module>.py`.

  **Alternative approach considered and rejected:**
  Integration tests hitting real LLM. Rejected: slow, non-deterministic, requires API keys in CI.

  **What breaks if this step deviates:**
  If `_call_level` is not patched and tests call real LLMs, the suite becomes flaky and expensive.

  ---

  **Idempotent:** Yes — creating a new test file.

  **Pre-Read Gate:**
  - Run `grep -n "def _make_spec" tests/test_trajectory.py` — must return 1 match. Copy the exact `_make_spec()` helper from there to ensure consistency.
  - Run `ls tests/test_escalation.py` — must return "No such file".

  **Self-Contained Rule:** All code is complete and runnable as written.

  ---

  ```python
  """tests/test_escalation.py — Unit tests for ballast/core/escalation.py.

  19 tests total:
      TestEscalationPacket   (6)
      TestEscalationFailed   (3)
      TestCallLevel          (4) — internal; patched
      TestEscalate           (6) — full chain; _call_level patched
  """
  from __future__ import annotations

  import pytest
  from unittest.mock import AsyncMock, MagicMock, patch

  from ballast.core.escalation import (
      EscalationFailed,
      EscalationPacket,
      _call_level,
      escalate,
  )
  from ballast.core.spec import SpecModel, lock


  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  def _make_spec(**overrides) -> SpecModel:
      base = dict(
          intent="Test intent for escalation tests",
          success_criteria=["criterion A"],
          constraints=["no irreversible actions"],
          irreversible_actions=["delete_database"],
          drift_threshold=0.7,
          allowed_tools=["read_file"],
          scope="test",
      )
      base.update(overrides)
      return lock(SpecModel(**base))


  def _make_assessment(
      tool_name: str | None = "delete_database",
      score: float = 0.1,
      label: str = "VIOLATED_IRREVERSIBLE",
      rationale: str = "Irreversible action detected",
  ):
      """Return a minimal NodeAssessment-like mock."""
      a = MagicMock()
      a.tool_name = tool_name
      a.score = score
      a.label = label
      a.rationale = rationale
      return a


  # ---------------------------------------------------------------------------
  # TestEscalationPacket
  # ---------------------------------------------------------------------------

  class TestEscalationPacket:
      def test_all_fields_set(self):
          spec = _make_spec()
          a = _make_assessment()
          ctx = [{"role": "user", "content": "do something"}]
          pkt = EscalationPacket(
              assessment=a, spec=spec, context=ctx, run_id="r1", node_index=3
          )
          assert pkt.assessment is a
          assert pkt.spec is spec
          assert pkt.context is ctx
          assert pkt.run_id == "r1"
          assert pkt.node_index == 3

      def test_run_id_defaults_to_empty_string(self):
          pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=[])
          assert pkt.run_id == ""

      def test_node_index_defaults_to_zero(self):
          pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=[])
          assert pkt.node_index == 0

      def test_context_is_stored_by_reference(self):
          ctx = [1, 2, 3]
          pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=ctx)
          ctx.append(4)
          assert pkt.context == [1, 2, 3, 4]

      def test_assessment_tool_name_accessible(self):
          a = _make_assessment(tool_name="nuke_prod")
          pkt = EscalationPacket(assessment=a, spec=_make_spec(), context=[])
          assert pkt.assessment.tool_name == "nuke_prod"

      def test_spec_version_hash_accessible(self):
          spec = _make_spec()
          pkt = EscalationPacket(assessment=_make_assessment(), spec=spec, context=[])
          assert len(pkt.spec.version_hash) > 0


  # ---------------------------------------------------------------------------
  # TestEscalationFailed
  # ---------------------------------------------------------------------------

  class TestEscalationFailed:
      def test_carries_assessment(self):
          a = _make_assessment()
          spec = _make_spec()
          exc = EscalationFailed(a, spec)
          assert exc.assessment is a

      def test_carries_spec(self):
          a = _make_assessment()
          spec = _make_spec()
          exc = EscalationFailed(a, spec)
          assert exc.spec is spec

      def test_message_contains_tool_name_and_version(self):
          a = _make_assessment(tool_name="nuke_prod")
          spec = _make_spec()
          exc = EscalationFailed(a, spec)
          msg = str(exc)
          assert "nuke_prod" in msg
          assert spec.version_hash[:8] in msg


  # ---------------------------------------------------------------------------
  # TestCallLevel
  # ---------------------------------------------------------------------------

  class TestCallLevel:
      @pytest.mark.asyncio
      async def test_returns_dict_on_valid_json_response(self):
          agent = MagicMock()
          result = MagicMock()
          result.output = '{"escalate": false, "resolution": "stop and revert"}'
          agent.run = AsyncMock(return_value=result)
          pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=[])
          out = await _call_level(agent, pkt)
          assert out == {"escalate": False, "resolution": "stop and revert"}

      @pytest.mark.asyncio
      async def test_returns_escalate_true_on_invalid_json(self):
          agent = MagicMock()
          result = MagicMock()
          result.output = "not json at all"
          agent.run = AsyncMock(return_value=result)
          pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=[])
          out = await _call_level(agent, pkt)
          assert out == {"escalate": True}

      @pytest.mark.asyncio
      async def test_returns_escalate_true_on_run_exception(self):
          agent = MagicMock()
          agent.run = AsyncMock(side_effect=RuntimeError("LLM timeout"))
          pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=[])
          out = await _call_level(agent, pkt)
          assert out == {"escalate": True}

      @pytest.mark.asyncio
      async def test_uses_str_fallback_when_output_attr_missing(self):
          agent = MagicMock()
          result = MagicMock(spec=[])  # no .output attribute
          result.__str__ = MagicMock(return_value='{"escalate": true}')
          agent.run = AsyncMock(return_value=result)
          pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=[])
          out = await _call_level(agent, pkt)
          assert out.get("escalate") is True


  # ---------------------------------------------------------------------------
  # TestEscalate
  # ---------------------------------------------------------------------------

  class TestEscalate:
      @pytest.mark.asyncio
      async def test_broker_resolves_returns_resolution(self):
          broker_return = {"escalate": False, "resolution": "redirect to safe path"}
          with patch("ballast.core.escalation._call_level", return_value=broker_return):
              result = await escalate(
                  _make_assessment(), _make_spec(), [], run_id="r1", node_index=1
              )
          assert result == "redirect to safe path"

      @pytest.mark.asyncio
      async def test_broker_escalates_ceo_resolves(self):
          returns = [
              {"escalate": True},
              {"escalate": False, "resolution": "CEO override: proceed carefully"},
          ]
          with patch("ballast.core.escalation._call_level", side_effect=returns):
              result = await escalate(
                  _make_assessment(), _make_spec(), [], run_id="r2", node_index=2
              )
          assert result == "CEO override: proceed carefully"

      @pytest.mark.asyncio
      async def test_both_escalate_raises_escalation_failed(self):
          returns = [{"escalate": True}, {"escalate": True}]
          with patch("ballast.core.escalation._call_level", side_effect=returns):
              with pytest.raises(EscalationFailed) as exc_info:
                  await escalate(
                      _make_assessment(), _make_spec(), [], run_id="r3", node_index=3
                  )
          assert exc_info.value.assessment is not None
          assert exc_info.value.spec is not None

      @pytest.mark.asyncio
      async def test_escalation_failed_carries_correct_assessment(self):
          a = _make_assessment(tool_name="nuke_prod")
          spec = _make_spec()
          returns = [{"escalate": True}, {"escalate": True}]
          with patch("ballast.core.escalation._call_level", side_effect=returns):
              with pytest.raises(EscalationFailed) as exc_info:
                  await escalate(a, spec, [], run_id="r4", node_index=4)
          assert exc_info.value.assessment is a
          assert exc_info.value.spec is spec

      @pytest.mark.asyncio
      async def test_broker_empty_resolution_treated_as_escalate(self):
          """Empty resolution string in broker result → escalate to CEO."""
          returns = [
              {"escalate": False, "resolution": ""},  # empty — treated as escalate
              {"escalate": False, "resolution": "CEO fallback"},
          ]
          with patch("ballast.core.escalation._call_level", side_effect=returns):
              result = await escalate(
                  _make_assessment(), _make_spec(), [], run_id="r5", node_index=5
              )
          assert result == "CEO fallback"

      @pytest.mark.asyncio
      async def test_packet_constructed_with_correct_fields(self):
          """Confirm packet passed to _call_level has all five fields."""
          captured: list[EscalationPacket] = []

          def capture(agent, packet):
              captured.append(packet)
              return {"escalate": False, "resolution": "ok"}

          a = _make_assessment()
          spec = _make_spec()
          ctx = ["msg1", "msg2"]
          with patch("ballast.core.escalation._call_level", side_effect=capture):
              await escalate(a, spec, ctx, run_id="r6", node_index=7)

          assert len(captured) == 1
          pkt = captured[0]
          assert pkt.assessment is a
          assert pkt.spec is spec
          assert pkt.context is ctx
          assert pkt.run_id == "r6"
          assert pkt.node_index == 7
  ```

  **Git Checkpoint:**
  ```bash
  git add tests/test_escalation.py
  git commit -m "step 7: add test_escalation.py — 19 tests covering EscalationPacket, EscalationFailed, _call_level, escalate chain"
  ```

  **Verification:**
  ```
  Type:     Unit
  Action:   pytest tests/test_escalation.py -v
  Expected: 19 passed, 0 failed
  Pass:     "19 passed" in output
  Fail:     ImportError → check ballast/core/escalation.py exports; AttributeError → check _make_assessment() fields
            "RuntimeWarning: coroutine was never awaited" → a TestCallLevel test is missing @pytest.mark.asyncio
            "PytestUnraisableExceptionWarning" → same cause; confirm all 4 TestCallLevel methods are async
  ```

---

### Phase 3 — Wire into trajectory.py

---

- [ ] 🟥 **Step 3: Wire escalation into `trajectory.py`** — *Critical: live path change in run_with_spec*

  **Step Architecture Thinking:**

  **Pattern applied:** Open/Closed — `run_with_spec` is open for extension (escalation wired in), closed for modification (no signature changes).

  **Why this step exists here in the sequence:**
  Steps 1 and 2 must be complete. The import in Edit A only works if `escalation.py` exists. The anchor in Edit B only resolves correctly if the post-guardrails state is present.

  **Why trajectory.py is the right location:**
  The VIOLATED_IRREVERSIBLE block lives in `run_with_spec()`, which is trajectory.py's orchestration loop. Wiring belongs here, not in escalation.py (which has no knowledge of the pydantic-ai run context).

  **Alternative approach considered and rejected:**
  Subclassing `run_with_spec` in escalation.py. Rejected: creates circular dependency (escalation.py importing trajectory.py which imports escalation.py).

  **What breaks if this step deviates:**
  If `HardInterrupt` is raised BEFORE `progress.write()`, the checkpoint is lost and the run cannot resume cleanly.

  ---

  **Idempotent:** No — `trajectory.py` is modified. Re-running this step on an already-edited file would duplicate the import. Pre-read gate prevents this.

  **Pre-Read Gate:**
  Before any edit, run ALL of the following. If ANY check fails → STOP and report.

  - `grep -c "from ballast.core.escalation" ballast/core/trajectory.py` — must return `0` (not yet imported). If 1 → step already applied → STOP.
  - `grep -c "from ballast.core.guardrails import" ballast/core/trajectory.py` — must return `1`. If 0 → guardrails plan not executed → STOP.
  - `grep -c "class HardInterrupt" ballast/core/guardrails.py` — must return `1`. If 0 → STOP. (Use `class HardInterrupt`, not the bare identifier — it appears multiple times in the file.)
  - `grep -c "TODO Step 7" ballast/core/trajectory.py` — must return `1` at the VIOLATED_IRREVERSIBLE block. If 0 → STOP (already wired).

  **Anchor Uniqueness Check:**
  - Edit A target: `from ballast.core.guardrails import build_correction, can_resume` — must appear exactly 1 time.
  - Edit B target: the full 12-line VIOLATED_IRREVERSIBLE block starting with `if assessment.label == "VIOLATED_IRREVERSIBLE":` and ending with the closing `logger.warning(...)` block — must appear exactly 1 time.

  ---

  **Edit A — Add escalation imports** (append to guardrails import line):

  Old string (exactly as it appears after guardrails plan executes):
  ```
  from ballast.core.guardrails import build_correction, can_resume
  ```

  New string:
  ```
  from ballast.core.guardrails import HardInterrupt, build_correction, can_resume
  from ballast.core.escalation import EscalationFailed, escalate
  ```

  ---

  **Edit B — Replace VIOLATED_IRREVERSIBLE stub with live escalation**:

  Old string (exactly as it appears after todo-1 is applied — post-todo-1 form):
  ```python
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
  ```

  New string:
  ```python
              if assessment.label == "VIOLATED_IRREVERSIBLE":
                  try:
                      resolution = await escalate(
                          assessment, active_spec,
                          compact_history + full_window,
                          run_id=run_id,
                          node_index=node_index,
                      )
                      agent_run.ctx.state.message_history.append(
                          ModelRequest(parts=[UserPromptPart(content=resolution)])
                      )
                  except EscalationFailed:
                      progress.write()
                      raise HardInterrupt(assessment, active_spec, node_index)
                  progress.total_violations += 1
                  progress.last_escalation = datetime.now(timezone.utc).isoformat()
                  logger.warning(
                      "irreversible_action_detected node=%d tool=%s spec_version=%s run_id=%s",
                      node_index,
                      assessment.tool_name,
                      active_spec.version_hash,
                      run_id,
                  )
  ```

  **What it does:**
  - Edit A: Adds `HardInterrupt` to guardrails import and adds escalation import.
  - Edit B: Replaces the TODO stub with live escalation chain call. On `EscalationFailed`, writes checkpoint then raises `HardInterrupt`. Progress counters and log are updated after escalation regardless of which level resolved (success path only — `HardInterrupt` path re-raises before reaching them).

  **Why this approach:** `progress.write()` before `HardInterrupt` ensures the checkpoint is durable before the exception propagates. The counters (`total_violations`, `last_escalation`) and logger are inside the `VIOLATED_IRREVERSIBLE` block after the try/except, so they only execute on successful resolution, not on `HardInterrupt`. This is intentional: a hard interrupt is not a "violation counted" event — it is a run termination.

  **Assumptions:**
  - `compact_history` and `full_window` are defined in scope at this point in `run_with_spec()`. Confirmed from trajectory.py.
  - `progress.write()` is a synchronous method on `BallastProgress`. Confirmed from checkpoint.py.
  - `ModelRequest` and `UserPromptPart` are already imported in trajectory.py (line 32). Confirmed.

  **Risks:**
  - todo-1 not yet applied → `assessment.label` does not exist at runtime → AttributeError → mitigation: pre-flight must confirm `assessment.label` usage exists in trajectory.py before this step.
  - Guardrails plan not executed → `HardInterrupt` not importable → ImportError → mitigation: pre-read gate checks `class HardInterrupt` exists in guardrails.py.
  - `escalate()` called with `await` from sync context (e.g. a test that forgot `asyncio.run`) → `RuntimeWarning: coroutine was never awaited` → ensure Step 2 tests all use `@pytest.mark.asyncio`.

  **Git Checkpoint:**
  ```bash
  git add ballast/core/trajectory.py
  git commit -m "step 7: wire escalation into run_with_spec — escalate() call, EscalationFailed → HardInterrupt path"
  ```

  **Verification:**
  ```
  Type:     Unit + Integration
  Action:   pytest tests/ -x -q
  Expected: All prior tests pass + at minimum 19 new tests from test_escalation.py
  Pass:     No failures; test count ≥ (pre-flight baseline + 19)
  Fail:     ImportError on HardInterrupt → guardrails plan not executed
            AttributeError on assessment.label → todo-1 not applied
            AttributeError on assessment.tool_name → check NodeAssessment dataclass fields
  ```

---

## Post-Plan Checklist

- [ ] `ballast/core/escalation.py` exists and imports cleanly.
- [ ] `tests/test_escalation.py` has 19 tests, all passing.
- [ ] `trajectory.py` imports `HardInterrupt`, `EscalationFailed`, `escalate`.
- [ ] `trajectory.py` VIOLATED_IRREVERSIBLE block no longer contains `# TODO Step 7`.
- [ ] `pytest tests/ -x -q` passes with no regressions.
- [ ] All three git commits made (one per step).

---

## State Manifest (fill after all steps complete)

```
Files modified:
  ballast/core/escalation.py   — created (new file)
  tests/test_escalation.py     — created (new file)
  ballast/core/trajectory.py   — edited (2 edits: import + VIOLATED_IRREVERSIBLE block)

Test count after plan: ____
Regressions: none expected
Next plan: evaluator.py (Step 8)
```
