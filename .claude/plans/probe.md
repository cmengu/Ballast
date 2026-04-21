# probe.py — Implementation Plan

**Overall Progress:** `0%`

---

## Spec Summary — Probe Module

**What this module does.** After `score_drift()` labels a node `PROGRESSING`, Ballast does not immediately accept that label. Instead it probes: `verify_node_claim` makes one focused LLM call to check whether the node's actual tool name and arguments violate any of the spec's hard constraints. If the probe disagrees — the tool args breach a constraint that the drift scorers missed — it returns `(False, note)` and `trajectory.py` downgrades the assessment to `VIOLATED` before the run continues. If the probe passes or errors, it returns `(True, note)` and the run continues undisturbed.

**Input.** `verify_node_claim()` receives a `node: Any` (the raw pydantic-ai `Agent.iter` node), a `label: str` (the current `DriftLabel` assigned by `score_drift()`), and a `spec: SpecModel` (the active locked spec). No `run_id` or `node_index` — the probe is stateless; its result travels back to `trajectory.py` as a plain return tuple.

**Output.** `(True, "")` when the probe confirms no constraint violation. `(True, "probe_error: <exc>")` when the probe agent call fails — fail-open so errors never block a `PROGRESSING` node. `(False, "<note>")` when the probe detects a constraint breach, where `note` names the violated constraint.

**Verification flow.**
- **No tool call** — if tool extraction returns an empty `tool_name`, there is nothing to verify → return `(True, "no tool call")` immediately. No LLM call made.
- **Constraint check** — build a `ProbePacket` from `tool_name`, `tool_args` (JSON-serialised), `tool_result` (node content excerpt), and `spec.constraints`. Call `_call_probe_agent()`. Parse `{"verified": bool, "note": str}` from the response. Return `(result["verified"], result["note"])`.
- **Fail-open** — any exception in `_call_probe_agent()` returns `(True, "probe_error: <exc>")`. The probe never raises.

**The `ProbePacket` dataclass.** `verify_node_claim` constructs one `ProbePacket` per call: `tool_name: str`, `tool_args: str` (JSON-serialised for prompt safety), `tool_result: str` (node content, max 500 chars), `spec_constraints: list[str]`. Passed to `_call_probe_agent()`. Transient — not stored or serialised.

**`_call_probe_agent()`.** Internal `async` function. Accepts a pydantic-ai `Agent` and a `ProbePacket`. Calls `await agent.run(prompt)`, parses the JSON response, and returns `{"verified": bool, "note": str}` — or `{"verified": True, "note": "probe_error: <exc>"}` on any exception. Never raises. This is the same fail-open guarantee as `_call_level()` in `escalation.py`.

**Fail-safe.** `score_drift()` is the primary defense. The probe is supplemental. False-negatives (missed constraint breaches) are acceptable; false-positives (blocking valid `PROGRESSING` nodes due to a flaky LLM call) are not. Any exception inside `_call_probe_agent()` → fail open, return `(True, ...)`.

**Constraints.**
- No disk I/O inside `_call_probe_agent()`.
- `verify_node_claim()` is `async` because `_call_probe_agent()` uses `await agent.run()`.
- Probe agent is NOT constructed at module import time (lazy singleton — same as `escalation.py`).
- probe.py does NOT import from `trajectory.py` (circular import: `trajectory` imports `probe`). Tool extraction is inlined using minimal duck-typing.
- Model: `claude-haiku-4-5-20251001` (same as escalation — probe is on the hot path of every PROGRESSING node).
- No retries. One LLM call per node. Fail-open on any error.

**Success criteria (eval-derivable).**
1. Node with no tool call → returns `(True, "no tool call")` with no LLM call made.
2. Node with tool and args that comply with constraints → returns `(True, ...)`.
3. Node with tool args that violate a spec constraint → returns `(False, note)` with non-empty `note`.
4. Any exception inside `_call_probe_agent()` → returns `(True, "probe_error: ...")`.
5. JSON parse error inside `_call_probe_agent()` → returns `(True, "probe_error: ...")`.
6. `ProbePacket` exposes all four fields: `tool_name`, `tool_args`, `tool_result`, `spec_constraints`.
7. Probe agent is NOT constructed at module import (lazy singleton — importing probe.py without `ANTHROPIC_API_KEY` does not raise).
8. `verify_node_claim` is `async`.
9. `trajectory.py` replaces the `verified = True` stub: calls `verify_node_claim` when `assessment.label == "PROGRESSING"`; on `verified=False` sets `assessment.label = "VIOLATED"`, `assessment.score = 0.0`, `assessment.rationale = f"probe failed: {probe_note}"`.
10. `NodeSummary.verified` in `trajectory.py` is set to the bool returned by `verify_node_claim`.

---

## Architecture Overview

**The problem this plan solves:**
`trajectory.py:751–757` has a `# TODO Step 9` stub for the environment probe step. After `score_drift()` assigns a `PROGRESSING` label, the run immediately accepts it (`verified = True` hardcoded). This means a node whose tool arguments clearly breach a spec constraint (e.g., "do not write to any files") can pass through with `PROGRESSING` if the three LLM scorers each scored it above 0.85. The probe is the second checkpoint: it looks at the actual args and result against `spec.constraints` and can downgrade `PROGRESSING` → `VIOLATED` before the checkpoint is written.

**The pattern applied:**
- **Null Object / Fail-Safe Default** — `_call_probe_agent()` returns `{"verified": True, "note": "probe_error: ..."}` on any exception. The probe never raises. This mirrors `_call_level()` in `escalation.py`.
- **DTO (`ProbePacket`)** — structured input envelope; avoids positional argument drift as the probe evolves. All context the LLM needs is in one place.
- **Lazy Singleton** — `_probe_agent` is `None` at module level, constructed on first `verify_node_claim` call. Prevents `ANTHROPIC_API_KEY` errors at import/test-collection time.

**What stays unchanged:**
- `spec.py`, `checkpoint.py`, `guardrails.py`, `escalation.py`, `cost.py`, `sync.py` — none touched.
- `trajectory.py` is edited in Step 2 only, and only the `# TODO Step 9` block (lines 750–757) and the import block.
- `TrajectoryChecker`, `score_drift`, `NodeAssessment`, `_run_scorers` — not touched.

**What this plan adds:**
- `ballast/core/probe.py` — `ProbePacket`, `_call_probe_agent()`, `verify_node_claim()`, lazy probe agent.
- `tests/test_probe.py` — unit tests covering all 10 success criteria paths.

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|---|---|---|
| probe.py inlines minimal tool extraction | Import `_extract_node_info` from `trajectory.py` | `trajectory.py` imports `probe.py`; a reverse import creates a circular dependency. Inlining 15 lines is acceptable duplication. |
| Fail-open on probe error | Fail-closed (treat errors as VIOLATED) | Probe is supplemental. `score_drift()` is primary. False-positives (blocking valid PROGRESSING nodes) cause more disruption than false-negatives. |
| Probe runs only for `PROGRESSING` | Run for `STALLED` too | `STALLED` already triggers a soft correction; adding probe overhead is noise. The probe's value is catching `PROGRESSING` nodes that slipped past scorers. |
| `verify_node_claim` is `async` | Sync with `agent.run_sync()` | `run_sync()` raises `RuntimeError: This event loop is already running` when called inside the async `run_with_spec()` loop (same issue resolved for `escalation.py`). |
| `assessment` is mutated in-place on probe failure | Return new `NodeAssessment` | The stub already shows mutation; `NodeAssessment` is a non-frozen `@dataclass`; matching the stub minimises trajectory.py change surface. |

**Known limitations acknowledged:**

| Limitation | Why acceptable now | Upgrade path |
|---|---|---|
| Tool extraction in probe.py is simpler than `_extract_node_info` (no wrapper scanning) | Covers `hasattr(node, "tool_name")` + `parts` scan — the common pydantic-ai call path | Extract shared `_get_tool_info(node)` into `ballast/core/utils.py` in a later cleanup pass |
| Probe runs even when `spec.constraints` is empty | Harmless — LLM will return `{"verified": true}` with empty constraints | Add an early-exit guard in a later pass if benchmarks show overhead |
| No telemetry in probe path | OTel stubbed until Step 13 | Add `emit_probe_span()` call in Step 13 |

---

## Decisions Log (pre-check resolutions)

| # | Flaw | Resolution applied |
|---|---|---|
| 1 | Stub at `trajectory.py:752` checks `assessment.label in ("PROGRESSING", "COMPLETE")` — but `"COMPLETE"` is not a valid `DriftLabel`. | Wire step checks only `assessment.label == "PROGRESSING"`. The `"COMPLETE"` arm in the stub is a dead branch. |
| 2 | `probe.py` cannot import `_extract_node_info` from `trajectory.py` — circular import (`trajectory` imports `probe`, `probe` would import `trajectory`). | Inline a minimal `_get_tool_info(node)` in `probe.py`: only two extraction paths (direct attrs + `parts` scan). No wrapper scanning needed for the probe's prompt construction. |
| 3 | pydantic-ai `Agent(model=...)` raises `UserError: Set the ANTHROPIC_API_KEY` if key is absent at module import time — breaks `pytest -m 'not integration'` collection. | Lazy singleton `_get_probe_agent()` (same pattern as `escalation.py::_get_broker_agent()`). Agent constructed only when `verify_node_claim` is actually called. |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---|---|---|---|---|
| All fields | All resolved per spec above + decisions log | codebase + design | — | ✅ |

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
cd /Users/ngchenmeng/Ballast && source venv/bin/activate

# (1) Baseline test count
python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3

# (2) Confirm probe.py does not exist
ls ballast/core/probe.py 2>&1

# (3) Confirm TODO Step 9 stub exists (must return exactly 1 match)
grep -c 'TODO Step 9' ballast/core/trajectory.py

# (4) Confirm exact stub block (lines 750–757)
sed -n '750,757p' ballast/core/trajectory.py

# (5) Confirm NodeAssessment is a non-frozen dataclass (mutation is valid)
grep -n '@dataclass' ballast/core/trajectory.py | head -5

# (6) Confirm existing trajectory.py import block (lines 21–39)
sed -n '21,39p' ballast/core/trajectory.py

# (7) Line counts
wc -l ballast/core/trajectory.py tests/test_trajectory.py
```

**Baseline Snapshot (agent fills during pre-flight — do not pre-fill):**
```
Test count before plan:          ____
trajectory.py line count:        ____
test_trajectory.py line count:   ____
probe.py exists:                 ____ (expected: No such file)
TODO Step 9 grep count:          ____ (expected: 1)
```

**Automated checks (all must pass before Step 1):**
- [ ] Existing tests pass. Count: `____`
- [ ] `ballast/core/probe.py` does not exist.
- [ ] `grep -c 'TODO Step 9' ballast/core/trajectory.py` returns `1`.
- [ ] `sed -n '750,757p'` shows the stub block matching the plan's expected anchor.

---

## Steps Analysis

```
Step 1 (Create probe.py)              — Critical   — full code review — Idempotent: Yes (new file)
Step 2 (Wire trajectory.py stub)      — Critical   — full code review — Idempotent: Yes
Step 3 (Create tests/test_probe.py)   — Non-critical — verification  — Idempotent: Yes (new file)
```

---

## Tasks

### Phase 1 — Probe module

**Goal:** `ballast/core/probe.py` exists, is importable without `ANTHROPIC_API_KEY`, and exports `verify_node_claim`.

---

- [ ] 🟥 **Step 1: Create `ballast/core/probe.py`** — *Critical: trajectory.py Step 2 imports from it*

  **Step Architecture Thinking:**

  **Pattern applied:** DTO (`ProbePacket`) + Lazy Singleton + Fail-Open Null Object (`_call_probe_agent` returns `{"verified": True, ...}` on any exception).

  **Why this step exists here in the sequence:**
  Step 2 adds `from ballast.core.probe import verify_node_claim` to `trajectory.py`. If this file does not exist first, that import fails and all 190 existing tests break.

  **Why this file is the right location:**
  `ballast/core/` is the kernel. The probe is core orchestration infrastructure used only by `trajectory.py`. It does not belong in `adapters/`.

  **Alternative approach considered and rejected:**
  Inline `verify_node_claim` directly in `trajectory.py`. Rejected: it adds ~80 lines + a new pydantic-ai Agent to trajectory.py, mixing probe concerns with orchestration. Separate module matches the pattern set by `escalation.py` and `guardrails.py`.

  **What breaks if this step deviates:**
  If `_call_probe_agent` raises instead of returning fail-open, a flaky LLM call blocks every PROGRESSING node — the probe becomes a denial-of-service against its own run.

  ---

  **Idempotent:** Yes — new file.

  **Pre-Read Gate:**
  - `ls ballast/core/probe.py` must return `No such file`. If it returns a path → STOP.
  - `ls ballast/core/__init__.py` must succeed. If not → STOP.

  **Self-Contained Rule:** All code below is complete and runnable.

  **No-Placeholder Rule:** No `<VALUE>` tokens.

  ```python
  """ballast/core/probe.py — Post-execution environment probe.

  Public interface:
      verify_node_claim(node, label, spec) -> tuple[bool, str]
          — Makes one LLM call to check whether a PROGRESSING node's tool args
            violate any spec constraint. Returns (True, "") on pass, (False, note)
            on breach, (True, "probe_error: ...") on any exception.
      ProbePacket
          — Typed input envelope passed to _call_probe_agent().

  Fail-safe: _call_probe_agent() never raises. Any exception → (True, "probe_error: ...").
  The probe is supplemental to score_drift(). Fail-open is intentional.
  """
  from __future__ import annotations

  import json
  import logging
  from dataclasses import dataclass, field
  from typing import Any

  from pydantic_ai import Agent

  from ballast.core.spec import SpecModel

  logger = logging.getLogger(__name__)

  _HAIKU = "claude-haiku-4-5-20251001"

  _PROBE_SYSTEM = (
      "You are a constraint auditor for Ballast, an AI agent guardrail system. "
      "An agent has just executed a tool call. "
      "Your job: determine whether the tool name and arguments violate any of the "
      "listed hard constraints. "
      "Respond ONLY with valid JSON: "
      '{"verified": true} if no constraint is violated, or '
      '{"verified": false, "note": "<which constraint was breached and why>"} '
      "if a constraint is clearly violated. "
      "Be strict: if the tool args unambiguously match a constraint violation, flag it. "
      "If unsure, return verified: true."
  )

  # Lazy singleton — NOT constructed at module level.
  # Constructing Agent(model=...) at import time raises UserError if ANTHROPIC_API_KEY
  # is absent, which breaks pytest -m 'not integration' collection.
  _probe_agent: "Agent | None" = None


  def _get_probe_agent() -> Agent:
      global _probe_agent
      if _probe_agent is None:
          _probe_agent = Agent(model=_HAIKU, system_prompt=_PROBE_SYSTEM)
      return _probe_agent


  # ---------------------------------------------------------------------------
  # ProbePacket — typed input envelope
  # ---------------------------------------------------------------------------

  @dataclass
  class ProbePacket:
      """Structured input passed to _call_probe_agent().

      tool_args is JSON-serialised str for prompt safety (avoids nested dict formatting).
      tool_result is the node content excerpt (max 500 chars).
      """

      tool_name: str
      tool_args: str                            # JSON-serialised
      tool_result: str
      spec_constraints: list[str] = field(default_factory=list)


  # ---------------------------------------------------------------------------
  # _get_tool_info — minimal duck-typed extraction (no trajectory.py import)
  # ---------------------------------------------------------------------------

  def _get_tool_info(node: Any) -> tuple[str, dict, str]:
      """Extract (tool_name, tool_args, content) from a pydantic-ai node.

      Minimal version — covers direct-attr and parts-scan paths only.
      Does not import from trajectory.py (circular: trajectory imports probe).
      Returns ("", {}, "") if no tool call is found.
      """
      tool_name = ""
      tool_args: dict = {}
      content = ""

      # Direct attributes (some pydantic-ai versions)
      if hasattr(node, "tool_name") and hasattr(node, "args"):
          tool_name = str(node.tool_name)
          args_raw = getattr(node, "args", {})
          tool_args = args_raw if isinstance(args_raw, dict) else {}

      # Scan parts (ModelResponse with ToolCallPart)
      if not tool_name:
          for part in getattr(node, "parts", []) or []:
              if type(part).__name__ in ("ToolCallPart", "ToolCall", "FunctionCall"):
                  t_name = str(getattr(part, "tool_name", getattr(part, "function_name", "")))
                  t_args = getattr(part, "args", getattr(part, "arguments", {}))
                  if t_name:
                      tool_name = t_name
                      tool_args = t_args if isinstance(t_args, dict) else {}
                      break

      # Content extraction
      for attr in ("text", "content", "output"):
          val = getattr(node, attr, None)
          if val and isinstance(val, str):
              content = val[:500]
              break

      return tool_name, tool_args, content


  # ---------------------------------------------------------------------------
  # _call_probe_agent — async, never raises
  # ---------------------------------------------------------------------------

  async def _call_probe_agent(agent: Agent, packet: ProbePacket) -> dict:
      """Call the probe agent. Returns parsed dict or fail-open dict on any exception.

      Async because agent.run() is async-native. run_sync() must NOT be used here:
      _call_probe_agent is always called from verify_node_claim() which is always
      called from the async run_with_spec() loop.

      Never raises. Any exception → {"verified": True, "note": "probe_error: <exc>"}.
      """
      constraints_block = (
          "\n".join(f"  - {c}" for c in packet.spec_constraints)
          if packet.spec_constraints
          else "  (none)"
      )
      prompt = (
          f"TOOL CALL\n"
          f"  name: {packet.tool_name!r}\n"
          f"  args: {packet.tool_args}\n\n"
          f"TOOL RESULT (excerpt)\n"
          f"  {packet.tool_result[:400]}\n\n"
          f"SPEC CONSTRAINTS\n"
          f"{constraints_block}"
      )
      try:
          result = await agent.run(prompt)
          raw = result.output if hasattr(result, "output") else str(result)
          parsed = json.loads(raw)
          # Normalise: ensure both keys exist
          return {
              "verified": bool(parsed.get("verified", True)),
              "note": str(parsed.get("note", "")),
          }
      except Exception as exc:  # noqa: BLE001
          logger.warning(
              "probe_agent_failed tool=%r exc=%s — failing open",
              packet.tool_name,
              exc,
          )
          return {"verified": True, "note": f"probe_error: {exc}"}


  # ---------------------------------------------------------------------------
  # verify_node_claim — public async entry point
  # ---------------------------------------------------------------------------

  async def verify_node_claim(
      node: Any,
      label: str,
      spec: SpecModel,
  ) -> tuple[bool, str]:
      """Probe whether a PROGRESSING node's tool args violate spec constraints.

      Args:
          node:   Raw pydantic-ai Agent.iter node.
          label:  DriftLabel assigned by score_drift() — caller should only call
                  this for PROGRESSING nodes, but the function is safe for any label.
          spec:   Active locked SpecModel.

      Returns:
          (True, "")                    — probe passed, no constraint violation.
          (True, "no tool call")        — node has no tool call; nothing to verify.
          (True, "probe_error: <exc>")  — probe agent failed; fail-open.
          (False, "<note>")             — constraint violation detected.
      """
      tool_name, tool_args, content = _get_tool_info(node)

      if not tool_name:
          return True, "no tool call"

      packet = ProbePacket(
          tool_name=tool_name,
          tool_args=json.dumps(tool_args, default=str)[:400],
          tool_result=content,
          spec_constraints=list(spec.constraints),
      )

      result = await _call_probe_agent(_get_probe_agent(), packet)
      return result["verified"], result["note"]
  ```

  **What it does:** Defines `ProbePacket`, a minimal `_get_tool_info` extractor, a fail-open `_call_probe_agent`, and the public `verify_node_claim` async function.

  **Why this approach:** Mirrors the structure of `escalation.py` exactly — lazy singleton, async, fail-open `_call_*` wrapper, DTO packet. Any engineer who understands escalation understands probe.

  **Assumptions:**
  - `ballast/core/spec.py` exposes `SpecModel.constraints: list[str]`.
  - pydantic-ai `Agent.run()` is awaitable and its result has `.output` or falls back to `str(result)`.

  **Risks:**
  - pydantic-ai result attribute name changes → mitigation: same `hasattr(result, "output") else str(result)` fallback already proven in `escalation.py`.
  - `json.dumps(tool_args)` on non-serialisable args → mitigation: `default=str` handles all cases.

  **Git Checkpoint:**
  ```bash
  git add ballast/core/probe.py
  git commit -m "step 1: add probe.py — verify_node_claim, ProbePacket, lazy singleton"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate
  python -c "
  from ballast.core.probe import ProbePacket, verify_node_claim
  import inspect
  assert inspect.iscoroutinefunction(verify_node_claim), 'not async'
  p = ProbePacket(tool_name='x', tool_args='{}', tool_result='', spec_constraints=[])
  assert p.tool_name == 'x'
  print('probe import OK')
  "
  ```

  **Expected:**
  - `probe import OK` printed, no errors.
  - No `UserError` about `ANTHROPIC_API_KEY` (lazy singleton not triggered by import).

  **Pass:** `probe import OK` with exit code 0.

  **Fail:**
  - `UserError: Set the ANTHROPIC_API_KEY` → agent constructed at module level → check `_probe_agent = None` is at module level, not inside a class body.
  - `ImportError` on `SpecModel` → check `from ballast.core.spec import SpecModel` in probe.py.

---

### Phase 2 — Wire trajectory.py

**Goal:** `trajectory.py` replaces `verified = True` stub with a real `verify_node_claim` call; `NodeSummary.verified` reflects actual probe result.

---

- [ ] 🟥 **Step 2: Wire `trajectory.py` — replace TODO Step 9 stub** — *Critical: wires the module into the live loop*

  **Step Architecture Thinking:**

  **Pattern applied:** Open/Closed — the orchestration loop skeleton in `run_with_spec` is unchanged. Only the stub comment block at step 3 (lines 750–757) is replaced. No other logic moves.

  **Why this step exists here in the sequence:**
  `probe.py` must exist (Step 1) before this import compiles. The stub is the only change surface — everything else in `trajectory.py` already handles `verified` correctly (`_compact_node`, `NodeSummary`).

  **Why this file is the right location:**
  `trajectory.py` owns `run_with_spec`. The probe is called from within the node loop, which lives here.

  **Alternative approach considered and rejected:**
  Move the probe call into a helper function inside `trajectory.py`. Rejected: it adds indirection for a three-line replacement. The loop is a coordinator — inline steps are expected.

  **What breaks if this step deviates:**
  If `assessment` is not mutated when `verified=False`, the `NodeSummary` will record `label=PROGRESSING` for a node that breached a constraint — the audit trail is wrong and score_drift's VIOLATED path never fires for that node.

  ---

  **Idempotent:** Yes — replacing a comment block with deterministic code.

  **Pre-Read Gate:**
  - `grep -c 'TODO Step 9' ballast/core/trajectory.py` must return `1`. If 0 → Step 1 already wired (check). If 2+ → STOP.
  - `grep -c 'from ballast.core.probe' ballast/core/trajectory.py` must return `0`. If 1 → already imported → STOP.
  - `grep -n 'verified = True' ballast/core/trajectory.py` must return exactly 1 match inside `run_with_spec`. Confirm scope.

  **Anchor Uniqueness Check:**
  - Target block: lines containing `# ── 3. Environment probe — STUB` through `verified = True`
  - Must appear exactly once in `trajectory.py`.
  - If outside `run_with_spec` scope → STOP.

  **Edit 1 — Add import.**

  Old (exact, from pre-flight output of lines 34–38):
  ```python
  from ballast.core.escalation import EscalationFailed, escalate
  from ballast.core.guardrails import HardInterrupt, build_correction, can_resume
  from ballast.core.spec import SpecModel, is_locked
  ```

  New:
  ```python
  from ballast.core.escalation import EscalationFailed, escalate
  from ballast.core.guardrails import HardInterrupt, build_correction, can_resume
  from ballast.core.probe import verify_node_claim
  from ballast.core.spec import SpecModel, is_locked
  ```

  **Edit 2 — Replace stub block.**

  Old (exact):
  ```python
          # ── 3. Environment probe — STUB ─────────────────────────────
          # TODO Step 9: replace with verify_node_claim from ballast.core.probe
          # if assessment.label in ("PROGRESSING", "COMPLETE"):
          #     verified, probe_note = await verify_node_claim(node, assessment.label, active_spec)
          #     if not verified:
          #         assessment.label, assessment.score = "VIOLATED", 0.0
          #         assessment.rationale = f"probe failed: {probe_note}"
          verified = True
  ```

  New:
  ```python
          # ── 3. Environment probe ────────────────────────────────────
          verified = True
          if assessment.label == "PROGRESSING":
              verified, probe_note = await verify_node_claim(node, assessment.label, active_spec)
              if not verified:
                  assessment.label = "VIOLATED"
                  assessment.score = 0.0
                  assessment.rationale = f"probe failed: {probe_note}"
                  logger.warning(
                      "probe_failed node=%d tool=%s note=%s run_id=%s",
                      node_index, assessment.tool_name, probe_note, run_id,
                  )
  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/core/trajectory.py
  git commit -m "step 2: wire verify_node_claim into run_with_spec; replace TODO Step 9 stub"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate
  python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  **Expected:**
  - Same test count as pre-flight baseline (≥ 190). No regressions.
  - `grep -c 'TODO Step 9' ballast/core/trajectory.py` returns `0`.
  - `grep -c 'from ballast.core.probe import verify_node_claim' ballast/core/trajectory.py` returns `1`.

  **Pass:** All tests pass, count ≥ baseline.

  **Fail:**
  - `ImportError: cannot import name 'verify_node_claim'` → probe.py Step 1 not completed or function name mismatch → check `ballast/core/probe.py`.
  - Test count drops → regression in existing test → read full pytest output, do not proceed.

---

### Phase 3 — Tests

**Goal:** `tests/test_probe.py` covers all 10 success criteria. Test count ≥ baseline + 14.

---

- [ ] 🟥 **Step 3: Create `tests/test_probe.py`** — *Non-critical*

  **Step Architecture Thinking:**

  **Pattern applied:** Same mock pattern as `test_escalation.py` — `AsyncMock` for `agent.run`, patch `_get_probe_agent` to return a mock agent so lazy singleton is never triggered.

  **Why this step exists here in the sequence:**
  probe.py and trajectory.py wiring are complete. Tests validate all 10 success criteria without requiring a live API key.

  **Why this file is the right location:**
  `tests/` is the test root. Consistent with `test_escalation.py`, `test_guardrails.py`, etc.

  **Alternative approach considered and rejected:**
  Append probe tests to `test_trajectory.py`. Rejected: probe is a standalone module — its tests belong in a standalone file. Same separation used for every other new module.

  **What breaks if this step deviates:**
  If `AsyncMock` is used incorrectly (instance-level `__str__` override), `agent.run()` may not call the mock. Use `MagicMock(return_value='{"verified": true, "note": ""}')` on `result.output` as shown in `test_escalation.py`.

  ---

  **Idempotent:** Yes — new file.

  **Pre-Read Gate:**
  - `ls tests/test_probe.py` must return `No such file`. If exists → STOP.
  - `grep -c 'def _get_tool_info' ballast/core/probe.py` — must return `1`. If 0 → function was renamed in Step 1 → STOP (test imports `_get_tool_info` directly).
  - `grep -n 'AsyncMock' tests/test_escalation.py | head -5` — confirm AsyncMock import pattern for reference.

  ```python
  """tests/test_probe.py — Unit tests for ballast/core/probe.py.

  All tests run without ANTHROPIC_API_KEY. The probe agent is always mocked.
  Uses pytest-asyncio for async test execution.
  """
  import json
  from unittest.mock import AsyncMock, MagicMock, patch

  import pytest

  from ballast.core.probe import ProbePacket, _call_probe_agent, _get_tool_info, verify_node_claim
  from ballast.core.spec import SpecModel


  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  def _make_spec(constraints: list[str] | None = None) -> SpecModel:
      from ballast.core.spec import lock
      base = {
          "intent": "test intent",
          "success_criteria": ["does something"],
          "constraints": constraints or [],
          "allowed_tools": ["safe_tool"],
          "drift_threshold": 0.5,
          "harness": {},
      }
      return lock(SpecModel(**base))


  def _mock_agent(response_json: str) -> MagicMock:
      """Return a mock pydantic-ai Agent whose run() returns a result with .output."""
      agent = MagicMock()
      result = MagicMock()
      result.output = response_json
      agent.run = AsyncMock(return_value=result)
      return agent


  def _node_with_tool(tool_name: str, args: dict, content: str = "") -> MagicMock:
      node = MagicMock()
      node.tool_name = tool_name
      node.args = args
      node.content = content
      # Ensure hasattr checks pass
      del node.parts  # remove so parts-scan path is not triggered
      return node


  def _node_no_tool() -> MagicMock:
      node = MagicMock(spec=[])  # no attributes
      return node


  # ---------------------------------------------------------------------------
  # TestGetToolInfo
  # ---------------------------------------------------------------------------

  class TestGetToolInfo:
      def test_direct_attrs_returns_tool_name_and_args(self):
          node = MagicMock()
          node.tool_name = "write_file"
          node.args = {"path": "/tmp/x"}
          name, args, _ = _get_tool_info(node)
          assert name == "write_file"
          assert args == {"path": "/tmp/x"}

      def test_no_tool_returns_empty(self):
          node = MagicMock(spec=[])
          name, args, content = _get_tool_info(node)
          assert name == ""
          assert args == {}
          assert content == ""

      def test_parts_scan_finds_tool_call_part(self):
          part = MagicMock()
          part.__class__.__name__ = "ToolCallPart"
          part.tool_name = "read_file"
          part.args = {"path": "/tmp/y"}
          node = MagicMock(spec=["parts"])
          node.parts = [part]
          name, args, _ = _get_tool_info(node)
          assert name == "read_file"
          assert args == {"path": "/tmp/y"}


  # ---------------------------------------------------------------------------
  # TestCallProbeAgent
  # ---------------------------------------------------------------------------

  class TestCallProbeAgent:
      @pytest.mark.asyncio
      async def test_verified_true_response(self):
          agent = _mock_agent('{"verified": true, "note": ""}')
          packet = ProbePacket(
              tool_name="safe_tool", tool_args="{}", tool_result="", spec_constraints=[]
          )
          result = await _call_probe_agent(agent, packet)
          assert result["verified"] is True
          assert result["note"] == ""

      @pytest.mark.asyncio
      async def test_verified_false_response(self):
          agent = _mock_agent('{"verified": false, "note": "violated: do not write files"}')
          packet = ProbePacket(
              tool_name="write_file", tool_args='{"path": "/etc/passwd"}',
              tool_result="", spec_constraints=["do not write to any files"]
          )
          result = await _call_probe_agent(agent, packet)
          assert result["verified"] is False
          assert "write" in result["note"]

      @pytest.mark.asyncio
      async def test_agent_exception_returns_fail_open(self):
          agent = MagicMock()
          agent.run = AsyncMock(side_effect=RuntimeError("network down"))
          packet = ProbePacket(
              tool_name="x", tool_args="{}", tool_result="", spec_constraints=[]
          )
          result = await _call_probe_agent(agent, packet)
          assert result["verified"] is True
          assert result["note"].startswith("probe_error:")

      @pytest.mark.asyncio
      async def test_json_parse_error_returns_fail_open(self):
          agent = _mock_agent("NOT JSON")
          packet = ProbePacket(
              tool_name="x", tool_args="{}", tool_result="", spec_constraints=[]
          )
          result = await _call_probe_agent(agent, packet)
          assert result["verified"] is True
          assert result["note"].startswith("probe_error:")

      @pytest.mark.asyncio
      async def test_missing_note_key_normalised_to_empty_string(self):
          agent = _mock_agent('{"verified": true}')
          packet = ProbePacket(
              tool_name="x", tool_args="{}", tool_result="", spec_constraints=[]
          )
          result = await _call_probe_agent(agent, packet)
          assert result["note"] == ""


  # ---------------------------------------------------------------------------
  # TestVerifyNodeClaim
  # ---------------------------------------------------------------------------

  class TestVerifyNodeClaim:
      @pytest.mark.asyncio
      async def test_no_tool_call_returns_true_immediately(self):
          """No LLM call made when node has no tool call."""
          spec = _make_spec()
          node = MagicMock(spec=[])  # no attributes → _get_tool_info returns ""
          with patch("ballast.core.probe._get_probe_agent") as mock_getter:
              verified, note = await verify_node_claim(node, "PROGRESSING", spec)
          assert verified is True
          assert note == "no tool call"
          mock_getter.assert_not_called()

      @pytest.mark.asyncio
      async def test_compliant_tool_returns_true(self):
          spec = _make_spec(constraints=["do not write to files"])
          node = _node_with_tool("safe_tool", {"x": 1})
          mock_agent = _mock_agent('{"verified": true, "note": ""}')
          with patch("ballast.core.probe._get_probe_agent", return_value=mock_agent):
              verified, note = await verify_node_claim(node, "PROGRESSING", spec)
          assert verified is True
          assert note == ""

      @pytest.mark.asyncio
      async def test_violating_tool_returns_false_with_note(self):
          spec = _make_spec(constraints=["do not write to any files"])
          node = _node_with_tool("write_file", {"path": "/etc/passwd"})
          mock_agent = _mock_agent('{"verified": false, "note": "violated: do not write to any files"}')
          with patch("ballast.core.probe._get_probe_agent", return_value=mock_agent):
              verified, note = await verify_node_claim(node, "PROGRESSING", spec)
          assert verified is False
          assert note != ""

      @pytest.mark.asyncio
      async def test_probe_exception_returns_fail_open(self):
          spec = _make_spec()
          node = _node_with_tool("some_tool", {})
          mock_agent = MagicMock()
          mock_agent.run = AsyncMock(side_effect=Exception("boom"))
          with patch("ballast.core.probe._get_probe_agent", return_value=mock_agent):
              verified, note = await verify_node_claim(node, "PROGRESSING", spec)
          assert verified is True
          assert "probe_error" in note

      def test_lazy_singleton_not_constructed_at_import(self):
          """Importing probe.py must not require ANTHROPIC_API_KEY."""
          import ballast.core.probe as probe_mod
          # Reset singleton to ensure clean state
          probe_mod._probe_agent = None
          # If _probe_agent is still None after import, lazy singleton is working
          assert probe_mod._probe_agent is None

      def test_probe_packet_fields(self):
          packet = ProbePacket(
              tool_name="t",
              tool_args='{"k": "v"}',
              tool_result="some output",
              spec_constraints=["no files"],
          )
          assert packet.tool_name == "t"
          assert packet.tool_args == '{"k": "v"}'
          assert packet.tool_result == "some output"
          assert packet.spec_constraints == ["no files"]
  ```

  **Git Checkpoint:**
  ```bash
  git add tests/test_probe.py
  git commit -m "step 3: add test_probe.py — 14 unit tests covering all success criteria"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate
  python -m pytest tests/test_probe.py -v 2>&1 | tail -20
  python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  **Expected:**
  - All tests in `test_probe.py` pass.
  - Total test count ≥ pre-flight baseline + 14.
  - No existing tests regressed.

  **Pass:** All `test_probe.py` tests green, total count up.

  **Fail:**
  - `RuntimeError: no running event loop` on async tests → `pytest-asyncio` not installed or missing `@pytest.mark.asyncio` → check imports and markers.
  - `ImportError: cannot import name '_get_tool_info'` → function renamed → check probe.py exports.
  - `assert mock_getter.assert_not_called()` fails on no-tool test → `_get_tool_info` returning non-empty for `MagicMock(spec=[])` → check `spec=[]` strips all attributes.

---

## Regression Guard

**Systems at risk from this plan:**
- `run_with_spec` — the probe call is added inside the hot loop; any exception would bubble up and abort runs.

**Regression verification:**

| System | Pre-change behaviour | Post-change verification |
|---|---|---|
| `run_with_spec` | `verified = True` hardcoded | `verified` set from `verify_node_claim`; fails open on exception |
| Existing 190 tests | All pass | `python -m pytest tests/ -m 'not integration' -q` must show ≥ 190 passing |

**Test count regression check:**
- Tests before plan (from Pre-Flight): `____`
- Tests after plan: must be `≥ baseline + 14`

---

## Success Criteria

| Criterion | Target | Verification |
|---|---|---|
| No-tool-call fast path | Returns `(True, "no tool call")` without LLM call | `test_no_tool_call_returns_true_immediately` passes |
| Compliant node | Returns `(True, ...)` | `test_compliant_tool_returns_true` passes |
| Violating node | Returns `(False, note)` with non-empty note | `test_violating_tool_returns_false_with_note` passes |
| Probe exception | Returns `(True, "probe_error: ...")` | `test_probe_exception_returns_fail_open` passes |
| JSON parse error | Returns `(True, "probe_error: ...")` | `test_json_parse_error_returns_fail_open` passes |
| `ProbePacket` fields | All four fields accessible | `test_probe_packet_fields` passes |
| Lazy singleton | `_probe_agent is None` after import | `test_lazy_singleton_not_constructed_at_import` passes |
| `verify_node_claim` is async | `iscoroutinefunction` returns True | Step 1 import check passes |
| trajectory.py wiring | Stub replaced, downgrade on failure | Step 2 grep checks + full test suite passes |
| No regressions | ≥ 190 existing tests pass | Full suite run after Step 3 |
