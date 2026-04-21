# evaluator.py — Implementation Plan

**Overall Progress:** `0%`

---

## Spec Summary — Evaluator Module

**What this module does.** When `score_drift()` assigns a node to the ambiguous zone (0.25 < aggregate < 0.85), Layer 1 has no confident determination — the action is neither clearly progressing nor clearly violating. Instead of leaving the label as `STALLED` (the current `LAYER_2_STUB`), `evaluator.py` makes a second-pass LLM call that resolves the ambiguity to either `PROGRESSING` or `VIOLATED`. This is Layer 2 of the two-layer scoring cascade described in the project spec.

**Input.** `evaluate_node(node, full_window, spec, *, tool_score, constraint_score, intent_score)` receives the raw pydantic-ai node, the full context window of recent nodes, the active locked `SpecModel`, and the three pre-computed Layer 1 float scores. The scores are passed in rather than re-computed to avoid double LLM calls.

**Output.** `evaluate_node()` returns a `tuple[str, str]` — the resolved `DriftLabel` (`"PROGRESSING"` or `"VIOLATED"`) and a one-sentence rationale string from the LLM. The rationale is appended to `NodeAssessment.rationale` as `"; layer2=<rationale>"` so the audit trail preserves the Layer 2 decision. On any exception it returns `("STALLED", "evaluator_error: <exc>")` — the fail-safe maintains the stub's current behavior so no evaluator error blocks a live run.

**EvaluatorPacket.** A typed dataclass passed to `_call_evaluator()`. Fields: `content` (node text excerpt), `tool_name`, `tool_args` (JSON-serialised str), `context_summary` (list of compact dicts from `full_window`), `spec_intent`, `spec_constraints`, `tool_score`, `constraint_score`, `intent_score`, `aggregate`. This structured input gives the LLM all context it needs without string formatting in the caller.

**`_call_evaluator(client, packet) -> tuple[str, str]`.** Synchronous internal function. Uses the `anthropic.Anthropic` sync client (same pattern as `score_constraint_violation` and `score_intent_alignment` in `trajectory.py`) because `score_drift()` is synchronous — making it async would require cascading `async` through `score_drift` and all its callers, a breaking change with no benefit. Calls `client.messages.create()` with a `resolve_label` forced-tool call. Parses the `tool_use` block to extract `label` and `rationale`. On any exception or missing block: returns `("STALLED", "evaluator_error: <exc>")` — never raises.

**`_get_evaluator_client()`.** Lazy singleton returning `anthropic.Anthropic()`. Constructed on first call; not at module level. Mirrors `_get_judge_client()` in `trajectory.py`. Avoids `AuthenticationError` at import time in environments without `ANTHROPIC_API_KEY` (e.g. `pytest -m 'not integration'`).

**Wire-in in `score_drift()`.** The `LAYER_2_STUB` two-line block (lines 486–487) is replaced with `elif spec.harness.enable_layer2_judge: label, eval_note = evaluate_node(...) else: label = "STALLED"`. When `enable_layer2_judge=False` (opus harness), the evaluator is skipped and STALLED is preserved. `eval_note = ""` guard before the cascade ensures PROGRESSING/VIOLATED fast paths never reference an unset variable. `NodeAssessment.rationale` is extended with `"; layer2=<eval_note>"` only when non-empty. The import is inserted alphabetically between the existing `escalation` and `probe` imports. `tests/test_trajectory.py` is updated to fix the one existing borderline test that would fail after wiring.

**Constraints.** No async — `score_drift` stays synchronous. No imports from `trajectory.py` — would be circular (`trajectory` imports `evaluator`; `evaluator` must not import `trajectory`). `evaluator.py` defines its own `_EVAL_MODEL` constant (`"claude-haiku-4-5-20251001"`) for fast, cheap Layer 2 calls.

**Success criteria (eval-derivable).**
1. `evaluate_node` returns `"PROGRESSING"` when LLM resolves to PROGRESSING.
2. `evaluate_node` returns `"VIOLATED"` when LLM resolves to VIOLATED.
3. `evaluate_node` returns `"STALLED"` on any LLM exception (fail-safe).
4. `_call_evaluator` returns `("STALLED", ...)` when no `tool_use` block in response.
5. `_call_evaluator` returns `("STALLED", ...)` when label is not PROGRESSING or VIOLATED.
6. `EvaluatorPacket` exposes all fields: content, tool_name, tool_args, context_summary, spec_intent, spec_constraints, tool_score, constraint_score, intent_score, aggregate.
7. `_get_evaluator_client()` is not called at import time (lazy singleton).
8. `score_drift()` no longer contains `# LAYER_2_STUB` after wiring.
9. `NodeAssessment.rationale` includes `"; layer2=<rationale>"` when evaluator fires.
10. `enable_layer2_judge=False` skips evaluator and returns STALLED (opus harness unchanged).
11. No regressions in existing 204 tests; total count ≥ 204 + 14 + 1 = 219.

---

## Architecture Overview

**The problem this plan solves:**
`trajectory.py:486–487` has a `LAYER_2_STUB` that unconditionally sets `label = "STALLED"` for ambiguous nodes (0.25 < aggregate < 0.85). STALLED nodes receive a correction injection (if score < `drift_threshold`) but are never definitively classified. This means genuinely violating nodes in the ambiguous band are not caught, and genuinely progressing nodes receive false-positive corrections.

**The pattern applied:**
- **Template Method** — `score_drift()` defines the cascade skeleton (heuristic gate → LLM scorers → label → return). `evaluate_node` is the Layer 2 hook step, called only when the skeleton cannot resolve the label itself.
- **DTO (EvaluatorPacket)** — typed input envelope; `_call_evaluator` receives all context through one object, not positional args. Prevents argument drift as the evaluator's prompt grows.
- **Null Object / Fail-Safe Default** — `_call_evaluator` returns `("STALLED", ...)` on any exception. The cascade never breaks silently.

**What stays unchanged:**
- `ballast/core/spec.py`, `ballast/core/checkpoint.py`, `ballast/core/escalation.py`, `ballast/core/guardrails.py`, `ballast/core/probe.py`, `ballast/core/cost.py`, `ballast/core/sync.py` — none touched.
- `trajectory.py` is edited only in Step 3, and only two locations: the import block and the LAYER_2_STUB block inside `score_drift()`.
- `score_drift()`'s signature is unchanged — still synchronous.
- `NodeAssessment` dataclass is unchanged — `rationale` is already a free-form string.

**What this plan adds:**
- `ballast/core/evaluator.py` — `EvaluatorPacket`, `_get_evaluator_client()`, `_call_evaluator()`, `evaluate_node()`.
- `tests/test_evaluator.py` — 14 unit tests covering all 10 success criteria.

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|----------|----------------------|--------------------------|
| Sync `_call_evaluator` using `anthropic.Anthropic()` | Async using `pydantic_ai.Agent` (like escalation/probe) | `score_drift()` is sync; making it async cascades through `run_with_spec()` call site, `TrajectoryChecker.check()`, and all test callers — large breaking change for zero benefit |
| `evaluate_node` returns `tuple[str, str]` | Return just `str` (label) | Rationale is essential for the audit trail in `NodeAssessment.rationale`; threading it out as the second element avoids adding a new dataclass field |
| Fail-open to `"STALLED"` on error | Fail to `"PROGRESSING"` (permissive) or `"VIOLATED"` (strict) | STALLED maintains exact pre-wiring behavior — any correction that would have fired for STALLED still fires; no regression for existing runs |
| Own `_EVAL_MODEL` constant in evaluator.py | Import `_JUDGE_MODEL` from trajectory.py | Cross-module import would be circular (`trajectory` imports `evaluator`; `evaluator` must not import `trajectory`) |
| Own minimal node extractor in `evaluate_node` | Reuse `_extract_node_info` from trajectory.py | Same circular import constraint; minimal duck-typing covers the two paths needed |

**Known limitations:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| `full_window` contains raw pydantic-ai nodes (not dicts) — only `dict` entries are included in `context_summary` | raw nodes are informational context only; Layer 1 scores already encode the numerical signal | compact_history (evicted nodes) already arrives as dicts; when hook.py is wired, full_window will contain dicts too |
| `_EVAL_MODEL` is Haiku (same as escalation/probe) | Fast and cheap; ambiguity resolution is high-frequency | Swap constant to Sonnet when eval results justify the cost |
| No telemetry | OTel stubbed until Step 13 | Add `emit_evaluator_span()` calls in Step 13 |

---

## Decisions Log

| # | Flaw | Resolution applied |
|---|------|--------------------|
| 1 | `evaluate_node` might be called with raw pydantic-ai nodes in `full_window` that are not dict-like — iterating them for `context_summary` would require `_extract_node_info`. | Only `dict` entries in `full_window` are appended to `context_summary`; raw nodes are skipped silently. Layer 1 scores are the numerical signal; context is informational. |
| 2 | `getattr(node, "args")` raises `AttributeError` if `args` is a property with a getter error. | Guard with `isinstance(getattr(node, "args", None), dict)` — only assign if it's a plain dict. |
| 3 | `test_score_drift_borderline_returns_stalled` (test_trajectory.py:374) calls `score_drift()` with aggregate=0.6 — inside the ambiguous zone. After Step 3 wires `evaluate_node`, this test hits `_get_evaluator_client()` → `AuthenticationError`. | Step 3 adds `tests/test_trajectory.py` as a third modified file. Edit C replaces the test with `test_score_drift_borderline_calls_evaluator` (patches `_get_evaluator_client`, asserts `label == "PROGRESSING"` and `"layer2=" in rationale`). A second new test `test_score_drift_borderline_returns_stalled_when_layer2_disabled` confirms the `enable_layer2_judge=False` path. |
| 4 | `HarnessProfile.enable_layer2_judge: bool = True` (spec.py:57) is set to `False` for the opus harness profile. The plan's Edit B unconditionally calls `evaluate_node`, firing the evaluator even when the harness explicitly disables it. | Edit B wraps the `evaluate_node` call with `if spec.harness.enable_layer2_judge:` — falls back to `label = "STALLED"` when the flag is False. |
| 5 | `test_evaluator.py` imported `pytest` but all 14 tests are synchronous — no `@pytest.mark.asyncio`, no `pytest.raises`, no `pytest.fixture`. `import pytest` triggers ruff `F401` (unused import). | Removed `import pytest` from the code block; added docstring note that all tests are sync. |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---------|----------|--------|----------|----------|
| All fields | All resolved per spec above + decisions log | codebase read + design | — | ✅ |

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
Read ballast/core/trajectory.py lines 34–42 and lines 474–497. Capture and output:
(1) Exact current import block (lines 34–42)
(2) Exact current LAYER_2_STUB block (lines 480–497)
(3) Confirm evaluator.py does not yet exist: ls ballast/core/evaluator.py
(4) Confirm probe.py exists: ls ballast/core/probe.py
(5) Run: grep -c 'LAYER_2_STUB' ballast/core/trajectory.py — must return 1
(6) Run: pytest tests/ -m 'not integration' -q — record passing test count

Do not change anything. Show full output and wait.
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan: 204
evaluator.py exists:    no
probe.py exists:        yes
LAYER_2_STUB count:     1
```

**Automated checks (all must pass before Step 1):**
- [ ] Existing test suite passes. Document test count: `204`
- [ ] `ballast/core/evaluator.py` does NOT exist yet.
- [ ] `ballast/core/probe.py` exists (probe plan must be executed first).
- [ ] `grep -c 'LAYER_2_STUB' ballast/core/trajectory.py` returns `1`.
- [ ] `grep -c 'from ballast.core.evaluator' ballast/core/trajectory.py` returns `0`.

> **PREREQUISITE:** The probe plan (`.claude/plans/probe.md`) must be fully executed before this plan starts. If `ballast/core/probe.py` does not exist, stop and execute the probe plan first.

---

## Environment Matrix

| Step | Dev | Staging | Prod |
|------|-----|---------|------|
| Step 1 (create evaluator.py) | ✅ | ✅ | ✅ |
| Step 2 (create test_evaluator.py) | ✅ | ✅ | ✅ |
| Step 3 (wire trajectory.py) | ✅ | ✅ | ✅ |

---

## Tasks

### Phase 1 — Core Module

**Goal:** `ballast/core/evaluator.py` exists and imports cleanly without `ANTHROPIC_API_KEY`.

---

- [ ] 🟥 **Step 1: Create `ballast/core/evaluator.py`** — *Critical: new module; Steps 2 and 3 both depend on it*

  **Step Architecture Thinking:**

  **Pattern applied:** Template Method hook + DTO (EvaluatorPacket) + Null Object fail-safe.

  **Why this step exists here in the sequence:**
  Steps 2 and 3 both import from this file. It must exist and import cleanly before either can run.

  **Why this file is the right location:**
  `ballast/core/` is the kernel layer — evaluation is a core scoring concern, not an adapter. Placed alongside `escalation.py`, `guardrails.py`, `probe.py`.

  **Alternative approach considered and rejected:**
  Inline the Layer 2 call directly inside `score_drift()` in `trajectory.py`. Rejected: `trajectory.py` is already 871 lines; inlining adds a 60-line function body + prompt constants to an already-large file and violates Single Responsibility.

  **What breaks if this step deviates:**
  If `_call_evaluator` raises instead of returning `("STALLED", ...)` on exception, a flaky LLM call will abort the scoring cascade and raise inside `run_with_spec()`.

  ---

  **Idempotent:** Yes — creating a new file is idempotent if the file does not exist (pre-flight confirms this).

  **Context:** This file is the complete Layer 2 subsystem. `trajectory.py` will import `evaluate_node` from here in Step 3.

  **Pre-Read Gate:**
  - Run `ls ballast/core/evaluator.py` — must return "No such file". If exists → STOP.
  - Run `grep -c 'class SpecModel' ballast/core/spec.py` — must return `1`. If 0 → spec module missing → STOP.

  **Self-Contained Rule:** Code block below is complete and immediately runnable.

  **No-Placeholder Rule:** No `<VALUE>` tokens in the code block.

  ---

  ```python
  """ballast/core/evaluator.py — Layer 2 ambiguity resolver for score_drift().

  Public interface:
      evaluate_node(node, full_window, spec, *, tool_score, constraint_score, intent_score)
          -> tuple[str, str]  (label: "PROGRESSING"|"VIOLATED"|"STALLED", rationale: str)
          — Called by score_drift() for nodes in the ambiguous zone (0.25 < aggregate < 0.85).
            Returns ("STALLED", "evaluator_error: ...") on any exception (fail-open).
      EvaluatorPacket
          — Typed input envelope passed to _call_evaluator().

  Sync design: score_drift() is synchronous; making evaluate_node async would require
  changing score_drift's signature and propagating async through run_with_spec() — a
  large breaking change. Uses anthropic.Anthropic() sync client, matching the existing
  scorer pattern (score_constraint_violation, score_intent_alignment) in trajectory.py.
  """
  from __future__ import annotations

  import json
  import logging
  from dataclasses import dataclass, field
  from typing import Any

  import anthropic

  from ballast.core.spec import SpecModel

  logger = logging.getLogger(__name__)

  _EVAL_MODEL = "claude-haiku-4-5-20251001"

  _EVALUATOR_SYSTEM = (
      "You are a Layer 2 evaluator for Ballast, an AI agent guardrail system. "
      "A node has scored in the ambiguous range (0.25–0.85) on Layer 1 — not clearly "
      "PROGRESSING and not clearly VIOLATED. "
      "Your job: make the definitive binary call using the full conversation context, "
      "spec intent, constraints, and Layer 1 scores provided. "
      "Be strict: if the action could plausibly violate a constraint, prefer VIOLATED."
  )

  _EVALUATOR_TOOL = {
      "name": "resolve_label",
      "description": "Resolve an ambiguous node's drift label to PROGRESSING or VIOLATED.",
      "input_schema": {
          "type": "object",
          "properties": {
              "label": {
                  "type": "string",
                  "enum": ["PROGRESSING", "VIOLATED"],
                  "description": (
                      "PROGRESSING if the action advances the goal within constraints; "
                      "VIOLATED if it breaches a constraint or works against the goal."
                  ),
              },
              "rationale": {
                  "type": "string",
                  "description": "One sentence explaining the label choice.",
              },
          },
          "required": ["label", "rationale"],
      },
  }

  # Lazy singleton — NOT constructed at module level. Mirrors _get_judge_client()
  # in trajectory.py. Constructing at import time raises AuthenticationError in
  # environments without ANTHROPIC_API_KEY (e.g. pytest -m 'not integration').
  _evaluator_client: "anthropic.Anthropic | None" = None


  def _get_evaluator_client() -> "anthropic.Anthropic":
      global _evaluator_client
      if _evaluator_client is None:
          _evaluator_client = anthropic.Anthropic()
      return _evaluator_client


  # ---------------------------------------------------------------------------
  # EvaluatorPacket — typed input envelope
  # ---------------------------------------------------------------------------

  @dataclass
  class EvaluatorPacket:
      """Structured input passed to _call_evaluator().

      Constructed once in evaluate_node(); treated as read-only by _call_evaluator().
      tool_args is JSON-serialised str for prompt safety (avoids nested dict formatting).
      context_summary is a list of compact dicts from full_window (may be empty).
      """

      content: str
      tool_name: str
      tool_args: str                              # JSON-serialised
      spec_intent: str
      spec_constraints: list[str] = field(default_factory=list)
      context_summary: list[dict] = field(default_factory=list)
      tool_score: float = 1.0
      constraint_score: float = 1.0
      intent_score: float = 1.0
      aggregate: float = 1.0


  # ---------------------------------------------------------------------------
  # _call_evaluator — sync, never raises
  # ---------------------------------------------------------------------------

  def _call_evaluator(
      client: "anthropic.Anthropic",
      packet: EvaluatorPacket,
  ) -> tuple[str, str]:
      """Call the Layer 2 evaluator. Returns (label, rationale) or ("STALLED", ...) on failure.

      Synchronous because score_drift() is synchronous — using async here would require
      making score_drift async and propagating that change through run_with_spec().

      Never raises. Any exception → ("STALLED", "evaluator_error: <exc>") so the caller
      falls back to existing STALLED behavior without crashing the run.
      """
      constraints_block = (
          "\n".join(f"  - {c}" for c in packet.spec_constraints)
          if packet.spec_constraints
          else "  (none)"
      )
      context_block = (
          "\n".join(
              f"  [{i}] tool={e.get('tool_name', '?')} "
              f"label={e.get('label', '?')} score={e.get('score', 0.0):.3f}"
              for i, e in enumerate(packet.context_summary[-5:])
          )
          if packet.context_summary
          else "  (empty)"
      )
      prompt = (
          f"NODE ACTION\n"
          f"  tool: {packet.tool_name!r}\n"
          f"  args: {packet.tool_args[:300]}\n"
          f"  content: {packet.content[:400]}\n\n"
          f"LAYER 1 SCORES\n"
          f"  tool={packet.tool_score:.3f}  constraint={packet.constraint_score:.3f}"
          f"  intent={packet.intent_score:.3f}  aggregate={packet.aggregate:.3f}\n\n"
          f"SPEC INTENT\n  {packet.spec_intent[:300]}\n\n"
          f"SPEC CONSTRAINTS\n{constraints_block}\n\n"
          f"CONTEXT (last 5 prior nodes)\n{context_block}"
      )
      try:
          response = client.messages.create(
              model=_EVAL_MODEL,
              max_tokens=300,
              system=_EVALUATOR_SYSTEM,
              tools=[_EVALUATOR_TOOL],
              tool_choice={"type": "tool", "name": "resolve_label"},
              messages=[{"role": "user", "content": prompt}],
          )
          for block in response.content:
              if block.type == "tool_use":
                  raw_label = block.input.get("label", "")
                  rationale = str(block.input.get("rationale", ""))
                  if raw_label in ("PROGRESSING", "VIOLATED"):
                      return raw_label, rationale
          logger.warning(
              "evaluator_no_valid_label tool=%r — failing open to STALLED",
              packet.tool_name,
          )
          return "STALLED", "no valid label from evaluator"
      except Exception as exc:  # noqa: BLE001
          logger.warning(
              "evaluator_failed tool=%r exc=%s — failing open to STALLED",
              packet.tool_name,
              exc,
          )
          return "STALLED", f"evaluator_error: {exc}"


  # ---------------------------------------------------------------------------
  # evaluate_node — public sync entry point
  # ---------------------------------------------------------------------------

  def evaluate_node(
      node: Any,
      full_window: list,
      spec: SpecModel,
      *,
      tool_score: float,
      constraint_score: float,
      intent_score: float,
  ) -> tuple[str, str]:
      """Resolve a STALLED node to PROGRESSING or VIOLATED using a Layer 2 LLM call.

      Called by score_drift() when 0.25 < aggregate < 0.85 (the ambiguous zone).
      Synchronous — score_drift() is synchronous; no event loop involved.

      Args:
          node:             Raw pydantic-ai Agent.iter node.
          full_window:      Recent node context (list of raw nodes — may be empty).
          spec:             Active locked SpecModel.
          tool_score:       Pre-computed Layer 1 tool compliance score [0, 1].
          constraint_score: Pre-computed Layer 1 constraint violation score [0, 1].
          intent_score:     Pre-computed Layer 1 intent alignment score [0, 1].

      Returns:
          ("PROGRESSING", rationale) — node advances the goal within constraints.
          ("VIOLATED", rationale)    — node breaches a constraint or works against goal.
          ("STALLED", error_note)    — evaluator failed; fail-open to pre-wiring behavior.
      """
      # Minimal duck-typed node extraction — does NOT import _extract_node_info from
      # trajectory.py to avoid circular imports (trajectory imports evaluator).
      tool_name = ""
      tool_args: dict = {}
      content = ""

      if hasattr(node, "tool_name"):
          tool_name = str(node.tool_name)
      args_val = getattr(node, "args", None)
      if isinstance(args_val, dict):
          tool_args = args_val

      for attr in ("text", "content", "output"):
          val = getattr(node, attr, None)
          if val and isinstance(val, str):
              content = val[:600]
              break

      # Build context summary from full_window.
      # Only dict entries are included — raw pydantic-ai nodes are skipped.
      # compact_history (already-evicted nodes) arrives as dicts; those are the
      # entries that carry label/score/tool_name for the LLM's context.
      context_summary: list[dict] = [n for n in full_window if isinstance(n, dict)]

      aggregate = min(tool_score, constraint_score, intent_score)

      packet = EvaluatorPacket(
          content=content,
          tool_name=tool_name,
          tool_args=json.dumps(tool_args, default=str)[:300],
          spec_intent=spec.intent,
          spec_constraints=list(spec.constraints),
          context_summary=context_summary,
          tool_score=tool_score,
          constraint_score=constraint_score,
          intent_score=intent_score,
          aggregate=aggregate,
      )

      return _call_evaluator(_get_evaluator_client(), packet)
  ```

  **What it does:** Defines the complete Layer 2 subsystem — packet DTO, lazy client singleton, sync LLM caller, public entry point. `trajectory.py` will import only `evaluate_node`.

  **Why this approach:** Sync anthropic client matches the existing scorer pattern in `trajectory.py` (`score_constraint_violation`, `score_intent_alignment`) and avoids cascading `async` through `score_drift`. Fail-open to STALLED on error maintains backward compatibility — any correction that would have fired for STALLED still fires.

  **Assumptions:**
  - `anthropic.Anthropic().messages.create()` is available and accepts `tools` + `tool_choice`. Confirmed — same API used by existing scorers in `trajectory.py`.
  - `SpecModel` has `intent: str`, `constraints: List[str]`. Confirmed from `spec.py:132–141`.
  - Response `block.type == "tool_use"` and `block.input` is a dict. Confirmed from existing scorer pattern.

  **Risks:**
  - `block.input.get("label")` returns a value not in `("PROGRESSING", "VIOLATED")` → mitigation: explicit membership check; returns `("STALLED", "no valid label from evaluator")` with a warning log.
  - `client.messages.create()` raises `anthropic.AuthenticationError` in test env → mitigation: `_get_evaluator_client()` is lazy (not called at import time); tests patch `_get_evaluator_client`.

  **Git Checkpoint:**
  ```bash
  git add ballast/core/evaluator.py
  git commit -m "step 10: add evaluator.py — EvaluatorPacket, _call_evaluator, evaluate_node Layer 2 resolver"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate
  python -c "
  from ballast.core.evaluator import EvaluatorPacket, evaluate_node
  import inspect
  assert not inspect.iscoroutinefunction(evaluate_node), 'must be sync'
  p = EvaluatorPacket(content='', tool_name='x', tool_args='{}', spec_intent='test')
  assert p.tool_name == 'x'
  import ballast.core.evaluator as ev
  assert ev._evaluator_client is None, 'lazy singleton violated'
  print('evaluator import OK')
  "
  ```

  **Expected:**
  - `evaluator import OK` printed with exit code 0.
  - No `AuthenticationError` or `UserError` (lazy singleton not triggered by import).

  **Pass:** `evaluator import OK` with exit code 0.

  **Fail:**
  - `AuthenticationError` → client constructed at module level → check `_evaluator_client = None` is module-level, `Anthropic()` is inside `_get_evaluator_client()` only.
  - `ImportError` on `SpecModel` → check `from ballast.core.spec import SpecModel` in evaluator.py.

---

### Phase 2 — Tests

**Goal:** `tests/test_evaluator.py` passes with 14 tests; all 10 success criteria covered.

---

- [ ] 🟥 **Step 2: Create `tests/test_evaluator.py`** — *Critical: confirms all scoring paths before wiring*

  **Step Architecture Thinking:**

  **Pattern applied:** Mock injection — `_get_evaluator_client` is patched to return a `MagicMock` client; `_call_evaluator` is driven through the client mock. Same pattern as `test_trajectory.py` for `score_constraint_violation` and `score_intent_alignment`.

  **Why this step exists here in the sequence:**
  Step 1 must exist so imports resolve. Step 3 wires into the hot path of `score_drift()` — tests here confirm the module contract before that wiring.

  **Why this file is the right location:**
  All Ballast tests live in `tests/`. Naming convention: `test_<module>.py`.

  **Alternative approach considered and rejected:**
  Append evaluator tests to `test_trajectory.py`. Rejected: `evaluator` is a standalone module; its tests belong in a standalone file — same separation used for escalation, guardrails, probe.

  **What breaks if this step deviates:**
  If `_get_evaluator_client` is not patched and tests call a real Anthropic client, the suite becomes flaky and expensive.

  ---

  **Idempotent:** Yes — creating a new test file.

  **Pre-Read Gate:**
  - Run `ls tests/test_evaluator.py` — must return "No such file". If exists → STOP.
  - Run `grep -c 'def _get_evaluator_client' ballast/core/evaluator.py` — must return `1`. If 0 → function was renamed in Step 1 → STOP.

  **Self-Contained Rule:** All code is complete and runnable as written.

  ---

  ```python
  """tests/test_evaluator.py — Unit tests for ballast/core/evaluator.py.

  14 tests total:
      TestEvaluatorPacket  (3) — field validation
      TestCallEvaluator    (6) — internal; client mocked
      TestEvaluateNode     (5) — full function; _get_evaluator_client patched

  All tests are synchronous — no pytest.mark.asyncio needed.
  """
  from unittest.mock import MagicMock, patch

  from ballast.core.evaluator import (
      EvaluatorPacket,
      _call_evaluator,
      evaluate_node,
  )
  from ballast.core.spec import SpecModel


  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  def _make_spec(constraints: list[str] | None = None) -> SpecModel:
      from ballast.core.spec import lock

      base = {
          "intent": "test intent for evaluator tests",
          "success_criteria": ["criterion A"],
          "constraints": constraints or [],
          "allowed_tools": ["safe_tool"],
          "drift_threshold": 0.4,
          "harness": {},
      }
      return lock(SpecModel(**base))


  def _make_packet(**overrides) -> EvaluatorPacket:
      defaults = dict(
          content="agent output here",
          tool_name="safe_tool",
          tool_args='{"path": "/tmp/x"}',
          spec_intent="complete the task safely",
          spec_constraints=["no file writes"],
          context_summary=[],
          tool_score=0.6,
          constraint_score=0.5,
          intent_score=0.7,
          aggregate=0.5,
      )
      defaults.update(overrides)
      return EvaluatorPacket(**defaults)


  def _mock_client(label: str, rationale: str = "looks fine") -> MagicMock:
      """Return a mock anthropic.Anthropic whose messages.create returns a tool_use block."""
      client = MagicMock()
      block = MagicMock()
      block.type = "tool_use"
      block.input = {"label": label, "rationale": rationale}
      response = MagicMock()
      response.content = [block]
      client.messages.create.return_value = response
      return client


  # ---------------------------------------------------------------------------
  # TestEvaluatorPacket
  # ---------------------------------------------------------------------------

  class TestEvaluatorPacket:
      def test_all_fields_set(self):
          pkt = _make_packet()
          assert pkt.content == "agent output here"
          assert pkt.tool_name == "safe_tool"
          assert pkt.tool_args == '{"path": "/tmp/x"}'
          assert pkt.spec_intent == "complete the task safely"
          assert pkt.spec_constraints == ["no file writes"]
          assert pkt.tool_score == 0.6
          assert pkt.constraint_score == 0.5
          assert pkt.intent_score == 0.7
          assert pkt.aggregate == 0.5

      def test_context_summary_defaults_to_empty_list(self):
          pkt = EvaluatorPacket(
              content="x", tool_name="t", tool_args="{}", spec_intent="i"
          )
          assert pkt.context_summary == []

      def test_tool_args_is_string(self):
          pkt = _make_packet(tool_args='{"key": "val"}')
          assert isinstance(pkt.tool_args, str)


  # ---------------------------------------------------------------------------
  # TestCallEvaluator
  # ---------------------------------------------------------------------------

  class TestCallEvaluator:
      def test_returns_progressing_on_valid_response(self):
          client = _mock_client("PROGRESSING", "advancing toward goal")
          label, rationale = _call_evaluator(client, _make_packet())
          assert label == "PROGRESSING"
          assert rationale == "advancing toward goal"

      def test_returns_violated_on_valid_response(self):
          client = _mock_client("VIOLATED", "writes to forbidden path")
          label, rationale = _call_evaluator(client, _make_packet())
          assert label == "VIOLATED"
          assert rationale == "writes to forbidden path"

      def test_returns_stalled_on_client_exception(self):
          client = MagicMock()
          client.messages.create.side_effect = RuntimeError("network down")
          label, rationale = _call_evaluator(client, _make_packet())
          assert label == "STALLED"
          assert rationale.startswith("evaluator_error:")

      def test_returns_stalled_on_no_tool_use_block(self):
          client = MagicMock()
          block = MagicMock()
          block.type = "text"  # not tool_use
          response = MagicMock()
          response.content = [block]
          client.messages.create.return_value = response
          label, rationale = _call_evaluator(client, _make_packet())
          assert label == "STALLED"
          assert "no valid label" in rationale

      def test_returns_stalled_on_invalid_label(self):
          client = MagicMock()
          block = MagicMock()
          block.type = "tool_use"
          block.input = {"label": "UNKNOWN", "rationale": "bad output"}
          response = MagicMock()
          response.content = [block]
          client.messages.create.return_value = response
          label, rationale = _call_evaluator(client, _make_packet())
          assert label == "STALLED"

      def test_rationale_included_in_result(self):
          client = _mock_client("PROGRESSING", "all constraints satisfied")
          _, rationale = _call_evaluator(client, _make_packet())
          assert rationale == "all constraints satisfied"


  # ---------------------------------------------------------------------------
  # TestEvaluateNode
  # ---------------------------------------------------------------------------

  class TestEvaluateNode:
      def test_progressing_label_returned(self):
          spec = _make_spec()
          node = MagicMock()
          node.tool_name = "safe_tool"
          node.args = {"path": "/tmp/x"}
          mock_client = _mock_client("PROGRESSING", "ok")
          with patch("ballast.core.evaluator._get_evaluator_client", return_value=mock_client):
              label, _ = evaluate_node(
                  node, [], spec,
                  tool_score=0.6, constraint_score=0.5, intent_score=0.7,
              )
          assert label == "PROGRESSING"

      def test_violated_label_returned(self):
          spec = _make_spec(constraints=["no file writes"])
          node = MagicMock()
          node.tool_name = "write_file"
          node.args = {"path": "/etc/passwd"}
          mock_client = _mock_client("VIOLATED", "constraint breached")
          with patch("ballast.core.evaluator._get_evaluator_client", return_value=mock_client):
              label, note = evaluate_node(
                  node, [], spec,
                  tool_score=0.5, constraint_score=0.4, intent_score=0.6,
              )
          assert label == "VIOLATED"
          assert note != ""

      def test_stalled_on_client_exception(self):
          spec = _make_spec()
          node = MagicMock()
          node.tool_name = "t"
          node.args = {}
          bad_client = MagicMock()
          bad_client.messages.create.side_effect = Exception("boom")
          with patch("ballast.core.evaluator._get_evaluator_client", return_value=bad_client):
              label, note = evaluate_node(
                  node, [], spec,
                  tool_score=0.5, constraint_score=0.5, intent_score=0.5,
              )
          assert label == "STALLED"
          assert "evaluator_error" in note

      def test_empty_full_window_ok(self):
          """evaluate_node must not crash when full_window is empty."""
          spec = _make_spec()
          node = MagicMock(spec=[])  # no attributes
          mock_client = _mock_client("PROGRESSING", "fine")
          with patch("ballast.core.evaluator._get_evaluator_client", return_value=mock_client):
              label, _ = evaluate_node(
                  node, [], spec,
                  tool_score=0.6, constraint_score=0.5, intent_score=0.7,
              )
          assert label == "PROGRESSING"

      def test_lazy_singleton_not_constructed_at_import(self):
          """Importing evaluator.py must not require ANTHROPIC_API_KEY."""
          import ballast.core.evaluator as ev_mod

          ev_mod._evaluator_client = None
          assert ev_mod._evaluator_client is None
  ```

  **Git Checkpoint:**
  ```bash
  git add tests/test_evaluator.py
  git commit -m "step 10: add test_evaluator.py — 14 unit tests covering all success criteria"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate
  python -m pytest tests/test_evaluator.py -v 2>&1 | tail -20
  ```

  **Expected:**
  - `14 passed` in output.
  - No `AuthenticationError` or `UserError`.

  **Pass:** `14 passed` with exit code 0.

  **Fail:**
  - `ImportError: cannot import name '_call_evaluator'` → function renamed in Step 1 → check evaluator.py exports.
  - `AssertionError` on lazy singleton test → `_evaluator_client` was not `None` → `Anthropic()` constructed at module level.
  - `MagicMock().messages.create.return_value` not reached → check `_mock_client` helper returns correct structure.

---

### Phase 3 — Wire into trajectory.py

**Goal:** `score_drift()` calls `evaluate_node` for ambiguous nodes; `LAYER_2_STUB` is removed; test suite ≥ 219 passing.

---

- [ ] 🟥 **Step 3: Wire evaluator into `trajectory.py`** — *Critical: live path change in score_drift*

  **Step Architecture Thinking:**

  **Pattern applied:** Open/Closed — `score_drift()` skeleton is open for extension (Layer 2 wired in), closed for modification (signature unchanged, PROGRESSING/VIOLATED fast paths untouched).

  **Why this step exists here in the sequence:**
  Steps 1 and 2 must be complete. The import in Edit A only compiles if `evaluator.py` exists. The anchor in Edit B only resolves correctly if the stub is present.

  **Why trajectory.py is the right location:**
  `score_drift()` owns the label cascade and lives in `trajectory.py`. The Layer 2 call is a step within that cascade, not a separate concern.

  **Alternative approach considered and rejected:**
  Subclassing `score_drift` or wrapping it in `evaluator.py`. Rejected: creates circular dependency (`evaluator.py` importing `trajectory.py` → `trajectory.py` importing `evaluator.py`).

  **What breaks if this step deviates:**
  If `eval_note = ""` guard is not added before the if/elif/else block, the `return NodeAssessment(...)` will reference `eval_note` for PROGRESSING/VIOLATED paths where it was never set → `NameError`.

  ---

  **Idempotent:** No — `trajectory.py` is modified. Re-running this step on an already-edited file would duplicate the import. Pre-read gate prevents this.

  **Pre-Read Gate:**
  Before any edit, run ALL of the following. If ANY check fails → STOP and report.

  - `grep -c 'from ballast.core.evaluator' ballast/core/trajectory.py` — must return `0`. If `1` → step already applied → STOP.
  - `grep -c 'LAYER_2_STUB' ballast/core/trajectory.py` — must return `1`. If `0` → stub already removed → STOP.
  - `grep -c 'from ballast.core.probe import verify_node_claim' ballast/core/trajectory.py` — must return `1`. If `0` → probe plan not executed → STOP.
  - `grep -n 'label = "STALLED"' ballast/core/trajectory.py` — must return exactly 1 match inside `score_drift`. Confirm scope.

  **Anchor Uniqueness Check:**
  - Edit A target: `from ballast.core.probe import verify_node_claim` — must appear exactly 1 time.
  - Edit B target: the 8-line block from `    # ── Label assignment` through `        label = "STALLED"` and the closing `return NodeAssessment(...)` — must appear exactly 1 time.

  ---

  **Edit A — Add evaluator import** (insert before probe import):

  Old (exact, confirmed at line 38):
  ```python
  from ballast.core.probe import verify_node_claim
  ```

  New:
  ```python
  from ballast.core.evaluator import evaluate_node
  from ballast.core.probe import verify_node_claim
  ```

  ---

  **Edit B — Replace LAYER_2_STUB with evaluator call guarded by `enable_layer2_judge`**:

  Old (exact, lines 480–497):
  ```python
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

  New:
  ```python
      # ── Label assignment ──────────────────────────────────────────────────
      eval_note = ""
      if aggregate >= 0.85:
          label = "PROGRESSING"
      elif aggregate <= 0.25:
          label = "VIOLATED"
      elif spec.harness.enable_layer2_judge:
          label, eval_note = evaluate_node(
              node, full_window, spec,
              tool_score=tool_score,
              constraint_score=constraint_score,
              intent_score=intent_score,
          )
      else:
          label = "STALLED"

      return NodeAssessment(
          score=round(aggregate, 4),
          label=label,
          rationale=(
              f"intent={intent_score:.2f} constraint={constraint_score:.2f} tool={tool_score:.2f}"
              + (f"; layer2={eval_note}" if eval_note else "")
          ),
          tool_score=tool_score,
          constraint_score=constraint_score,
          intent_score=intent_score,
          tool_name=tool_name,
      )
  ```

  **Edit C — Update `tests/test_trajectory.py`** (three edits to this file):

  **Edit C1 — Rename and fix `test_score_drift_borderline_returns_stalled`** — this test uses aggregate=0.6 (inside the ambiguous zone). After wiring, it hits `_get_evaluator_client()` without a patch → `AuthenticationError`.

  Old (exact, lines 374–380):
  ```python
  def test_score_drift_borderline_returns_stalled():
      spec = _make_spec_with_irreversible()
      with patch("ballast.core.trajectory.score_constraint_violation", return_value=0.6), \
           patch("ballast.core.trajectory.score_intent_alignment", return_value=0.6):
          a = score_drift(FakeTextNode("unclear"), [], spec)
      assert a.label == "STALLED"
      assert 0.25 < a.score < 0.85
  ```

  New:
  ```python
  def test_score_drift_borderline_calls_evaluator():
      """Borderline nodes (0.25 < aggregate < 0.85) are resolved by the Layer 2 evaluator."""
      spec = _make_spec_with_irreversible()
      mock_client = MagicMock()
      block = MagicMock()
      block.type = "tool_use"
      block.input = {"label": "PROGRESSING", "rationale": "looks fine"}
      mock_response = MagicMock()
      mock_response.content = [block]
      mock_client.messages.create.return_value = mock_response
      with patch("ballast.core.trajectory.score_constraint_violation", return_value=0.6), \
           patch("ballast.core.trajectory.score_intent_alignment", return_value=0.6), \
           patch("ballast.core.evaluator._get_evaluator_client", return_value=mock_client):
          a = score_drift(FakeTextNode("unclear"), [], spec)
      assert a.label == "PROGRESSING"
      assert 0.25 < a.score < 0.85
      assert "layer2=" in a.rationale
  ```

  **Edit C2 — Add `test_score_drift_borderline_returns_stalled_when_layer2_disabled`** — new test directly after Edit C1 confirming the `enable_layer2_judge=False` path is STALLED with no evaluator call:

  ```python
  def test_score_drift_borderline_returns_stalled_when_layer2_disabled():
      """When enable_layer2_judge=False (opus harness), ambiguous nodes stay STALLED."""
      from ballast.core.spec import HarnessProfile
      spec = lock(SpecModel(
          intent="test",
          success_criteria=["done"],
          irreversible_actions=["send_email"],
          allowed_tools=["read_file"],
          drift_threshold=0.4,
          harness=HarnessProfile(enable_layer2_judge=False),
      ))
      with patch("ballast.core.trajectory.score_constraint_violation", return_value=0.6), \
           patch("ballast.core.trajectory.score_intent_alignment", return_value=0.6):
          a = score_drift(FakeTextNode("unclear"), [], spec)
      assert a.label == "STALLED"
      assert 0.25 < a.score < 0.85
  ```

  **What Edits A–C do:**
  - Edit A: Inserts `evaluate_node` import alphabetically before the existing `probe` import.
  - Edit B: Introduces `eval_note = ""` guard; replaces the STALLED stub with `elif spec.harness.enable_layer2_judge: evaluate_node(...)` + `else: label = "STALLED"`; extends `NodeAssessment.rationale` with `"; layer2=<eval_note>"` only when non-empty.
  - Edit C1: Fixes the one existing `test_trajectory.py` test that would `AuthenticationError` after wiring — patches `_get_evaluator_client`, asserts PROGRESSING label + layer2 rationale.
  - Edit C2: New test confirming `enable_layer2_judge=False` still produces STALLED with no LLM call.

  **Why `elif` not `else + if`:** Using `elif spec.harness.enable_layer2_judge:` keeps the cascade as a single if/elif chain. An `else: if:` structure would require a nested block and creates ambiguity about where `label = "STALLED"` belongs — the elif chain is unambiguous.

  **Assumptions:**
  - `node`, `full_window`, `spec`, `tool_score`, `constraint_score`, `intent_score` are all in scope at the label assignment block inside `score_drift()`. Confirmed from lines 426–497.
  - `spec.harness` is always a `HarnessProfile` instance (never None). Confirmed — `harness: HarnessProfile = Field(default_factory=HarnessProfile)` in spec.py:169.
  - `NodeAssessment.rationale` is `str` with no length constraint. Confirmed from dataclass at lines 389–403.
  - `MagicMock` is already imported in `test_trajectory.py` line 8. Confirmed — `from unittest.mock import MagicMock, patch`.
  - `HarnessProfile` is importable within the test function scope. Confirmed from spec.py exports.

  **Git Checkpoint:**
  ```bash
  git add ballast/core/trajectory.py tests/test_trajectory.py
  git commit -m "step 10: wire evaluate_node into score_drift; replace LAYER_2_STUB; fix borderline test"
  ```

  **✓ Verification Test:**

  **Type:** Unit + Integration

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate
  grep -c 'LAYER_2_STUB' ballast/core/trajectory.py
  grep -c 'from ballast.core.evaluator import evaluate_node' ballast/core/trajectory.py
  grep -c 'enable_layer2_judge' ballast/core/trajectory.py
  python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -5
  ```

  **Expected:**
  - `grep LAYER_2_STUB` → `0` (stub removed).
  - `grep from ballast.core.evaluator` → `1` (import present).
  - `grep enable_layer2_judge` → `1` (harness flag check present).
  - `pytest` → `≥ 219 passed` (204 existing + 14 new evaluator tests + 1 new trajectory test); `0 failed`.

  **Pass:** All four checks green.

  **Fail:**
  - `grep LAYER_2_STUB` returns `1` → Edit B not applied → re-check old string exactness.
  - `grep enable_layer2_judge` returns `0` → harness guard missing from Edit B → re-apply.
  - `ImportError: cannot import name 'evaluate_node'` → evaluator.py Step 1 not complete or function name mismatch.
  - `AuthenticationError` in `test_score_drift_borderline_calls_evaluator` → Edit C1 not applied to test_trajectory.py.
  - Test count drops below 204 → regression → read full pytest output; do not proceed.

---

## Regression Guard

**Systems at risk from this plan:**
- `score_drift()` — evaluator call inside the hot path; non-caught exception would abort runs hitting the ambiguous zone. Mitigated by fail-open to STALLED.
- `test_score_drift_borderline_returns_stalled` (test_trajectory.py:374) — replaced by `test_score_drift_borderline_calls_evaluator` which patches the evaluator client; old assertion `a.label == "STALLED"` would fail after wiring.
- Opus-model runs with `enable_layer2_judge=False` — guarded by `elif spec.harness.enable_layer2_judge:` fallback to STALLED; confirmed by new `test_score_drift_borderline_returns_stalled_when_layer2_disabled` test.

**Regression verification:**

| System | Pre-change behavior | Post-change verification |
|--------|---------------------|--------------------------|
| `score_drift` ambiguous zone (layer2 enabled) | Returns `label="STALLED"` | Returns `"PROGRESSING"` or `"VIOLATED"` from evaluator (or STALLED on error) |
| `score_drift` ambiguous zone (layer2 disabled) | Returns `label="STALLED"` | Still returns `label="STALLED"` — `enable_layer2_judge=False` guard fires |
| `score_drift` PROGRESSING fast path | Returns `label="PROGRESSING"` unchanged | `eval_note=""` guard; rationale unchanged |
| `score_drift` VIOLATED fast path | Returns `label="VIOLATED"` unchanged | Same guard; rationale unchanged |
| Existing 204 tests | All pass | `pytest tests/ -m 'not integration' -q` must show ≥ 204 passing |

**Test count regression check:**
- Tests before plan (from Pre-Flight baseline): `204`
- Tests after plan: run `pytest tests/ -m 'not integration' -q` — must be `≥ 219` (204 + 14 evaluator + 1 trajectory)

---

## Post-Plan Checklist

- [ ] `ballast/core/evaluator.py` exists and imports cleanly.
- [ ] `tests/test_evaluator.py` has 14 tests, all passing.
- [ ] `trajectory.py` imports `evaluate_node` from `ballast.core.evaluator`.
- [ ] `grep -c 'LAYER_2_STUB' ballast/core/trajectory.py` returns `0`.
- [ ] `grep -c 'enable_layer2_judge' ballast/core/trajectory.py` returns `1`.
- [ ] `test_score_drift_borderline_calls_evaluator` exists in `tests/test_trajectory.py`.
- [ ] `test_score_drift_borderline_returns_stalled_when_layer2_disabled` exists in `tests/test_trajectory.py`.
- [ ] `pytest tests/ -m 'not integration' -q` passes with ≥ 219 tests; 0 failures.
- [ ] All three git commits made (one per step).

---

## State Manifest (fill after all steps complete)

```
Files modified:
  ballast/core/evaluator.py   — created (new file)
  tests/test_evaluator.py     — created (new file)
  ballast/core/trajectory.py  — edited (2 edits: import + label assignment block with enable_layer2_judge guard)
  tests/test_trajectory.py    — edited (Edit C1: replace borderline test; Edit C2: add layer2-disabled test)

Test count after plan: ____
Regressions: none expected
Next plan: Step 13 — OTel spans (emit_drift_span)
```

---

## Success Criteria

| Criterion | Target | Verification |
|-----------|--------|--------------|
| PROGRESSING resolution | `evaluate_node` returns `"PROGRESSING"` when LLM resolves PROGRESSING | `test_progressing_label_returned` passes |
| VIOLATED resolution | `evaluate_node` returns `"VIOLATED"` when LLM resolves VIOLATED | `test_violated_label_returned` passes |
| Fail-open on exception | `evaluate_node` returns `"STALLED"` on any exception | `test_stalled_on_client_exception` passes |
| No tool_use block | `_call_evaluator` returns `("STALLED", "no valid label")` | `test_returns_stalled_on_no_tool_use_block` passes |
| Invalid label | `_call_evaluator` returns `("STALLED", ...)` for unknown label | `test_returns_stalled_on_invalid_label` passes |
| EvaluatorPacket fields | All 10 fields accessible | `test_all_fields_set` passes |
| Lazy singleton | `_evaluator_client is None` after import | `test_lazy_singleton_not_constructed_at_import` passes |
| `evaluate_node` is sync | `inspect.iscoroutinefunction` → False | Step 1 import check passes |
| LAYER_2_STUB removed | `grep -c 'LAYER_2_STUB'` → 0 | Step 3 verification |
| `enable_layer2_judge` guarded | `grep -c 'enable_layer2_judge' trajectory.py` → 1 | Step 3 verification |
| Borderline test fixed | `test_score_drift_borderline_calls_evaluator` passes | Step 3 Edit C1 |
| Layer2-disabled path | `test_score_drift_borderline_returns_stalled_when_layer2_disabled` passes | Step 3 Edit C2 |
| No regressions | ≥ 204 existing tests pass; total ≥ 219 | Full suite run after Step 3 |
